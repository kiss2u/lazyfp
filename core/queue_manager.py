import asyncio
import os
import time
import uuid
import logging
import json
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class UploadTask:
    id: str
    filename: str
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    result: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status.value,
            "progress": self.progress,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "result": self.result,
        }


class UploadQueue:
    def __init__(self, max_size: int = 1000, worker_count: int = 2):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._tasks: dict[str, UploadTask] = {}
        self._worker_count = worker_count
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._processor = None
        self._sse_queues: list[asyncio.Queue] = []

    def set_processor(self, processor):
        self._processor = processor

    async def start(self):
        if self._running:
            return
        self._running = True
        for i in range(self._worker_count):
            worker = asyncio.create_task(self._worker_loop(i))
            self._workers.append(worker)
        logger.info("UploadQueue started with %d workers.", self._worker_count)

    async def stop(self):
        self._running = False
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        for q in self._sse_queues:
            await q.put(None)
        self._sse_queues.clear()
        logger.info("UploadQueue stopped.")

    async def enqueue(self, filename: str) -> str:
        task_id = str(uuid.uuid4())[:8]
        task = UploadTask(id=task_id, filename=filename)
        self._tasks[task_id] = task
        await self._queue.put(task_id)
        await self._broadcast({"type": "task_added", "task": task.to_dict()})
        return task_id

    async def _broadcast(self, event: dict):
        event["ts"] = time.time()
        msg = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        dead = []
        for q in self._sse_queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._sse_queues.remove(q)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._sse_queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._sse_queues:
            self._sse_queues.remove(q)

    async def _worker_loop(self, worker_id: int):
        logger.info("Worker %d started.", worker_id)
        while self._running:
            try:
                task_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            task = self._tasks.get(task_id)
            if not task:
                continue

            task.status = TaskStatus.PROCESSING
            task.progress = 10
            await self._broadcast({"type": "task_update", "task": task.to_dict()})

            await asyncio.sleep(0.15)
            task.progress = 30
            await self._broadcast({"type": "task_update", "task": task.to_dict()})

            try:
                if self._processor:
                    result = await self._processor(task.filename)
                    task.result = result
                    task.progress = 80
                    await self._broadcast({"type": "task_update", "task": task.to_dict()})

                    await asyncio.sleep(0.1)
                    task.progress = 100
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = time.time()
                    await self._broadcast({"type": "task_update", "task": task.to_dict()})
                    logger.info("Worker %d completed: %s", worker_id, task.filename)
                else:
                    raise RuntimeError("No processor set")
            except Exception as e:
                task.error = str(e)
                task.status = TaskStatus.FAILED
                task.progress = 100
                task.completed_at = time.time()
                await self._broadcast({"type": "task_update", "task": task.to_dict()})
                logger.error("Worker %d failed on %s: %s", worker_id, task.filename, e)
            finally:
                self._queue.task_done()

    def get_task(self, task_id: str) -> Optional[dict]:
        task = self._tasks.get(task_id)
        return task.to_dict() if task else None

    def get_all_tasks(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks.values()]

    def get_pending_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.PENDING)

    def get_processing_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.PROCESSING)

    def get_completed_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.COMPLETED)

    def get_failed_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.FAILED)

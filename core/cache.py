import json
import os
import threading
from typing import Optional


class InvoiceCache:
    _instance = None
    _lock = threading.Lock()

    def __init__(self, cache_file: str):
        self._cache_file = cache_file
        self._data: dict = {}
        self._file_lock = threading.Lock()
        self._load()

    @classmethod
    def get_instance(cls, cache_file: str = "invoice_cache.json") -> "InvoiceCache":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(cache_file)
        return cls._instance

    def _load(self):
        if os.path.exists(self._cache_file):
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = {}

    def get(self, filename: str, mtime: float, size: int) -> Optional[dict]:
        entry = self._data.get(filename)
        if entry and entry.get("mtime") == mtime and entry.get("size") == size:
            return entry.get("data")
        return None

    def put(self, filename: str, mtime: float, size: int, data: dict):
        self._data[filename] = {
            "mtime": mtime,
            "size": size,
            "data": data,
        }

    def remove(self, filename: str):
        self._data.pop(filename, None)

    def invalidate(self, filename: Optional[str] = None):
        if filename:
            self._data.pop(filename, None)
        else:
            self._data.clear()

    def save(self):
        with self._file_lock:
            tmp = self._cache_file + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._cache_file)
            except IOError:
                if os.path.exists(tmp):
                    os.remove(tmp)

    def cleanup(self, current_files: set):
        removed = False
        for key in list(self._data.keys()):
            if key not in current_files:
                del self._data[key]
                removed = True
        if removed:
            self.save()

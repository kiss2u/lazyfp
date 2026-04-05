import os
import io
import re
import json
import uuid
import shutil
import zipfile
import logging
import aiofiles
import asyncio
import yaml
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote

import pandas as pd
from openpyxl import Workbook
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from main import process_invoices, scan_directory, INPUT_DIR, OUTPUT_FILE, get_quarter
from main import _get_orchestrator, _get_cache, _rule_engine
from core.queue_manager import UploadQueue, TaskStatus

with open("config/settings.yaml", "r", encoding="utf-8") as f:
    _settings = yaml.safe_load(f)

UPLOAD_MAX_SIZE = _settings["upload"]["max_file_size"]
ALLOWED_EXTENSIONS = set(_settings["upload"]["allowed_extensions"])
ALLOWED_MAGIC = _settings["upload"]["allowed_magic"].encode("ascii")

app = FastAPI(title="LazyFP WebUI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings["app"]["cors_origins"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for d in [INPUT_DIR, "static", os.path.join(INPUT_DIR, "dump"), os.path.join(INPUT_DIR, "organized")]:
    os.makedirs(d, exist_ok=True)

_upload_queue = UploadQueue(
    max_size=_settings["queue"]["max_queue_size"],
    worker_count=_settings["queue"]["worker_count"],
)

_organize_lock = asyncio.Lock()


async def _process_uploaded(filename: str) -> dict:
    file_path = os.path.join(INPUT_DIR, filename)
    orchestrator = _get_orchestrator()
    result = orchestrator.extract(file_path)
    cache = _get_cache()
    if os.path.exists(file_path):
        stat = os.stat(file_path)
        cache.put(filename, stat.st_mtime, stat.st_size, result)
        cache.save()
    return result


@app.on_event("startup")
async def startup():
    await _upload_queue.start()
    _upload_queue.set_processor(_process_uploaded)
    import asyncio
    asyncio.create_task(_warmup_cache())


async def _warmup_cache():
    import asyncio
    await asyncio.sleep(0.5)
    try:
        from main import scan_directory
        scan_directory(INPUT_DIR)
        logging.info("Cache warmup complete.")
    except Exception as e:
        logging.error("Cache warmup failed: %s", e)


@app.on_event("shutdown")
async def shutdown():
    await _upload_queue.stop()


def _invalidate_cache():
    global _invoices_cache
    _invoices_cache = {"data": None, "mtime": 0}


def _validate_pdf(file_bytes: bytes, filename: str) -> Optional[str]:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return f"Only PDF files allowed. Got: {ext}"
    if len(file_bytes) > UPLOAD_MAX_SIZE:
        return f"File too large: {len(file_bytes) / 1024 / 1024:.1f}MB > 10MB limit"
    if not file_bytes.startswith(b"%PDF-"):
        return "Invalid PDF file (magic bytes mismatch)"
    return None


@app.get("/")
async def read_root():
    return FileResponse("static/index.html")


_invoices_cache = {"data": None, "mtime": 0}


@app.get("/api/invoices")
async def get_invoices(page: Optional[int] = None, limit: Optional[int] = None):
    try:
        global _invoices_cache
        import os
        current_mtime = os.path.getmtime(INPUT_DIR) if os.path.exists(INPUT_DIR) else 0

        if _invoices_cache["data"] is None or current_mtime > _invoices_cache["mtime"]:
            from main import scan_directory, get_quarter
            data_list = scan_directory(INPUT_DIR)
            records = []
            for item in data_list:
                clean = {}
                for k, v in item.items():
                    if isinstance(v, float):
                        import math
                        if math.isnan(v) or math.isinf(v):
                            clean[k] = None
                        else:
                            clean[k] = v
                    elif isinstance(v, str) and v == "":
                        clean[k] = None
                    else:
                        clean[k] = v
                # Add quarter
                clean["quarter"] = get_quarter(str(clean.get("date") or ""))
                records.append(clean)
            _invoices_cache = {"data": records, "mtime": current_mtime}
        else:
            records = _invoices_cache["data"]

        total = len(records)
        if page is not None and limit is not None:
            start = (page - 1) * limit
            end = start + limit
            records = records[start:end]
            return {"data": records, "total": total, "page": page, "limit": limit}

        return {"data": records, "total": total, "page": 1, "limit": 0}
    except Exception as e:
        logging.error("Error fetching invoices: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scan")
async def scan_invoices():
    return await get_invoices()


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    uploaded_count = 0
    errors = []
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            continue
        content = await file.read()
        error = _validate_pdf(content, file.filename)
        if error:
            errors.append(f"{file.filename}: {error}")
            continue
        file_path = os.path.join(INPUT_DIR, file.filename)
        if os.path.exists(file_path):
            base, ext = os.path.splitext(file.filename)
            file_path = os.path.join(INPUT_DIR, f"{base}_{uuid.uuid4().hex[:6]}{ext}")
        try:
            async with aiofiles.open(file_path, 'wb') as out_file:
                await out_file.write(content)
            uploaded_count += 1
            _get_cache().invalidate(file.filename if file.filename == os.path.basename(file_path) else None)
        except Exception as e:
            logging.error("Failed to upload %s: %s", file.filename, e)
            errors.append(f"{file.filename}: {e}")
        _invalidate_cache()
    return {"message": f"Successfully uploaded {uploaded_count} files", "errors": errors}


@app.post("/api/upload/queued")
async def upload_files_queued(files: List[UploadFile] = File(...)):
    task_ids = []
    errors = []
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            continue
        content = await file.read()
        error = _validate_pdf(content, file.filename)
        if error:
            errors.append(f"{file.filename}: {error}")
            continue
        save_name = file.filename
        file_path = os.path.join(INPUT_DIR, save_name)
        if os.path.exists(file_path):
            base, ext = os.path.splitext(file.filename)
            save_name = f"{base}_{uuid.uuid4().hex[:6]}{ext}"
            file_path = os.path.join(INPUT_DIR, save_name)
        try:
            async with aiofiles.open(file_path, 'wb') as out_file:
                await out_file.write(content)
            task_id = await _upload_queue.enqueue(save_name)
            task_ids.append({"filename": save_name, "task_id": task_id})
        except Exception as e:
            logging.error("Failed to upload %s: %s", file.filename, e)
            errors.append(f"{file.filename}: {e}")
    return {"tasks": task_ids, "errors": errors}


@app.get("/api/upload/status")
async def get_upload_status():
    return {
        "tasks": _upload_queue.get_all_tasks(),
        "summary": {
            "pending": _upload_queue.get_pending_count(),
            "processing": _upload_queue.get_processing_count(),
            "completed": _upload_queue.get_completed_count(),
            "failed": _upload_queue.get_failed_count(),
        }
    }


@app.get("/api/upload/status/{task_id}")
async def get_upload_task_status(task_id: str):
    task = _upload_queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/api/upload/sse")
async def upload_sse():
    from starlette.responses import StreamingResponse

    async def event_stream():
        q = _upload_queue.subscribe()
        try:
            # Send current state first
            yield f"data: {json.dumps({'type': 'init', 'tasks': _upload_queue.get_all_tasks(), 'summary': {'pending': _upload_queue.get_pending_count(), 'processing': _upload_queue.get_processing_count(), 'completed': _upload_queue.get_completed_count(), 'failed': _upload_queue.get_failed_count()}}, ensure_ascii=False)}\n\n"
            while True:
                msg = await q.get()
                if msg is None:
                    break
                yield msg
        except asyncio.CancelledError:
            pass
        finally:
            _upload_queue.unsubscribe(q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/unrecognized")
async def get_unrecognized():
    from main import scan_directory
    data_list = scan_directory(INPUT_DIR)
    unrecognized = [d for d in data_list if d.get("maybe_not_invoice")]
    return {"data": unrecognized, "total": len(unrecognized)}


@app.delete("/api/invoices/{filename:path}")
async def delete_invoice(filename: str):
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files can be deleted")
    safe_name = os.path.basename(filename)
    path = os.path.join(INPUT_DIR, safe_name)
    if os.path.exists(path):
        try:
            os.remove(path)
            _get_cache().invalidate(safe_name)
            _invalidate_cache()
            return {"message": f"Deleted {safe_name}"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        raise HTTPException(status_code=404, detail="File not found")


@app.post("/api/deduplicate")
async def deduplicate_invoices():
    async with _organize_lock:
        df = process_invoices(INPUT_DIR)
        if df.empty:
            return {"message": "No invoices to process.", "moved_count": 0}

        dump_dir = os.path.join(INPUT_DIR, "dump")
        os.makedirs(dump_dir, exist_ok=True)

        moved_count = 0
        duplicates = df[df["count"] > 1]

        for _, row in duplicates.iterrows():
            filenames = row["filename"].split(", ")
            to_move = filenames[1:]
            for fname in to_move:
                src = os.path.join(INPUT_DIR, fname)
                dst = os.path.join(dump_dir, fname)
                if os.path.exists(src):
                    try:
                        if os.path.exists(dst):
                            base, ext = os.path.splitext(fname)
                            dst = os.path.join(dump_dir, f"{base}_{int(datetime.now().timestamp())}{ext}")
                        shutil.move(src, dst)
                        _get_cache().invalidate(fname)
                        moved_count += 1
                    except Exception as e:
                        logging.error("Failed to move %s: %s", fname, e)

    _get_cache().save()
    _invalidate_cache()
    return {"message": f"Deduplication complete. Moved {moved_count} files to 'dump/'.", "moved_count": moved_count}


@app.post("/api/organize")
async def organize_invoices(move: bool = False):
    async with _organize_lock:
        data_list = scan_directory(INPUT_DIR)
        organized_base = os.path.join(INPUT_DIR, "organized")
        os.makedirs(organized_base, exist_ok=True)

        count = 0
        errors = 0

        for item in data_list:
            try:
                filename = item.get("filename")
                src_path = os.path.join(INPUT_DIR, filename)
                if not os.path.exists(src_path):
                    continue

                purchaser = item.get("purchaser") or "Unknown Purchaser"
                date_str = item.get("date")
                quarter = get_quarter(str(date_str))
                seller = item.get("seller") or "Unknown Seller"
                invoice_no = item.get("invoice_no") or "000000"
                amount = item.get("total_amount")
                amount = f"{float(amount):.2f}" if amount is not None else "0.00"

                safe_purchaser = re.sub(r'[\\/*?:"<>|]', "", purchaser).strip()
                target_dir = os.path.join(organized_base, safe_purchaser, quarter)
                os.makedirs(target_dir, exist_ok=True)

                inv_str = str(invoice_no)
                inv_suffix = inv_str[-6:] if len(inv_str) >= 6 else inv_str.zfill(6)
                safe_seller = re.sub(r'[\\/*?:"<>|]', "", seller).strip()
                new_name = f"{inv_suffix}-{safe_seller}-{amount}.pdf"
                dst_path = os.path.join(target_dir, new_name)

                if os.path.exists(dst_path):
                    logging.info("Skipping duplicate organized file: %s (src: %s)", new_name, filename)
                    continue

                if move:
                    shutil.move(src_path, dst_path)
                else:
                    shutil.copy2(src_path, dst_path)
                count += 1
            except Exception as e:
                logging.error("Error organizing %s: %s", item, e)
                errors += 1

        return {"message": f"Organized {count} files.", "errors": errors}


@app.get("/api/export/{purchaser}/{quarter}")
async def export_quarter_zip(purchaser: str, quarter: str):
    safe_purchaser = re.sub(r'[\\/*?:"<>|]', "", purchaser).strip()
    safe_quarter = re.sub(r'[\\/*?:"<>|]', "", quarter).strip()
    target_dir = os.path.join(INPUT_DIR, "organized", safe_purchaser, safe_quarter)

    if not os.path.exists(target_dir):
        raise HTTPException(status_code=400, detail="Folder not found. Please click 'Organize' first.")

    mem_zip = io.BytesIO()
    files_to_zip = [f for f in os.listdir(target_dir) if f.lower().endswith(".pdf")]
    seen_names = set()
    unique_files = []
    for fname in files_to_zip:
        if fname not in seen_names:
            seen_names.add(fname)
            unique_files.append(fname)

    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in unique_files:
            fpath = os.path.join(target_dir, fname)
            zf.write(fpath, arcname=fname)

    raw_data = scan_directory(INPUT_DIR)
    sheet_rows = []
    total_amount = 0.0

    for item in raw_data:
        p = item.get("purchaser") or "Unknown"
        d = item.get("date")
        q = get_quarter(str(d))
        if p == purchaser and q == quarter:
            amt = float(item.get("total_amount") or 0)
            total_amount += amt
            sheet_rows.append({
                "Date": d,
                "Invoice No": item.get("invoice_no"),
                "Seller": item.get("seller"),
                "Amount": amt,
                "Filename": item.get("filename")
            })

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Date", "Invoice No", "Seller", "Amount", "Original Filename"])
    for row in sheet_rows:
        ws.append([row["Date"], row["Invoice No"], row["Seller"], row["Amount"], row["Filename"]])
    ws.append(["", "", "Total", total_amount, ""])

    excel_io = io.BytesIO()
    wb.save(excel_io)
    excel_io.seek(0)

    with zipfile.ZipFile(mem_zip, mode="a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{safe_quarter}_Summary.xlsx", excel_io.getvalue())

    mem_zip.seek(0)
    zip_filename = f"{safe_purchaser}-{safe_quarter}-{total_amount:.2f}.zip"
    encoded_name = quote(zip_filename)

    return StreamingResponse(
        mem_zip,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"}
    )


@app.get("/api/invoices/{filename:path}/debug")
async def get_invoice_debug(filename: str):
    safe_name = os.path.basename(filename)
    file_path = os.path.join(INPUT_DIR, safe_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    orchestrator = _get_orchestrator()
    result = orchestrator.extract(file_path)
    return result


@app.post("/api/debug/test-rule")
async def test_rule(payload: dict):
    pattern = payload.get("pattern", "")
    text = payload.get("text", "")
    flags = payload.get("flags", 0)
    try:
        compiled = re.compile(pattern, flags)
        matches = []
        for m in compiled.finditer(text):
            matches.append({
                "full_match": m.group(0),
                "groups": list(m.groups()),
                "start": m.start(),
                "end": m.end(),
            })
        return {"valid": True, "matches": matches}
    except re.error as e:
        return {"valid": False, "error": str(e)}


@app.post("/api/debug/add-rule")
async def add_rule(payload: dict):
    field = payload.get("field", "")
    pattern = payload.get("pattern", "")
    desc = payload.get("desc", "")
    if field not in ("invoice_no", "date", "amount", "purchaser", "seller"):
        raise HTTPException(status_code=400, detail="Invalid field")
    if not pattern:
        raise HTTPException(status_code=400, detail="Pattern required")
    rule = {
        "pattern": pattern,
        "flags": payload.get("flags", 0),
        "desc": desc or "User-added rule",
        "added_at": datetime.now().isoformat(),
        "source": "user",
    }
    _rule_engine.add_rule(field, rule)
    return {"message": "Rule added successfully"}


app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

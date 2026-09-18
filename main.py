import os
import time
import asyncio
import urllib.parse
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, status, Request, Response
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import (
    BASE_DIR,
    UPLOAD_DIR,
    MAX_FILE_SIZE,
    CHANNEL_ID,
    TODO_CHANNEL_ID,
    SOFTWARE_CHANNEL_ID,
    is_telegram_configured,
)
from database import (
    init_db,
    add_file,
    get_files,
    get_file,
    delete_file,
    detect_category,
    format_size,
    add_todo,
    get_todos,
    get_todo,
    update_todo_status,
    delete_todo,
    clear_completed_todos,
    add_note,
    get_notes,
    delete_note,
)
from telegram_client import storage_client

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await storage_client.initialize()
    # Auto-import any existing channel files on startup
    try:
        await storage_client.sync_channel_messages()
    except Exception as e:
        pass
    yield
    await storage_client.close()

app = FastAPI(
    title="Telegram Cloud & Productivity Hub",
    description="Multi-tab cloud drive and task hub backed by Telegram MTProto channels.",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Disposition", "Content-Type", "Accept-Ranges"],
)

# Authentication Middleware: Protects API with password "Allow" (Softwares tab is public)
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Public endpoints
    if not path.startswith("/api/") or path in ["/api/auth/verify", "/api/status", "/api/prefetch"] or path.startswith("/api/upload-progress"):
        return await call_next(request)

    # Public Softwares category: no password required to list, upload, or download software
    if path == "/api/files" and request.method == "GET" and request.query_params.get("category") == "software":
        return await call_next(request)

    if path == "/api/upload" and request.method == "POST" and request.query_params.get("category") == "software":
        return await call_next(request)

    if path.startswith("/api/download/") and request.method == "GET":
        file_id = path.split("/api/download/")[-1].split("?")[0]
        try:
            file_rec = await get_file(file_id)
            if file_rec and file_rec.get("category") == "software":
                return await call_next(request)
        except Exception:
            pass

    cookie_auth = request.cookies.get("tg_auth", "")
    header_auth = request.headers.get("X-Auth-Token", "")
    query_auth = request.query_params.get("auth", "")

    if cookie_auth.lower() == "allow" or header_auth.lower() == "allow" or query_auth.lower() == "allow":
        return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required. Please enter password 'Allow'."}
    )

# Pydantic request models
class AuthRequest(BaseModel):
    password: str

class TodoCreate(BaseModel):
    title: str

class TodoUpdate(BaseModel):
    completed: bool

class NoteCreate(BaseModel):
    content: str

# Auto-Prefetch Debouncing Mechanism
_last_prefetch_time = 0.0
_prefetch_lock = asyncio.Lock()

async def auto_prefetch(force: bool = False) -> int:
    """Debounced prefetch of latest files from Telegram channels."""
    global _last_prefetch_time
    now = time.time()
    if not force and (now - _last_prefetch_time < 5.0):
        return 0
    async with _prefetch_lock:
        if not force and (time.time() - _last_prefetch_time < 5.0):
            return 0
        _last_prefetch_time = time.time()
        try:
            imported = await storage_client.sync_channel_messages(max_ids=150)
            return imported
        except Exception:
            return 0

# ==================== AUTH & STATUS ====================

@app.post("/api/auth/verify")
async def verify_password(payload: AuthRequest, response: Response):
    """Verify password 'Allow' and issue session cookie."""
    if payload.password.strip().lower() == "allow":
        response.set_cookie(
            key="tg_auth",
            value="Allow",
            max_age=60 * 60 * 24 * 365,
            httponly=False,
            samesite="lax"
        )
        return {"success": True, "message": "Authenticated"}
    raise HTTPException(status_code=401, detail="Incorrect password. Please enter 'Allow'.")

@app.get("/api/status")
async def get_system_status():
    """Retrieve system health and triple channel status."""
    soft_resolved = bool(storage_client.software_channel_entity and storage_client.software_channel_entity != storage_client.channel_entity)
    return {
        "status": "online",
        "mode": "demo" if storage_client.is_demo else "live",
        "is_demo": storage_client.is_demo,
        "credentials_configured": is_telegram_configured(),
        "storage_channel_id": CHANNEL_ID if CHANNEL_ID != 0 else None,
        "todo_channel_id": TODO_CHANNEL_ID if TODO_CHANNEL_ID != 0 else CHANNEL_ID,
        "software_channel_id": SOFTWARE_CHANNEL_ID if SOFTWARE_CHANNEL_ID != 0 else CHANNEL_ID,
        "software_channel_resolved": soft_resolved,
        "bot_username": getattr(storage_client.bot_info, "username", None) if storage_client.bot_info else None,
    }

@app.get("/api/prefetch")
@app.post("/api/prefetch")
async def prefetch_files(force: bool = Query(False)):
    """Trigger a background prefetch of files from Telegram channels."""
    imported = await auto_prefetch(force=force)
    return {"success": True, "imported_files": imported}

@app.post("/api/sync")
async def sync_channel():
    """Scan Telegram channel and import any existing files into SQLite."""
    imported = await storage_client.sync_channel_messages()
    return {"success": True, "imported_files": imported}

# ==================== FILES, PHOTOS, VIDEOS ====================

@app.get("/api/files")
async def list_files(
    category: Optional[str] = Query(None, description="Category filter: file, photo, video, all, software"),
    q: Optional[str] = Query(None, description="Search query"),
    prefetch: bool = Query(False, description="Whether to auto-prefetch newest channel files")
):
    """Retrieve stored files filtered by category and search, with optional prefetch."""
    if prefetch:
        await auto_prefetch()
    files = await get_files(category=category, search=q)
    return {"files": files, "count": len(files)}

# Upload Progress Tracking (upload_id -> progress metrics)
upload_progress_tracker: Dict[str, Dict[str, Any]] = {}

@app.get("/api/upload-progress/{upload_id}")
async def get_upload_progress(upload_id: str):
    """Retrieve real-time transfer progress of file streaming to Telegram."""
    info = upload_progress_tracker.get(upload_id)
    if not info:
        return {"status": "not_found", "percent": 0.0, "speed_mbs": 0.0, "speed_mbps": 0.0, "eta_seconds": 0}
    return info

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    category: Optional[str] = Form(None),
    cat_query: Optional[str] = Query(None, alias="category"),
    upload_id: Optional[str] = Form(None),
    upload_id_query: Optional[str] = Query(None, alias="upload_id")
):
    """Upload a file, photo, video, or software (up to 2 GB) directly to Telegram Channel."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    chosen_cat = category or cat_query
    cat = chosen_cat if chosen_cat in ["file", "photo", "video", "software"] else detect_category(file.filename, file.content_type)
    temp_filename = f"upload_{os.urandom(8).hex()}_{file.filename}"
    temp_path = UPLOAD_DIR / temp_filename

    uid = upload_id or upload_id_query
    if uid:
        upload_progress_tracker[uid] = {
            "current": 0,
            "total": 0,
            "percent": 0.0,
            "speed_mbs": 0.0,
            "speed_mbps": 0.0,
            "eta_seconds": 0,
            "status": "uploading_to_server"
        }

    total_uploaded = 0
    try:
        with open(temp_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                total_uploaded += len(chunk)
                if total_uploaded > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds maximum 2 GB limit."
                    )
                buffer.write(chunk)

        if uid:
            upload_progress_tracker[uid]["status"] = "starting_telegram_sync"
            upload_progress_tracker[uid]["total"] = total_uploaded

        last_time = time.time()
        last_bytes = 0

        def progress_cb(current, total):
            nonlocal last_time, last_bytes
            now = time.time()
            dt = max(now - last_time, 0.001)
            bytes_diff = max(current - last_bytes, 0)
            speed = bytes_diff / dt  # bytes per sec
            last_time = now
            last_bytes = current
            effective_total = total or total_uploaded
            percent = round((current / effective_total) * 100, 1) if effective_total > 0 else 0
            speed_mbs = round(speed / (1024 * 1024), 2)
            speed_mbps = round((speed * 8) / (1024 * 1024), 2)
            remaining_bytes = max(effective_total - current, 0)
            eta_sec = round(remaining_bytes / max(speed, 1)) if speed > 10000 else 0

            if uid:
                upload_progress_tracker[uid] = {
                    "current": current,
                    "total": effective_total,
                    "percent": min(percent, 100.0),
                    "speed_mbs": speed_mbs,
                    "speed_mbps": speed_mbps,
                    "eta_seconds": eta_sec,
                    "status": "uploading_to_telegram"
                }

        # Upload to Storage Channel
        msg_id, channel_id, tg_file_id = await storage_client.upload_file(
            file_path=temp_path,
            filename=file.filename,
            category=cat,
            progress_callback=progress_cb if uid else None
        )

        # Save to SQLite
        file_record = await add_file(
            filename=file.filename,
            size=total_uploaded,
            mime_type=file.content_type or "application/octet-stream",
            telegram_message_id=msg_id,
            telegram_channel_id=channel_id,
            telegram_file_id=tg_file_id,
            is_demo=storage_client.is_demo,
            category=cat
        )

        if uid:
            upload_progress_tracker[uid] = {
                "current": total_uploaded,
                "total": total_uploaded,
                "percent": 100.0,
                "speed_mbs": 0.0,
                "speed_mbps": 0.0,
                "eta_seconds": 0,
                "status": "completed"
            }

        return file_record

    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass

@app.get("/api/download/{file_id}")
async def download_file(file_id: str):
    """Stream download file chunks directly from Telegram."""
    file_record = await get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found.")

    try:
        chunk_stream = storage_client.download_file_stream(
            telegram_message_id=file_record["telegram_message_id"],
            filename=file_record["filename"],
            telegram_channel_id=file_record.get("telegram_channel_id"),
            is_demo=file_record["is_demo"]
        )

        quoted_filename = urllib.parse.quote(file_record["filename"])
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted_filename}",
            "Content-Length": str(file_record["size"]),
            "Accept-Ranges": "bytes"
        }

        return StreamingResponse(
            chunk_stream,
            media_type=file_record["mime_type"] or "application/octet-stream",
            headers=headers
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Underlying media not found on Telegram.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Streaming error: {str(e)}")

@app.delete("/api/files/{file_id}")
async def delete_file_endpoint(file_id: str):
    """Delete file from DB and remove document message from Telegram Channel 1."""
    file_record = await get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found.")

    await storage_client.delete_message(
        channel_id=file_record["telegram_channel_id"],
        message_id=file_record["telegram_message_id"],
        is_demo=file_record["is_demo"],
        filename=file_record["filename"]
    )
    await delete_file(file_id)
    return {"success": True, "message": f"'{file_record['filename']}' deleted successfully."}

# ==================== TO-DO LIST (CHANNEL 2) ====================

@app.get("/api/todos")
async def list_todos(status: Optional[str] = Query(None, description="Status filter: active, completed, all")):
    """List todos with optional active/completed filter."""
    todos = await get_todos(status=status)
    return {"todos": todos, "count": len(todos)}

@app.post("/api/todos")
async def create_todo(payload: TodoCreate):
    """Create a new todo task, send to Telegram Channel 2, and save to DB."""
    clean_title = payload.title.strip()
    if not clean_title:
        raise HTTPException(status_code=400, detail="Task title cannot be empty.")

    msg_id, channel_id = await storage_client.send_todo_message(clean_title)
    todo = await add_todo(
        title=clean_title,
        telegram_message_id=msg_id,
        telegram_channel_id=channel_id,
        is_demo=storage_client.is_demo
    )
    return todo

@app.patch("/api/todos/{todo_id}")
async def toggle_todo(todo_id: str, payload: TodoUpdate):
    """Toggle or set completion status and update Telegram message."""
    todo = await get_todo(todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found.")

    updated = await update_todo_status(todo_id, payload.completed)
    await storage_client.update_todo_message(
        telegram_message_id=todo["telegram_message_id"],
        title=todo["title"],
        completed=payload.completed
    )
    return updated

@app.delete("/api/todos/{todo_id}")
async def delete_single_todo(todo_id: str):
    """Delete a single todo item and delete its message from Telegram."""
    todo = await get_todo(todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found.")

    await storage_client.delete_message(
        channel_id=todo["telegram_channel_id"],
        message_id=todo["telegram_message_id"],
        is_demo=todo["is_demo"]
    )
    await delete_todo(todo_id)
    return {"success": True, "message": "Task deleted."}

@app.post("/api/todos/clear-completed")
async def clear_completed():
    """Purge all completed todos from database and delete messages from Telegram."""
    completed_items = await clear_completed_todos()
    for item in completed_items:
        await storage_client.delete_message(
            channel_id=item["telegram_channel_id"],
            message_id=item["telegram_message_id"],
            is_demo=item["is_demo"]
        )
    return {"success": True, "cleared_count": len(completed_items)}

# ==================== SEND TEXT / NOTES (CHANNEL 2) ====================

@app.get("/api/notes")
async def list_notes():
    """Retrieve saved text notes."""
    notes = await get_notes()
    return {"notes": notes, "count": len(notes)}

@app.post("/api/notes")
async def create_note(payload: NoteCreate):
    """Send text note to Telegram Channel 2 and save to DB."""
    clean_content = payload.content.strip()
    if not clean_content:
        raise HTTPException(status_code=400, detail="Note content cannot be empty.")

    msg_id, channel_id = await storage_client.send_note_message(clean_content)
    note = await add_note(
        content=clean_content,
        telegram_message_id=msg_id,
        telegram_channel_id=channel_id,
        is_demo=storage_client.is_demo
    )
    return note

@app.delete("/api/notes/{note_id}")
async def delete_single_note(note_id: str):
    """Delete a note from DB and remove message from Telegram."""
    note = await delete_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")

    await storage_client.delete_message(
        channel_id=note["telegram_channel_id"],
        message_id=note["telegram_message_id"],
        is_demo=note["is_demo"]
    )
    return {"success": True, "message": "Note deleted."}

# ==================== STATIC UI ====================

STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def serve_favicon():
    favicon_svg = STATIC_DIR / "favicon.svg"
    if favicon_svg.exists():
        return FileResponse(favicon_svg, media_type="image/svg+xml")
    return Response(status_code=204)

@app.get("/")
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Telegram Cloud Hub is running."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

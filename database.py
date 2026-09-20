import aiosqlite
import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple, Union
from config import DB_PATH

def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable string (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.2f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"

def detect_category(filename: str, mime_type: Optional[str] = None) -> str:
    """Detect if a file is a photo, video, software, or general file."""
    ext = filename.split(".")[-1].lower() if "." in filename else ""
    if ext in ["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico", "heic", "tiff"]:
        return "photo"
    if ext in ["mp4", "mkv", "mov", "avi", "webm", "flv", "wmv", "3gp", "m4v"]:
        return "video"
    if ext in ["exe", "apk", "msi", "dmg", "pkg", "deb", "rpm", "iso", "appimage", "zip", "rar", "7z", "tar", "gz"]:
        return "software"
    if mime_type:
        if mime_type.startswith("image/"):
            return "photo"
        if mime_type.startswith("video/"):
            return "video"
        if mime_type in ["application/x-msdownload", "application/vnd.android.package-archive", "application/x-iso9660-image"]:
            return "software"
    return "file"

async def init_db() -> None:
    """Initialize the SQLite database schema with files, todos, notes, otp_sessions, and ip_lockouts tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Files table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                size INTEGER NOT NULL,
                mime_type TEXT,
                category TEXT DEFAULT 'file',
                telegram_message_id INTEGER,
                telegram_channel_id INTEGER,
                telegram_file_id TEXT,
                is_demo INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        # Try adding category column if older table exists
        try:
            await db.execute("ALTER TABLE files ADD COLUMN category TEXT DEFAULT 'file'")
        except Exception:
            pass  # Already exists

        await db.execute("CREATE INDEX IF NOT EXISTS idx_files_created ON files(created_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_files_category ON files(category)")

        # To-Do List table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                completed INTEGER DEFAULT 0,
                telegram_message_id INTEGER,
                telegram_channel_id INTEGER,
                is_demo INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_todos_created ON todos(created_at DESC)")

        # Quick Text Notes table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                telegram_message_id INTEGER,
                telegram_channel_id INTEGER,
                is_demo INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                extra_message_ids TEXT DEFAULT ''
            )
        """)
        try:
            await db.execute("ALTER TABLE notes ADD COLUMN extra_message_ids TEXT DEFAULT ''")
        except Exception:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created_at DESC)")

        # OTP Sessions table (with unique tab binding & cancellation tracking)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS otp_sessions (
                id TEXT PRIMARY KEY,
                ip TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                tab TEXT,
                action TEXT,
                target_name TEXT,
                cancelled INTEGER DEFAULT 0
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_otp_ip ON otp_sessions(ip, created_at DESC)")

        # Migration safe check for new columns in existing databases
        for col_def in ["tab TEXT", "action TEXT", "target_name TEXT", "cancelled INTEGER DEFAULT 0"]:
            col_name = col_def.split()[0]
            try:
                await db.execute(f"ALTER TABLE otp_sessions ADD COLUMN {col_def}")
            except Exception:
                pass

        # IP Lockouts table (Option 3: 10 min lockout after 5 failed attempts)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ip_lockouts (
                ip TEXT PRIMARY KEY,
                locked_until TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_lockout_time ON ip_lockouts(locked_until)")

        await db.commit()

# ==================== FILES ====================

async def add_file(
    filename: str,
    size: int,
    mime_type: Optional[str],
    telegram_message_id: int,
    telegram_channel_id: int,
    telegram_file_id: Optional[str] = None,
    is_demo: bool = False,
    category: Optional[str] = None
) -> Dict[str, Any]:
    """Insert a new file record into the database."""
    file_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    cat = category or detect_category(filename, mime_type)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO files (
                id, filename, size, mime_type, category,
                telegram_message_id, telegram_channel_id, telegram_file_id,
                is_demo, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            file_id, filename, size, mime_type, cat,
            telegram_message_id, telegram_channel_id, telegram_file_id,
            1 if is_demo else 0, created_at
        ))
        await db.commit()

    return {
        "id": file_id,
        "filename": filename,
        "size": size,
        "size_formatted": format_size(size),
        "mime_type": mime_type,
        "category": cat,
        "telegram_message_id": telegram_message_id,
        "telegram_channel_id": telegram_channel_id,
        "telegram_file_id": telegram_file_id,
        "is_demo": is_demo,
        "created_at": created_at
    }

async def get_existing_file_message_ids(channel_id: Optional[int] = None) -> set:
    """Return set of all telegram_message_id values currently stored in files table (filtered by channel if provided)."""
    async with aiosqlite.connect(DB_PATH) as db:
        if channel_id is not None:
            cursor = await db.execute(
                "SELECT telegram_message_id FROM files WHERE telegram_channel_id = ? AND telegram_message_id IS NOT NULL",
                (channel_id,)
            )
        else:
            cursor = await db.execute("SELECT telegram_message_id FROM files WHERE telegram_message_id IS NOT NULL")
        rows = await cursor.fetchall()
        return {r[0] for r in rows}

async def get_existing_note_message_ids() -> set:
    """Return set of all telegram_message_id and extra_message_ids values currently stored in notes table."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT telegram_message_id, extra_message_ids FROM notes WHERE telegram_message_id IS NOT NULL")
        rows = await cursor.fetchall()
        ids = set()
        for r in rows:
            if r["telegram_message_id"] is not None:
                ids.add(r["telegram_message_id"])
            extra = r["extra_message_ids"] if "extra_message_ids" in r.keys() else ""
            if extra:
                for extra_id in str(extra).split(","):
                    extra_id = extra_id.strip()
                    if extra_id.isdigit():
                        ids.add(int(extra_id))
        return ids

async def get_existing_todo_message_ids() -> set:
    """Return set of all telegram_message_id values currently stored in todos table."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT telegram_message_id FROM todos WHERE telegram_message_id IS NOT NULL")
        rows = await cursor.fetchall()
        return {r[0] for r in rows}

async def get_files(category: Optional[str] = None, search: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve files filtered by category and search query."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        conditions = []
        params = []
        if category and category != "all":
            if category == "file":
                conditions.append("(category = 'file' OR category IS NULL OR category = 'document')")
            else:
                conditions.append("category = ?")
                params.append(category)
        if search:
            conditions.append("filename LIKE ?")
            params.append(f"%{search}%")
            
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM files {where_clause} ORDER BY created_at DESC"
        cursor = await db.execute(query, tuple(params))
        rows = await cursor.fetchall()
        
        files_list = []
        for row in rows:
            files_list.append({
                "id": row["id"],
                "filename": row["filename"],
                "size": row["size"],
                "size_formatted": format_size(row["size"]),
                "mime_type": row["mime_type"],
                "category": row["category"] if "category" in row.keys() else "file",
                "telegram_message_id": row["telegram_message_id"],
                "telegram_channel_id": row["telegram_channel_id"],
                "telegram_file_id": row["telegram_file_id"],
                "is_demo": bool(row["is_demo"]),
                "created_at": row["created_at"]
            })
        return files_list

async def get_file(file_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single file by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "filename": row["filename"],
            "size": row["size"],
            "size_formatted": format_size(row["size"]),
            "mime_type": row["mime_type"],
            "category": row["category"] if "category" in row.keys() else "file",
            "telegram_message_id": row["telegram_message_id"],
            "telegram_channel_id": row["telegram_channel_id"],
            "telegram_file_id": row["telegram_file_id"],
            "is_demo": bool(row["is_demo"]),
            "created_at": row["created_at"]
        }

async def delete_file(file_id: str) -> Optional[Dict[str, Any]]:
    """Delete a file record from the database and return its metadata."""
    file_record = await get_file(file_id)
    if not file_record:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await db.commit()
    return file_record

# ==================== TODOS ====================

async def add_todo(
    title: str,
    telegram_message_id: int,
    telegram_channel_id: int,
    is_demo: bool = False,
    created_at: Optional[str] = None,
    completed: bool = False
) -> Dict[str, Any]:
    """Create a new To-Do item."""
    todo_id = str(uuid.uuid4())
    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat()
    completed_at = created_at if completed else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO todos (id, title, completed, telegram_message_id, telegram_channel_id, is_demo, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (todo_id, title, 1 if completed else 0, telegram_message_id, telegram_channel_id, 1 if is_demo else 0, created_at, completed_at))
        await db.commit()
    return {
        "id": todo_id,
        "title": title,
        "completed": completed,
        "telegram_message_id": telegram_message_id,
        "telegram_channel_id": telegram_channel_id,
        "is_demo": is_demo,
        "created_at": created_at,
        "completed_at": completed_at
    }

async def get_todos(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get todos, optionally filtered by status ('active' or 'completed')."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status == "active":
            query = "SELECT * FROM todos WHERE completed = 0 ORDER BY created_at DESC"
            cursor = await db.execute(query)
        elif status == "completed":
            query = "SELECT * FROM todos WHERE completed = 1 ORDER BY completed_at DESC"
            cursor = await db.execute(query)
        else:
            query = "SELECT * FROM todos ORDER BY completed ASC, created_at DESC"
            cursor = await db.execute(query)
        rows = await cursor.fetchall()
        return [{
            "id": r["id"],
            "title": r["title"],
            "completed": bool(r["completed"]),
            "telegram_message_id": r["telegram_message_id"],
            "telegram_channel_id": r["telegram_channel_id"],
            "is_demo": bool(r["is_demo"]),
            "created_at": r["created_at"],
            "completed_at": r["completed_at"]
        } for r in rows]

async def get_todo(todo_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM todos WHERE id = ?", (todo_id,))
        r = await cursor.fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "title": r["title"],
            "completed": bool(r["completed"]),
            "telegram_message_id": r["telegram_message_id"],
            "telegram_channel_id": r["telegram_channel_id"],
            "is_demo": bool(r["is_demo"]),
            "created_at": r["created_at"],
            "completed_at": r["completed_at"]
        }

async def update_todo_status(todo_id: str, completed: bool) -> Optional[Dict[str, Any]]:
    """Toggle or set the completion status of a todo."""
    completed_at = datetime.now(timezone.utc).isoformat() if completed else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE todos SET completed = ?, completed_at = ? WHERE id = ?
        """, (1 if completed else 0, completed_at, todo_id))
        await db.commit()
    return await get_todo(todo_id)

async def delete_todo(todo_id: str) -> Optional[Dict[str, Any]]:
    """Delete a single todo item."""
    todo = await get_todo(todo_id)
    if not todo:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        await db.commit()
    return todo

async def clear_completed_todos() -> List[Dict[str, Any]]:
    """Purge all completed todos and return their records (for Telegram sync)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM todos WHERE completed = 1")
        rows = await cursor.fetchall()
        completed_items = [{
            "id": r["id"],
            "title": r["title"],
            "telegram_message_id": r["telegram_message_id"],
            "telegram_channel_id": r["telegram_channel_id"],
            "is_demo": bool(r["is_demo"])
        } for r in rows]
        
        await db.execute("DELETE FROM todos WHERE completed = 1")
        await db.commit()
        return completed_items

# ==================== NOTES ====================

async def add_note(
    content: str,
    telegram_message_id: int,
    telegram_channel_id: int,
    is_demo: bool = False,
    created_at: Optional[str] = None,
    extra_message_ids: Optional[Union[List[int], str]] = None
) -> Dict[str, Any]:
    """Save a quick text note, optionally linking multi-part Telegram chunk message IDs."""
    note_id = str(uuid.uuid4())
    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat()

    extra_str = ""
    if isinstance(extra_message_ids, list):
        extra_str = ",".join(str(x) for x in extra_message_ids if str(x).isdigit())
    elif isinstance(extra_message_ids, str):
        extra_str = extra_message_ids.strip()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO notes (id, content, telegram_message_id, telegram_channel_id, is_demo, created_at, extra_message_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (note_id, content, telegram_message_id, telegram_channel_id, 1 if is_demo else 0, created_at, extra_str))
        await db.commit()
    return {
        "id": note_id,
        "content": content,
        "telegram_message_id": telegram_message_id,
        "telegram_channel_id": telegram_channel_id,
        "is_demo": is_demo,
        "created_at": created_at,
        "extra_message_ids": [int(x) for x in extra_str.split(",") if x.strip().isdigit()] if extra_str else []
    }

async def get_notes() -> List[Dict[str, Any]]:
    """Get all saved text notes."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM notes ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [{
            "id": r["id"],
            "content": r["content"],
            "telegram_message_id": r["telegram_message_id"],
            "telegram_channel_id": r["telegram_channel_id"],
            "is_demo": bool(r["is_demo"]),
            "created_at": r["created_at"],
            "extra_message_ids": [
                int(x) for x in str(r["extra_message_ids"]).split(",") if x.strip().isdigit()
            ] if "extra_message_ids" in r.keys() and r["extra_message_ids"] else []
        } for r in rows]

async def get_note_by_telegram_id(telegram_message_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve note record by its primary telegram_message_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM notes WHERE telegram_message_id = ?", (telegram_message_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "content": row["content"],
            "telegram_message_id": row["telegram_message_id"],
            "telegram_channel_id": row["telegram_channel_id"],
            "is_demo": bool(row["is_demo"]),
            "created_at": row["created_at"],
            "extra_message_ids": [
                int(x) for x in str(row["extra_message_ids"]).split(",") if x.strip().isdigit()
            ] if "extra_message_ids" in row.keys() and row["extra_message_ids"] else []
        }

async def append_note_chunk(parent_telegram_id: int, new_message_id: int, chunk_content: str) -> bool:
    """
    Append text chunk to an existing note and record continuation message ID.
    Used when a multi-part note arrives in chunks from Telegram.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM notes WHERE telegram_message_id = ?", (parent_telegram_id,))
        row = await cursor.fetchone()
        if not row:
            return False

        current_content = row["content"]
        updated_content = current_content + chunk_content
        current_extra = str(row["extra_message_ids"]) if "extra_message_ids" in row.keys() and row["extra_message_ids"] else ""
        extra_list = [x.strip() for x in current_extra.split(",") if x.strip().isdigit()]
        if str(new_message_id) not in extra_list:
            extra_list.append(str(new_message_id))
        updated_extra = ",".join(extra_list)

        await db.execute("""
            UPDATE notes SET content = ?, extra_message_ids = ? WHERE id = ?
        """, (updated_content, updated_extra, row["id"]))
        await db.commit()
        return True

async def delete_note(note_id: str) -> Optional[Dict[str, Any]]:
    """Delete a note and return its metadata including extra Telegram chunk message IDs."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        extra_ids = [
            int(x) for x in str(row["extra_message_ids"]).split(",") if x.strip().isdigit()
        ] if "extra_message_ids" in row.keys() and row["extra_message_ids"] else []
        note = {
            "id": row["id"],
            "content": row["content"],
            "telegram_message_id": row["telegram_message_id"],
            "telegram_channel_id": row["telegram_channel_id"],
            "is_demo": bool(row["is_demo"]),
            "extra_message_ids": extra_ids
        }
        await db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        await db.commit()
        return note

async def find_note_by_message_id(message_id: int) -> Optional[Dict[str, Any]]:
    """Find a note record by either its primary telegram_message_id or any continuation extra_message_ids."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # 1. Direct match on primary ID
        cursor = await db.execute("SELECT * FROM notes WHERE telegram_message_id = ?", (message_id,))
        row = await cursor.fetchone()
        if row:
            extra_ids = [
                int(x) for x in str(row["extra_message_ids"]).split(",") if x.strip().isdigit()
            ] if "extra_message_ids" in row.keys() and row["extra_message_ids"] else []
            return {
                "id": row["id"],
                "content": row["content"],
                "telegram_message_id": row["telegram_message_id"],
                "telegram_channel_id": row["telegram_channel_id"],
                "is_demo": bool(row["is_demo"]),
                "extra_message_ids": extra_ids
            }
        # 2. Match in continuation extra_message_ids
        cursor = await db.execute("SELECT * FROM notes WHERE extra_message_ids IS NOT NULL AND extra_message_ids != ''")
        rows = await cursor.fetchall()
        for r in rows:
            extra_ids = [
                int(x) for x in str(r["extra_message_ids"]).split(",") if x.strip().isdigit()
            ] if "extra_message_ids" in r.keys() and r["extra_message_ids"] else []
            if message_id in extra_ids:
                return {
                    "id": r["id"],
                    "content": r["content"],
                    "telegram_message_id": r["telegram_message_id"],
                    "telegram_channel_id": r["telegram_channel_id"],
                    "is_demo": bool(r["is_demo"]),
                    "extra_message_ids": extra_ids
                }
        return None

async def find_todo_by_message_id(message_id: int) -> Optional[Dict[str, Any]]:
    """Find a todo record by its telegram_message_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM todos WHERE telegram_message_id = ?", (message_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "completed": bool(row["completed"]),
            "telegram_message_id": row["telegram_message_id"],
            "telegram_channel_id": row["telegram_channel_id"],
            "is_demo": bool(row["is_demo"])
        }

# ==================== OTP & SECURITY LOCKOUT ====================

def hash_otp_code(code: str) -> str:
    """Hash OTP code with SHA-256."""
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()

async def check_ip_lockout(ip: str) -> Tuple[bool, int]:
    """
    Check if an IP address is currently locked out.
    Returns (is_locked, remaining_seconds).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT locked_until FROM ip_lockouts WHERE ip = ?", (ip,))
        row = await cursor.fetchone()
        if not row:
            return False, 0
        
        locked_until_dt = datetime.fromisoformat(row["locked_until"])
        now_dt = datetime.now(timezone.utc)
        diff = (locked_until_dt - now_dt).total_seconds()
        if diff > 0:
            return True, int(diff)
        else:
            # Lockout expired, clean up
            await db.execute("DELETE FROM ip_lockouts WHERE ip = ?", (ip,))
            await db.commit()
            return False, 0

async def lockout_ip(ip: str, duration_seconds: int = 600, reason: str = "failed_otp") -> str:
    """Lock out an IP address for duration_seconds (default: 600s = 10 min)."""
    now = datetime.now(timezone.utc)
    locked_until = (now + timedelta(seconds=duration_seconds)).isoformat()
    created_at = now.isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO ip_lockouts (ip, locked_until, reason, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ip) DO UPDATE SET locked_until = excluded.locked_until, reason = excluded.reason
        """, (ip, locked_until, reason, created_at))
        await db.commit()
    return locked_until

async def check_rate_limit(ip: str, max_requests: int = 4, window_seconds: int = 60) -> Tuple[bool, int]:
    """
    Checks if the IP has exceeded max_requests within window_seconds.
    Returns (allowed, retry_after_seconds).
    """
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(seconds=window_seconds)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT created_at FROM otp_sessions
            WHERE ip = ? AND created_at >= ?
            ORDER BY created_at ASC
        """, (ip, window_start))
        rows = await cursor.fetchall()
        if len(rows) >= max_requests:
            oldest_dt = datetime.fromisoformat(rows[0][0])
            elapsed = (now - oldest_dt).total_seconds()
            retry_after = max(1, int(window_seconds - elapsed))
            return False, retry_after
        return True, 0

async def create_otp_session(
    ip: str,
    code: str,
    expiry_seconds: int = 60,
    tab: Optional[str] = None,
    action: Optional[str] = None,
    target_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Invalidates any previous unverified OTP for this IP and creates a new one tied specifically to a tab and action.
    """
    session_id = str(uuid.uuid4())
    code_hash = hash_otp_code(code)
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at = (now + timedelta(seconds=expiry_seconds)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        # Prune old expired or used sessions older than 1 hour to prevent unbounded SQLite table retention
        purge_cutoff = (now - timedelta(hours=1)).isoformat()
        await db.execute("DELETE FROM otp_sessions WHERE created_at < ?", (purge_cutoff,))

        # Invalidate previous unused OTP sessions for this IP as cancelled
        await db.execute("UPDATE otp_sessions SET used = 1, cancelled = 1 WHERE ip = ? AND used = 0", (ip,))
        await db.execute("""
            INSERT INTO otp_sessions (id, ip, code_hash, attempts, expires_at, created_at, used, tab, action, target_name, cancelled)
            VALUES (?, ?, ?, 0, ?, ?, 0, ?, ?, ?, 0)
        """, (session_id, ip, code_hash, expires_at, created_at, tab, action, target_name))
        await db.commit()

    return {
        "id": session_id,
        "ip": ip,
        "expires_at": expires_at,
        "expires_in": expiry_seconds,
        "created_at": created_at,
        "tab": tab,
        "action": action,
        "target_name": target_name
    }

async def cleanup_expired_otp_sessions(max_age_hours: int = 1) -> int:
    """Purge expired, used, and cancelled OTP sessions to prevent unbounded SQLite growth."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM otp_sessions WHERE created_at < ?", (cutoff,))
        await db.commit()
        return cursor.rowcount

async def cancel_otp_sessions(ip: Optional[str] = None, tab: Optional[str] = None, session_id: Optional[str] = None) -> int:
    """
    Cancels/invalidates active unused OTP sessions.
    External endpoint enforces session_id; internal logic can invalidate by session_id or IP.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if session_id and ip:
            cursor = await db.execute("""
                UPDATE otp_sessions SET cancelled = 1, used = 1
                WHERE id = ? AND ip = ? AND used = 0
            """, (session_id, ip))
        elif session_id:
            cursor = await db.execute("""
                UPDATE otp_sessions SET cancelled = 1, used = 1
                WHERE id = ? AND used = 0
            """, (session_id,))
        elif ip and tab:
            cursor = await db.execute("""
                UPDATE otp_sessions SET cancelled = 1, used = 1
                WHERE ip = ? AND LOWER(tab) = LOWER(?) AND used = 0
            """, (ip, tab))
        elif ip:
            cursor = await db.execute("""
                UPDATE otp_sessions SET cancelled = 1, used = 1
                WHERE ip = ? AND used = 0
            """, (ip,))
        else:
            return 0
        await db.commit()
        return cursor.rowcount

async def get_active_otp_session(ip: str, tab: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve the latest active unused and non-cancelled OTP session for this IP."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if tab:
            cursor = await db.execute("""
                SELECT * FROM otp_sessions
                WHERE ip = ? AND used = 0 AND (cancelled IS NULL OR cancelled = 0) AND expires_at > ? AND LOWER(tab) = LOWER(?)
                ORDER BY created_at DESC LIMIT 1
            """, (ip, now_iso, tab))
        else:
            cursor = await db.execute("""
                SELECT * FROM otp_sessions
                WHERE ip = ? AND used = 0 AND (cancelled IS NULL OR cancelled = 0) AND expires_at > ?
                ORDER BY created_at DESC LIMIT 1
            """, (ip, now_iso))
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)

async def get_otp_session_by_id(session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve an OTP session record directly by its unique session ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM otp_sessions WHERE id = ?", (session_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

async def increment_otp_attempts(session_id: str) -> int:
    """Increment the failed attempt count for an OTP session and return the new count."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE otp_sessions SET attempts = attempts + 1 WHERE id = ?", (session_id,))
        await db.commit()
        cursor = await db.execute("SELECT attempts FROM otp_sessions WHERE id = ?", (session_id,))
        row = await cursor.fetchone()
        return row[0] if row else 1

async def mark_otp_session_used(session_id: str) -> None:
    """Mark an OTP session as used / completed."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE otp_sessions SET used = 1 WHERE id = ?", (session_id,))
        await db.commit()

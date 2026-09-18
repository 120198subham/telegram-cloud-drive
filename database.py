import aiosqlite
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
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
    """Initialize the SQLite database schema with files, todos, and notes tables."""
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
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created_at DESC)")

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
    is_demo: bool = False
) -> Dict[str, Any]:
    """Create a new To-Do item."""
    todo_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO todos (id, title, completed, telegram_message_id, telegram_channel_id, is_demo, created_at)
            VALUES (?, ?, 0, ?, ?, ?, ?)
        """, (todo_id, title, telegram_message_id, telegram_channel_id, 1 if is_demo else 0, created_at))
        await db.commit()
    return {
        "id": todo_id,
        "title": title,
        "completed": False,
        "telegram_message_id": telegram_message_id,
        "telegram_channel_id": telegram_channel_id,
        "is_demo": is_demo,
        "created_at": created_at,
        "completed_at": None
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
    is_demo: bool = False
) -> Dict[str, Any]:
    """Save a quick text note."""
    note_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO notes (id, content, telegram_message_id, telegram_channel_id, is_demo, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (note_id, content, telegram_message_id, telegram_channel_id, 1 if is_demo else 0, created_at))
        await db.commit()
    return {
        "id": note_id,
        "content": content,
        "telegram_message_id": telegram_message_id,
        "telegram_channel_id": telegram_channel_id,
        "is_demo": is_demo,
        "created_at": created_at
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
            "created_at": r["created_at"]
        } for r in rows]

async def delete_note(note_id: str) -> Optional[Dict[str, Any]]:
    """Delete a note."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        note = {
            "id": row["id"],
            "content": row["content"],
            "telegram_message_id": row["telegram_message_id"],
            "telegram_channel_id": row["telegram_channel_id"],
            "is_demo": bool(row["is_demo"])
        }
        await db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        await db.commit()
        return note

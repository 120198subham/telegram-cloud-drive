# SS Workspace — Module Reference (Code Explained in Plain English)

---

## 1. `config.py` — Settings and Credentials Loader

**What it does:**  
This is the master configuration file. When the application starts, it reads all your secret keys and settings from a `.env` file and makes them available to the rest of the code. Think of it as the "settings panel" — every other module asks this file what the API key, channel ID, or maximum file size should be.

**Key things it does:**
- Loads `.env` file (your private credentials) using the `python-dotenv` library.
- Defines helper `get_env_int()` that safely converts environment strings to integers without crashing if the value is missing.
- Exports constants like `API_ID`, `API_HASH`, `BOT_TOKEN`, `CHANNEL_ID`, `TODO_CHANNEL_ID`, `SOFTWARE_CHANNEL_ID`.
- Provides `is_telegram_configured()` which returns `True` only when all required credentials are present — if any are missing, the app falls back to Demo Mode.
- Defines folder paths: `UPLOAD_DIR` (temporary upload staging) and `DEMO_STORAGE_DIR` (demo mode files).

| Constant | Purpose |
| :--- | :--- |
| `API_ID` | Your Telegram app ID from my.telegram.org |
| `API_HASH` | Your Telegram app secret from my.telegram.org |
| `BOT_TOKEN` | Bot authentication token from @BotFather |
| `CHANNEL_ID` | Files/Photos/Videos channel (negative integer) |
| `TODO_CHANNEL_ID` | Tasks/Notes channel |
| `SOFTWARE_CHANNEL_ID` | Software installers channel |
| `MAX_FILE_SIZE` | 2 GB (enforced during upload) |
| `SESSION_NAME` | Filename for the Telethon session file |

---

## 2. `database.py` — Local Catalog and SQLite Operations

**What it does:**  
This is the data layer. It manages a local SQLite database (`cloud_storage.db`) that acts as a searchable catalog of everything stored in Telegram. Note: the actual file bytes are in Telegram. SQLite only stores metadata (names, sizes, IDs, dates) so the app can list and search files without calling Telegram every time.

**Three database tables:**

| Table | What's Stored |
| :--- | :--- |
| `files` | Every uploaded file: name, size, mime type, category, Telegram message ID and channel ID |
| `todos` | Task items: title, done/not done, timestamp, Telegram message ID |
| `notes` | Text notes: content, timestamp, Telegram message ID |

**Important functions explained:**

| Function | Plain English |
| :--- | :--- |
| `init_db()` | Creates the three tables on first run if they don't exist. Also adds indexes for speed. |
| `format_size(bytes)` | Converts 1,572,864 bytes to "1.50 MB" — used in the file listing UI |
| `detect_category(filename, mime)` | Looks at the file extension: `.jpg` → photo, `.mp4` → video, `.exe` → software, everything else → file |
| `add_file(...)` | Generates a UUID, saves metadata row, returns the file dictionary |
| `get_files(category, search)` | Reads all files from SQLite with optional category filter and filename search |
| `get_file(id)` | Fetches one file row by its UUID (used before download or delete) |
| `delete_file(id)` | Removes the metadata row from SQLite |
| `add_todo(title, ...)` | Creates a task row with a UUID, status=0 (pending) |
| `update_todo_status(id, completed)` | Sets `completed=1` and records `completed_at` timestamp |
| `clear_completed_todos()` | Returns all done tasks (so Telegram messages can be deleted) then wipes them from DB |
| `add_note(content, ...)` | Creates a note row |
| `delete_note(id)` | Removes a note row and returns it (so Telegram message can also be deleted) |

All database functions are **async** (they don't block the server while waiting for disk I/O) using the `aiosqlite` library.

---

## 3. `telegram_client.py` — The Telegram Engine

**What it does:**  
This is the heart of the application. It is a Python class (`TelegramStorageClient`) that manages the actual connection to Telegram using the MTProto binary protocol (via Telethon). All file uploads, downloads, and message sending/editing/deleting go through this class.

**Initialization:**

When the app starts, `initialize()` is called. It:
1. Checks if credentials are configured.
2. Connects the bot to Telegram using `TelegramClient.start(bot_token=...)`.
3. Resolves the entity objects for all three channels (needed to send/receive messages).
4. Registers a real-time listener that auto-indexes any file someone sends directly into your Telegram channels (even from the Telegram app on your phone).
5. Falls back to **Demo Mode** (local file storage, no Telegram) if credentials are missing.

**Upload functions:**

| Function | What it Does |
| :--- | :--- |
| `fast_upload_file()` | Splits the file into 512 KB chunks. Spawns 6 workers that each read their assigned chunk and send `SaveBigFilePartRequest` to Telegram simultaneously. 3x retry on failure. Reports progress. Returns `InputFileBig` handle. |
| `upload_file()` | Decides which channel to upload to (Software vs main). Calls `fast_upload_file` for files > 10 MB, standard Telethon upload for smaller files. Posts the file as a Telegram message with a caption. Returns `(message_id, channel_id, file_id)`. |

**Download functions:**

| Function | What it Does |
| :--- | :--- |
| `fast_download_stream()` | For large files (> 10 MB). Fetches the file location and DC ID from the media object. Spawns 6 workers each sending `GetFileRequest` at different offsets. Uses a bounded `asyncio.Queue` to limit memory usage (max 12 chunks buffered = ~6 MB RAM). Uses `asyncio.Condition` to yield chunks in strict order (chunk 0 first, then 1, 2…). Streams directly to browser. |
| `download_file_stream()` | The public-facing download method. Looks up the Telegram message, reads the document size, calls `fast_download_stream` for big files or `iter_download` for small files. Always yields bytes as an async generator. |

**Channel sync:**

| Function | What it Does |
| :--- | :--- |
| `sync_channel_messages()` | Scans both Channel 1 and Channel 3 in batches of 100 message IDs. Any message with a document attachment that isn't already in SQLite gets auto-imported. Used at startup and on `/api/sync`. |

**Productivity channel functions:**

| Function | What it Does |
| :--- | :--- |
| `send_todo_message(title)` | Sends `⏳ [TODO] title` as a plain text message to Channel 2. Returns the message ID. |
| `update_todo_message(id, title, completed)` | Edits the Telegram message to `✅ [COMPLETED] ~title~` or back to `⏳ [TODO] title`. |
| `send_note_message(content)` | Sends `📝 [NOTE]\n\ncontent` to Channel 2. Returns the message ID. |
| `delete_message(channel_id, msg_id)` | Calls `client.delete_messages()` on the appropriate channel. Works for files, todos, and notes. |

**Singleton pattern:**  
At the bottom of the file, a single instance is created: `storage_client = TelegramStorageClient()`. All API routes in `main.py` share this one instance.

---

## 4. `main.py` — The Web API Server

**What it does:**  
This is the command center. It creates the FastAPI web application and defines all the HTTP endpoints (routes) that the browser communicates with. It connects the web layer (browser requests) to the business logic (`telegram_client.py` and `database.py`).

**Application lifecycle:**
- On startup: initializes the database, connects to Telegram, and runs the first channel sync.
- On shutdown: disconnects from Telegram gracefully.

**Authentication Middleware:**  
A function that runs before every API request. It checks for the password token (`Allow`) in three places:
1. A browser cookie named `tg_auth`.
2. A request header named `X-Auth-Token`.
3. A URL query parameter `?auth=Allow`.

Software files are always public (no password needed).

**Key API routes explained:**

| Route | Method | What It Does |
| :--- | :--- | :--- |
| `/api/auth/verify` | POST | Checks password = "Allow", sets a 365-day cookie |
| `/api/status` | GET | Returns health info: mode (live/demo), bot username, channel IDs |
| `/api/files` | GET | Lists all files with category + search filter; triggers prefetch if asked |
| `/api/upload` | POST | Receives file via multipart, saves to temp folder, calls `upload_file()`, records to SQLite, cleans up temp |
| `/api/upload-progress/{id}` | GET | Returns live JSON with percent, speed, ETA for an ongoing upload |
| `/api/download/{id}` | GET | Fetches file from SQLite, calls `download_file_stream()`, returns `StreamingResponse` |
| `/api/files/{id}` | DELETE | Deletes from Telegram then from SQLite |
| `/api/todos` | GET/POST | List all tasks or create a new task |
| `/api/todos/{id}` | PATCH | Toggle task completion |
| `/api/todos/{id}` | DELETE | Delete a specific task |
| `/api/todos/clear-completed` | POST | Bulk delete all done tasks |
| `/api/notes` | GET/POST | List all notes or create a new note |
| `/api/notes/{id}` | DELETE | Delete a specific note |
| `/api/prefetch` | GET/POST | Debounced background sync (only runs if 5+ seconds since last sync) |
| `/api/sync` | POST | Force a full channel scan right now |
| `/favicon.ico` | GET | Returns the cloud+vault SVG icon |
| `/` | GET | Returns `index.html` (the entire single-page app) |

**Upload progress tracking:**  
A Python dictionary `upload_progress_tracker` in memory. The upload endpoint writes to this dict every 512 KB chunk. The browser polls `/api/upload-progress/{id}` every 500ms to get live speed and ETA.

---

## 5. `static/index.html` — The Web User Interface

**What it does:**  
A complete single-page application (SPA) written in plain HTML5 + CSS + JavaScript. No frameworks like React or Vue — just vanilla JavaScript and Tailwind CSS loaded from CDN. It runs entirely in the browser and talks to the FastAPI server via `fetch()` API calls.

**Structure of the UI:**

| Section | Purpose |
| :--- | :--- |
| Password screen | Full-screen overlay shown before authentication |
| Tab bar | 6 tabs: Files, Photos, Videos, Software, Notes, Tasks |
| Upload zone | Drag-and-drop area + file picker button |
| Progress card | Real-time upload telemetry (speed, ETA, stage indicator) |
| File grid | Cards showing filename, size, date, download and delete buttons |
| Notes panel | Text area to write + list of saved notes |
| Tasks panel | Input to add tasks + checklist with complete/delete controls |

**Key JavaScript features:**
- `fetchFiles(category)` — calls `/api/files?category=X` and renders the grid.
- `uploadFile(file)` — generates a random `upload_id`, starts polling `upload-progress`, then POSTs the file.
- `downloadFile(id, name)` — opens the download stream URL with auth token in URL.
- `deleteFile(id)` — sends DELETE request, updates the grid.
- `addTodo()` / `toggleTodo()` / `deleteTodo()` — task management via the API.
- `saveNote()` / `deleteNote()` — notes management via the API.

---

## 6. `requirements.txt` — Python Dependencies

| Package | What It Does |
| :--- | :--- |
| `fastapi` | The web framework — handles routes, middleware, responses |
| `uvicorn` | ASGI server — runs FastAPI and handles HTTP connections |
| `telethon` | Telegram MTProto client — the protocol engine for talking to Telegram |
| `aiosqlite` | Async SQLite — read/write the local catalog without blocking |
| `python-dotenv` | Loads `.env` file into environment variables |
| `cryptg` | C extension that accelerates Telethon's AES encryption ~400x faster than pure Python |
| `python-multipart` | Required by FastAPI to accept file uploads via multipart forms |

---

## 7. `run_server.bat` — Local Startup Script (Windows)

**What it does:**  
A Windows batch script that starts the server locally. It:
1. Activates the Python virtual environment.
2. Runs `uvicorn main:app --host 0.0.0.0 --port 8000`.

Called by `launch_silent.vbs` which runs it invisibly in the background so no terminal window stays open.

---

## 8. `test_app.py` — Automated Tests

**What it does:**  
Uses `pytest` and FastAPI's `TestClient` to test the API without running a real Telegram connection. Tests run in Demo Mode automatically.

| Test | What It Verifies |
| :--- | :--- |
| `test_health_check` | `/api/status` returns 200 and contains "mode" field |
| `test_upload_and_download_demo` | Upload a small file → download it → verify bytes match |
| `test_fast_download_stream_parallel` | 6-worker parallel download engine yields all bytes in correct order |

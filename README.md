# ☁️ Telegram Cloud Storage Drive (2 GB Per-File Vault)

A self-hosted, full-stack cloud storage drive that uses **Telegram's MTProto network** as an unlimited backend file vault.

Uploads are directly dispatched as documents in a private Telegram channel, with **files up to 2 GB** supported, while presenting a modern Google Drive-style web interface.

---

## 🌟 Key Features

- **6-Worker Parallel MTProto Upload Engine** — Splits large files into 512 KB parts dispatched by 6 concurrent workers. Uses `upload.saveBigFilePart` directly, eliminating single-threaded round-trip latency.
- **6-Worker Parallel Streaming Download** — `upload.getFile` fetched across 6 concurrent workers with a bounded sliding window (`asyncio.Condition`) for strict sequential chunk delivery to the browser.
- **Triple-Channel Architecture** — Channel 1 (Files/Photos/Videos), Channel 2 (Tasks + Notes), Channel 3 (Software Installers) — each with independent resolution and routing.
- **Real-Time Upload Progress** — Live per-upload telemetry: stage, percent, MB/s, Mbps, ETA — polled every 500 ms via `/api/upload-progress/{id}`.
- **Password-Protected Drive** — Cookie-based auth (`tg_auth=Allow`). Software tab is always public; all other endpoints require the password.
- **To-Do Task Manager** — Tasks stored as Telegram messages (Channel 2). Create / complete / delete syncs Telegram message text in real time.
- **Quick Notes Pad** — Plaintext notes backed by Telegram messages. Create and delete from the web UI.
- **Auto-Sync on Startup** — Scans all Telegram channels at boot and imports any existing documents into the local SQLite catalog automatically.
- **CI/CD via GitHub → Render** — Every `git push` to `main` auto-deploys to Render in ~60 seconds.
- **cryptg AES-NI Acceleration** — C-extension replacing pyaes, delivering ~400× faster MTProto encryption/decryption.
- **Demo / Simulation Fallback** — Automatically runs in local demo mode if Telegram credentials are not configured.

---

## 🏗️ Architecture

```
[Web Browser]  ──HTTPS──►  [FastAPI + Uvicorn on Render]
                                  │            │
                           [SQLite DB]   [TelegramStorageClient]
                         (metadata only)       │
                                        MTProto TCP 443
                                               │
                              ┌────────────────┼────────────────┐
                           [Ch 1]           [Ch 2]           [Ch 3]
                        Files/Photos     Tasks+Notes        Software
                           /Videos
                              └────────────────┼────────────────┘
                                      [Telegram Data Centers]
                                    (actual bytes — free, unlimited)
```

---

## 🚀 Quick Start

### 1. Clone & Install

```powershell
git clone https://github.com/120198subham/telegram-cloud-drive.git
cd telegram-cloud-drive
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure Telegram Credentials

```powershell
cp .env.example .env
```

Open `.env` and fill in:

| Variable | Where to Get |
| :--- | :--- |
| `TELEGRAM_API_ID` | [my.telegram.org](https://my.telegram.org) → API development tools |
| `TELEGRAM_API_HASH` | Same as above |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHANNEL_ID` | Forward any channel message to [@JsonDumpBot](https://t.me/JsonDumpBot) |
| `TELEGRAM_TODO_CHANNEL_ID` | Same method — Channel 2 for tasks and notes |
| `TELEGRAM_SOFTWARE_CHANNEL_ID` | Same method — Channel 3 for software installers |

> If left blank the server runs in **Demo Mode** — stores files locally and simulates Telegram IDs.

### 3. Start the Server

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Or use the background launcher (no terminal window):

```powershell
cscript launch_silent.vbs
```

Open your browser at 👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## ☁️ Live Deployment

| Item | Value |
| :--- | :--- |
| **Platform** | [Render](https://render.com) — Free Python Web Service |
| **Live URL** | https://drive-ssworkspace.onrender.com |
| **Deploy Trigger** | `git push origin main` → auto-deploys in ~60s |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` |

---

## 📡 REST API Reference

| Method | Endpoint | Auth | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/auth/verify` | Public | Verify password and issue session cookie |
| `GET` | `/api/status` | Public | Health check, mode (Live/Demo), channel status |
| `GET` | `/api/files` | Required | List files with category and search filter |
| `POST` | `/api/upload` | Required | Upload file up to 2 GB → Telegram → SQLite |
| `GET` | `/api/upload-progress/{id}` | Public | Live upload telemetry (%, MB/s, ETA) |
| `GET` | `/api/download/{id}` | Required* | Stream file directly from Telegram |
| `DELETE` | `/api/files/{id}` | Required | Delete from Telegram + SQLite |
| `GET/POST` | `/api/todos` | Required | List or create To-Do tasks |
| `PATCH` | `/api/todos/{id}` | Required | Toggle task completion |
| `DELETE` | `/api/todos/{id}` | Required | Delete task |
| `POST` | `/api/todos/clear-completed` | Required | Bulk delete all completed tasks |
| `GET/POST` | `/api/notes` | Required | List or create text notes |
| `DELETE` | `/api/notes/{id}` | Required | Delete a note |
| `GET/POST` | `/api/prefetch` | Public | Debounced channel sync (5 s throttle) |
| `POST` | `/api/sync` | Required | Force full channel scan |
| `GET` | `/docs` | Public | Interactive Swagger UI |

> *Software category files are publicly downloadable without auth.

---

## 📂 Project Structure

```
telegram-cloud-drive/
├── config.py                      # Credentials loader (.env → typed constants)
├── database.py                    # aiosqlite CRUD — files, todos, notes tables
├── telegram_client.py             # MTProto engine — 6-worker upload/download, messaging
├── main.py                        # FastAPI routes + auth middleware + progress tracker
├── requirements.txt               # Python dependencies (fastapi, telethon, cryptg, ...)
├── .env.example                   # Template for Telegram API keys
├── .gitignore                     # Excludes .env, *.session, db, logs
├── test_app.py                    # pytest automated test suite (3 tests)
├── run_server.bat                 # Windows local startup script
├── launch_silent.vbs              # Background launcher (no terminal window)
├── static/
│   ├── index.html                 # Single-page app (Tailwind CSS + Vanilla JS)
│   └── favicon.svg                # Cloud + vault icon (indigo-cyan gradient)
├── uploads_temp/                  # Staging area — temp files deleted after upload
├── demo_storage/                  # Demo mode local file storage
├── architecture-scanner/          # 📐 Full project documentation
│   ├── README.md                  #    Index and module map
│   ├── 1_flow_diagram.md          #    Master all-modules flowchart + 6 operation flows
│   ├── 2_architecture_diagram.md  #    System + component + upload/download arch diagrams
│   ├── 3_deployment_diagram.md    #    Deployment topology + combined sequence diagram
│   ├── 4_how_to_use.md            #    End-user tab-by-tab feature guide
│   ├── 5_module_reference.md      #    Every function explained in plain English
│   └── 6_data_flow.md             #    Data lifecycle + SQLite ER diagram + auth flow
└── venv/                          # Python virtual environment (not committed)
```

---

## 🧪 Running Tests

```powershell
.\venv\Scripts\python.exe -m pytest test_app.py -v
```

All 3 tests run in Demo Mode (no Telegram credentials needed):
- `test_health_check` — `/api/status` returns 200
- `test_upload_and_download_demo` — upload → download → byte-verify
- `test_fast_download_stream_parallel` — 6-worker parallel engine correctness

---

## 📖 Documentation

Full architecture, flow diagrams, deployment diagrams, data-flow diagrams, and module reference are in the [`architecture-scanner/`](./architecture-scanner/) folder.

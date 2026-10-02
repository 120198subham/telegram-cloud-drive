# ☁️ Telegram Cloud Storage Drive (20 GB Multi-GB Pixel Vault)

A self-hosted, full-stack cloud storage drive that uses **Telegram's MTProto network** as an unlimited backend file vault.

Uploads are directly dispatched as documents in a private Telegram channel, with **files up to 20 GB** supported via the **Pixel Vault AES-256-CTR chunking engine**, while presenting a modern Google Drive-style web interface.

---

## 🌟 Key Features

- **Multi-GB Pixel Vault Engine (Up to 20 GB)** — Automatically detects files exceeding Telegram's 1.9 GB threshold, splitting them into AES-256-CTR encrypted 1.0 GB PNG chunks using compress_level=0 uncompressed binary encapsulation.
- **On-The-Fly Streaming Decryption** — Downloads and decrypts multi-GB files in continuous chunks with zero intermediate disk writes, maintaining a strict RAM footprint under 15 MB.
- **Universal Upload Dropzone** — Prominent drag-and-drop zone with real-time automatic classification badges (`Video`, `Software`, `Photo`, `Vault File`) supporting files up to 20 GB.
- **Dual-Track Download & Decryption Telemetry** — Real-time progress toast tracking both cloud chunk transfer and client-side AES-256 stream assembly with speed indicators.
- **RFC 7233 Byte-Range Resumption** — Full HTTP Range support with `ETag`, `Accept-Ranges: bytes`, and HTTP 416 EOF handling, enabling pause/resume in browsers and download managers.
- **Browser Protection Guidance** — Completed downloads display safe-keeping guidance to help users navigate Chrome/Edge SafeBrowsing prompts for large/executable downloads.
- **6-Worker Parallel MTProto Upload Engine** — Splits large files into 512 KB parts dispatched by 6 concurrent workers. Uses `upload.saveBigFilePart` directly, eliminating single-threaded round-trip latency.
- **6-Worker Parallel Streaming Download** — `upload.getFile` fetched across 6 concurrent workers with a bounded sliding window (`asyncio.Condition`) for strict sequential chunk delivery to the browser.
- **Quad-Channel Architecture** — Four dedicated Telegram channels for distinct operational responsibilities:
  1. **Storage Vault**: Files, Photos, and Videos up to 20 GB.
  2. **Tasks & Notes**: Real-time To-Do items and markdown quick notes with multi-chunk threading.
  3. **Software Vault**: Public application downloads (100% free of OTP for visitors).
  4. **Security & OTP**: Dedicated security channel for dynamic login codes and alert dispatching.
- **Dynamic Telegram OTP Authentication** — 6-digit one-time login passcodes sent directly to the Telegram security channel.
  - **Context-Aware**: The alert specifies the requested Tab and Action (e.g. `Action: 🗑️ Removing Item: setup.exe`).
  - **Per-Tab Isolation**: Switching tabs automatically cancels pending OTPs and immediately purges the Telegram message.
  - **Auto-Purge**: Telegram messages auto-delete after 3 minutes.
- **Brute-Force Defense & IP Lockout** — 5 failed OTP attempts trigger an immediate 10-minute IP lockout and an urgent security alert dispatched to Telegram.
- **Real-Time Upload Progress** — Live per-upload telemetry: stage, percent, MB/s, Mbps, ETA — polled every 500 ms via `/api/upload-progress/{id}`.
- **Auto-Sync on Startup** — Scans all Telegram channels at boot and imports any existing documents into the local SQLite catalog automatically (excluding internal chunk documents).
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
             ┌───────────────────┬─────────────┴─────┬───────────────────┐
          [Ch 1]              [Ch 2]              [Ch 3]              [Ch 4]
       Vault Storage       Tasks & Notes      Software Vault       Security & OTP
     (Files/Photos/Vids)   (To-Dos/Notes)    (Public Downloads)   (Passcodes/Alerts)
             └───────────────────┴─────────────┬─────┴───────────────────┘
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

Open `.env` and fill in your credentials:

| Variable | Where to Get | Description |
| :--- | :--- | :--- |
| `TELEGRAM_API_ID` | [my.telegram.org](https://my.telegram.org) → API development tools | MTProto Application API ID |
| `TELEGRAM_API_HASH` | Same as above | MTProto Application API Hash |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` | Telegram Bot Token |
| `TELEGRAM_CHANNEL_ID` | Forward message to [@JsonDumpBot](https://t.me/JsonDumpBot) | Channel 1: Vault Storage |
| `TELEGRAM_TODO_CHANNEL_ID` | Same method | Channel 2: Tasks & Quick Notes |
| `TELEGRAM_SOFTWARE_CHANNEL_ID` | Same method | Channel 3: Public Software Vault |
| `TELEGRAM_OTP_CHANNEL_ID` | Same method | Channel 4: Dedicated Security OTP Channel |
| `TELEGRAM_OWNER_ID` | Message [@userinfobot](https://t.me/userinfobot) | Your personal Telegram User ID |
| `OTP_AUTO_DELETE_SECONDS` | *(Default: `180`)* | Seconds before Telegram OTP message self-destructs |

> If left blank, the server runs in **Demo Mode** — storing files locally and simulating Telegram IDs.

### 3. Start the Server

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Or use the Windows background launcher (no terminal window):

```powershell
cscript launch_silent.vbs
```

Open your browser at 👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## ☁️ Live Deployment

| Item | Value |
| :--- | :--- |
| **Platform** | [Render](https://render.com) — Web Service |
| **Deploy Trigger** | `git push origin main` → automated zero-downtime deployment |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` |

---

## 📡 REST API Reference

| Method | Endpoint | Auth | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/auth/status` | Public | Telemetry, lockout status, active tab OTP check |
| `POST` | `/api/auth/send-otp` | Public | Dispatch 6-digit OTP to Telegram security channel |
| `POST` | `/api/auth/verify-otp` | Public | Verify OTP code & issue 24h `tg_auth` session cookie |
| `POST` | `/api/auth/cancel-otp` | Public | Cancel active OTP & immediately purge from Telegram |
| `POST` | `/api/auth/logout` | Public | Terminate active authentication cookie session |
| `GET` | `/api/status` | Public | Health check, mode (Live/Demo), channel status |
| `GET` | `/api/files` | Required | List files with category and search filter |
| `POST` | `/api/upload` | Required | Upload file up to 2 GB → Telegram → SQLite |
| `GET` | `/api/upload-progress/{id}` | Public | Live upload telemetry (%, MB/s, ETA) |
| `GET` | `/api/download/{id}` | Public* | Stream file directly from Telegram (*Software free) |
| `DELETE` | `/api/files/{id}` | Required | Delete file from Telegram + SQLite |
| `GET/POST` | `/api/todos` | Required | List or create To-Do tasks |
| `PATCH` | `/api/todos/{id}` | Required | Toggle task completion status |
| `DELETE` | `/api/todos/{id}` | Required | Delete To-Do item |
| `POST` | `/api/todos/clear-completed` | Required | Bulk delete completed tasks |
| `GET/POST` | `/api/notes` | Required | List or create text notes |
| `DELETE` | `/api/notes/{id}` | Required | Delete note from Telegram + SQLite |
| `GET/POST` | `/api/prefetch` | Public | Debounced channel sync (5 s throttle) |
| `POST` | `/api/sync` | Required | Force full channel scan and database sync |
| `GET` | `/docs` | Public | Interactive Swagger API documentation |

---

## 📂 Project Structure

```
telegram-cloud-drive/
├── config.py                      # Credentials loader (.env → typed constants)
├── database.py                    # aiosqlite CRUD — files, todos, notes, OTP sessions
├── telegram_client.py             # MTProto engine — 6-worker upload/download, OTP dispatcher
├── main.py                        # FastAPI routes + auth middleware + progress tracker
├── requirements.txt               # Dependencies (fastapi, telethon, cryptg, pytest, ...)
├── .env.example                   # Clean template for Telegram API credentials
├── .gitignore                     # Excludes .env, *.session, db, logs
├── test_app.py                    # pytest automated test suite (13/13 tests passing)
├── run_server.bat                 # Windows local startup script (hardened 127.0.0.1)
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

Run the full 35-test UAT and regression test suite with `pytest`:

```powershell
.\venv\Scripts\python.exe -m pytest test_uat_validation.py test_app.py -v
```

All 35 automated tests run with full isolation in Demo Mode:
- **12 Pixel Vault & Streaming Tests (`test_uat_validation.py`)**: Category detection (archives as files, video, software), PNG chunk encapsulation, streaming round-trip, bit-exact streaming unpack, telemetry recording, dual-track progress, and RFC 7233 byte-range / EOF 416 probes.
- **23 Security & Core Platform Tests (`test_app.py`)**: Status, upload/download, 6-worker parallel MTProto, dynamic OTP lifecycle, rate limiting, lockout defense, cross-tab session protection, CSP/HSTS headers, path traversal protection, software isolation, note chunk unification, and bi-directional deletion cascade.

---

## 📖 Documentation

Detailed architectural diagrams, user guides, data models, and module references can be found in the [`architecture-scanner/`](./architecture-scanner/) folder.

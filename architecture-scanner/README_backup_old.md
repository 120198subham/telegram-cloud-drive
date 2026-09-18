# Γÿü∩╕Å Telegram Cloud Storage Drive (2 GB Per-File Vault)

A self-hosted, full-stack cloud storage drive that uses **Telegram's MTProto network** as an unlimited backend file vault. 

Uploads are directly dispatched as documents in a private Telegram channel, with **files up to 2 GB** supported, while presenting a modern Google Drive-style web interface.

---

## ≡ƒîƒ Key Features

- **2 GB Per-File Uploads**: Leverages Telegram's native MTProto protocol (via `Telethon`) to bypass the standard HTTP Bot API 50 MB limit.
- **Backend Message Storage**: Every uploaded file is saved as a discrete message in your private Telegram channel.
- **Zero RAM Saturation**: Uploads and downloads are chunked into 1 MB streams to handle multi-gigabyte files without crashing server memory.
- **SQLite Metadata Catalog**: Tracks filename, size, mime-type, upload timestamp, and Telegram `message_id` for instant searches and lookups.
- **Resumable Streaming Downloads**: Downloads stream bytes directly from Telegram back to the browser with standard HTTP headers (`Accept-Ranges`, `Content-Length`, `Content-Disposition`).
- **Demo / Simulation Fallback**: Automatically operates in local demo mode if Telegram credentials are not yet configured, allowing you to test the web interface and API immediately.
- **Modern Web Dashboard**: Drag-and-drop file upload with live progress bar, file search, direct download links, and storage statistics.

---

## ≡ƒÅù∩╕Å Architecture

```
[Web Browser]
      |
      | 1. Upload File (Streamed Chunks up to 2 GB)
      v
[FastAPI Server] <-----------------------------> [SQLite Database]
      |                                           (Filename, Size, Message ID, Date)
      | 2. Upload Document via MTProto
      v
[Telegram MTProto]
      |
      | 3. Stores Document as Message
      v
[Private Telegram Channel] (Storage Vault)
```

---

## ≡ƒÜÇ Quick Start

### 1. Installation

Activate the virtual environment and install the required dependencies:

```powershell
cd C:\Users\12019\.gemini\antigravity\scratch\telegram-cloud-drive

# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Install dependencies (if not already installed)
pip install -r requirements.txt
```

### 2. Configure Telegram Credentials (Optional for Demo Mode)

Copy the `.env.example` file to `.env`:

```powershell
cp .env.example .env
```

Open `.env` and configure your credentials:

1. **`TELEGRAM_API_ID` & `TELEGRAM_API_HASH`**:
   - Log in at [my.telegram.org](https://my.telegram.org).
   - Go to **API development tools** and create an application to obtain your `api_id` and `api_hash`.
2. **`TELEGRAM_BOT_TOKEN`**:
   - Message [@BotFather](https://t.me/BotFather) on Telegram.
   - Run `/newbot` to generate a bot and copy its access token.
3. **`TELEGRAM_CHANNEL_ID`**:
   - Create a new **Private Channel** on Telegram (e.g., `My Cloud Vault`).
   - Add your bot to the channel as an **Administrator** with post and delete permissions.
   - Forward a message from that channel to [@userinfobot](https://t.me/userinfobot) or [@JsonDumpBot](https://t.me/JsonDumpBot) to find the channel ID (usually starts with `-100`, like `-1001234567890`).

*(Note: If left blank, the server runs in **Demo Mode**, storing files locally and simulating Telegram message IDs so you can explore the UI immediately).*

### 3. Start the Server

```powershell
.\venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser at:
≡ƒæë **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## ≡ƒôí REST API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/status` | Connection status, mode (Live vs Demo), channel ID, and max limit. |
| `GET` | `/api/files` | List all stored files with storage stats (total files, space used). |
| `POST` | `/api/upload` | Stream upload a file up to 2 GB and send it as a message to Telegram. |
| `GET` | `/api/download/{id}` | Stream file download directly from Telegram message. |
| `DELETE` | `/api/files/{id}` | Delete file record from SQLite and remove message from Telegram channel. |
| `GET` | `/docs` | Interactive Swagger UI API documentation. |

---

## ≡ƒôé Project Structure

```
telegram-cloud-drive/
Γö£ΓöÇΓöÇ config.py             # Environment variables, 2 GB limit, directory paths
Γö£ΓöÇΓöÇ database.py           # SQLite asynchronous catalog (cloud_storage.db)
Γö£ΓöÇΓöÇ telegram_client.py    # Telethon MTProto client (upload, stream download, message delete)
Γö£ΓöÇΓöÇ main.py               # FastAPI server & streaming REST endpoints
Γö£ΓöÇΓöÇ requirements.txt      # Python dependencies
Γö£ΓöÇΓöÇ .env.example          # Template for Telegram API keys
Γö£ΓöÇΓöÇ static/
Γöé   ΓööΓöÇΓöÇ index.html        # Responsive Single Page Web Application
ΓööΓöÇΓöÇ venv/                 # Python 3.12 virtual environment
```

# SS Workspace — Data Processing & Flow Explained

---

## Overview

Every piece of data in SS Workspace goes through a journey: from your browser, through FastAPI, through Telegram's MTProto binary protocol, and into Telegram's data centers. On the way back (download), it streams from Telegram's DCs directly to your browser.

The local SQLite database is only a **lightweight index** — it stores names, sizes, and references (Telegram message IDs). The actual file bytes never live on the server permanently.

---

## 1. Data Lifecycle: File Upload

```mermaid
sequenceDiagram
    participant BROWSER as Browser (User)
    participant FASTAPI as FastAPI Server (main.py)
    participant TEMP as Temp Folder (uploads_temp/)
    participant TG_CLIENT as TelegramStorageClient
    participant TG_CHANNEL as Telegram Channel 1
    participant SQLITE as SQLite (cloud_storage.db)

    BROWSER->>FASTAPI: POST /api/upload (multipart/form-data)
    Note over BROWSER,FASTAPI: Auth cookie checked first
    FASTAPI->>TEMP: Stream file in 1 MB chunks to disk<br/>(avoids loading full 2 GB into RAM)
    FASTAPI->>FASTAPI: Reject if file > 2 GB (413 error)
    FASTAPI->>TG_CLIENT: upload_file(temp_path, filename, category)
    
    alt File > 10 MB
        TG_CLIENT->>TG_CLIENT: fast_upload_file()<br/>Split into 512 KB parts
        loop 6 workers in parallel
            TG_CLIENT->>TG_CHANNEL: SaveBigFilePartRequest(file_id, part_index, bytes)
        end
    else File ≤ 10 MB
        TG_CLIENT->>TG_CHANNEL: Standard upload_file() single stream
    end
    
    TG_CHANNEL-->>TG_CLIENT: All parts confirmed → InputFileBig handle
    TG_CLIENT->>TG_CHANNEL: send_file(entity, InputFileBig, caption)<br/>Creates a new message with document attachment
    TG_CHANNEL-->>TG_CLIENT: Message ID (integer) + Document ID
    TG_CLIENT-->>FASTAPI: (message_id, channel_id, tg_file_id)
    FASTAPI->>SQLITE: INSERT INTO files (uuid, filename, size, mime, category, message_id, channel_id)
    FASTAPI->>TEMP: Delete temp file
    FASTAPI-->>BROWSER: 200 OK + file record JSON
```

---

## 2. Data Lifecycle: File Download

```mermaid
sequenceDiagram
    participant BROWSER as Browser (User)
    participant FASTAPI as FastAPI Server
    participant SQLITE as SQLite Index
    participant TG_CLIENT as TelegramStorageClient
    participant TG_DC as Telegram Data Center

    BROWSER->>FASTAPI: GET /api/download/{uuid}
    FASTAPI->>SQLITE: SELECT * FROM files WHERE id = uuid
    SQLITE-->>FASTAPI: filename, telegram_message_id, channel_id, size

    FASTAPI->>TG_CLIENT: download_file_stream(message_id, filename, channel_id)
    TG_CLIENT->>TG_DC: get_messages(channel_entity, message_id)
    TG_DC-->>TG_CLIENT: Message object with media (document) attached
    TG_CLIENT->>TG_CLIENT: Extract doc_size, location, dc_id

    alt File > 10 MB (fast path)
        TG_CLIENT->>TG_CLIENT: fast_download_stream()<br/>Bounded asyncio.Queue (max 12 chunks)
        loop 6 workers in parallel
            TG_CLIENT->>TG_DC: GetFileRequest(location, offset=N×512KB, limit=512KB)
            TG_DC-->>TG_CLIENT: Bytes chunk at offset N
        end
        TG_CLIENT->>TG_CLIENT: asyncio.Condition ordering<br/>yield chunk[0], chunk[1], chunk[2]... in strict sequence
    else File ≤ 10 MB (simple path)
        TG_CLIENT->>TG_DC: iter_download(media, chunk_size=1MB)
        TG_DC-->>TG_CLIENT: Sequential 1 MB byte chunks
    end

    TG_CLIENT-->>FASTAPI: Async generator of byte chunks
    FASTAPI-->>BROWSER: StreamingResponse (HTTP chunked transfer)<br/>Content-Disposition: attachment; filename=...
    Note over BROWSER: Browser saves file to Downloads
```

---

## 3. Data Lifecycle: To-Do Tasks

```mermaid
sequenceDiagram
    participant BROWSER as Browser
    participant FASTAPI as FastAPI
    participant SQLITE as SQLite
    participant TG as Telegram Channel 2

    Note over BROWSER,TG: CREATE TASK
    BROWSER->>FASTAPI: POST /api/todos {"title": "Buy milk"}
    FASTAPI->>TG: send_message("⏳ [TODO] Buy milk")
    TG-->>FASTAPI: message_id = 1042
    FASTAPI->>SQLITE: INSERT INTO todos (uuid, "Buy milk", completed=0, msg_id=1042)
    FASTAPI-->>BROWSER: {id, title, completed: false, created_at}

    Note over BROWSER,TG: COMPLETE TASK
    BROWSER->>FASTAPI: PATCH /api/todos/{uuid} {"completed": true}
    FASTAPI->>SQLITE: UPDATE todos SET completed=1, completed_at=now WHERE id=uuid
    FASTAPI->>TG: edit_message(1042, "✅ [COMPLETED] ~Buy milk~")
    FASTAPI-->>BROWSER: Updated todo object

    Note over BROWSER,TG: DELETE TASK
    BROWSER->>FASTAPI: DELETE /api/todos/{uuid}
    FASTAPI->>TG: delete_messages(channel_2, [1042])
    FASTAPI->>SQLITE: DELETE FROM todos WHERE id=uuid
    FASTAPI-->>BROWSER: {success: true}
```

---

## 4. Data Lifecycle: Text Notes

```mermaid
sequenceDiagram
    participant BROWSER as Browser
    participant FASTAPI as FastAPI
    participant SQLITE as SQLite
    participant TG as Telegram Channel 2

    Note over BROWSER,TG: SAVE NOTE
    BROWSER->>FASTAPI: POST /api/notes {"content": "Meeting at 3pm"}
    FASTAPI->>TG: send_message("📝 [NOTE]\n\nMeeting at 3pm")
    TG-->>FASTAPI: message_id = 1087
    FASTAPI->>SQLITE: INSERT INTO notes (uuid, content, msg_id=1087)
    FASTAPI-->>BROWSER: Note object with id and created_at

    Note over BROWSER,TG: DELETE NOTE
    BROWSER->>FASTAPI: DELETE /api/notes/{uuid}
    FASTAPI->>SQLITE: SELECT note → DELETE note
    FASTAPI->>TG: delete_messages(channel_2, [1087])
    FASTAPI-->>BROWSER: {success: true}
```

---

## 5. Channel Sync Data Flow

How existing Telegram channel files get discovered and indexed:

```mermaid
flowchart TD
    A([App Startup or /api/sync called]) --> B[Get known message IDs from SQLite]
    B --> C[Request batch: IDs 1-100 from Channel 1]
    C --> D{Any messages found?}
    D -->|Yes| E{Message has document media?}
    E -->|Yes| F{Already in SQLite?}
    F -->|No| G[Extract filename from DocumentAttributeFilename]
    G --> H[detect_category by extension and mime type]
    H --> I[INSERT INTO files in SQLite]
    I --> J{More batches?}
    F -->|Yes| J
    E -->|No| J
    D -->|No, and past ID 150| K{Software Channel separate?}
    J -->|Yes| C
    K -->|Yes| L[Repeat same process for Channel 3]
    K -->|No| M([Sync done — N new files imported])
    L --> M
```

---

## 6. SQLite Data Model

```mermaid
erDiagram
    FILES {
        TEXT id PK "UUID v4"
        TEXT filename "Original file name"
        INTEGER size "Bytes"
        TEXT mime_type "image/jpeg, video/mp4 etc"
        TEXT category "file | photo | video | software"
        INTEGER telegram_message_id "Channel message number"
        INTEGER telegram_channel_id "Which Telegram channel"
        TEXT telegram_file_id "Telegram document ID"
        INTEGER is_demo "0=live, 1=demo mode"
        TEXT created_at "ISO 8601 UTC timestamp"
    }

    TODOS {
        TEXT id PK "UUID v4"
        TEXT title "Task description"
        INTEGER completed "0=pending, 1=done"
        INTEGER telegram_message_id "Message to edit on toggle"
        INTEGER telegram_channel_id "Channel 2"
        INTEGER is_demo "0=live, 1=demo"
        TEXT created_at "ISO 8601 UTC"
        TEXT completed_at "Set when marked done, null otherwise"
    }

    NOTES {
        TEXT id PK "UUID v4"
        TEXT content "Note body text"
        INTEGER telegram_message_id "Message to delete"
        INTEGER telegram_channel_id "Channel 2"
        INTEGER is_demo "0=live, 1=demo"
        TEXT created_at "ISO 8601 UTC"
    }
```

---

## 7. In-Memory Upload Progress Tracking

The progress tracker is a simple Python dictionary kept in RAM inside `main.py`:

```mermaid
sequenceDiagram
    participant FRONTEND as Browser
    participant UPLOAD as Upload Endpoint
    participant TRACKER as upload_progress_tracker dict
    participant POLL as Progress Endpoint

    FRONTEND->>UPLOAD: POST /api/upload?upload_id=abc123 + file bytes
    UPLOAD->>TRACKER: Set abc123 → {status: "uploading_to_server", percent: 0}
    
    loop Every 512 KB part uploaded
        UPLOAD->>TRACKER: Update abc123 → {percent: X%, speed: Y MB/s, eta: Z sec}
    end
    
    loop Every 500ms (browser polls)
        FRONTEND->>POLL: GET /api/upload-progress/abc123
        POLL->>TRACKER: Read abc123 entry
        TRACKER-->>POLL: Current snapshot
        POLL-->>FRONTEND: {percent: X, speed_mbs: Y, eta_seconds: Z, status: "uploading_to_telegram"}
    end
    
    UPLOAD->>TRACKER: Set abc123 → {percent: 100, status: "completed"}
    FRONTEND->>POLL: Final poll → sees completed
    Note over FRONTEND: Shows green badge — upload done
```

---

## 8. Authentication Data Flow

```mermaid
flowchart LR
    A([Request hits server]) --> B[auth_middleware reads request]
    B --> C{Is path public?\n/api/status, /api/prefetch,\n/static/*, /}
    C -->|Yes| PASS([Allow through])
    C -->|No| D{Is it software list/download?}
    D -->|Yes| PASS
    D -->|No| E{Cookie tg_auth == Allow?}
    E -->|Yes| PASS
    E -->|No| F{Header X-Auth-Token == Allow?}
    F -->|Yes| PASS
    F -->|No| G{Query ?auth=Allow?}
    G -->|Yes| PASS
    G -->|No| BLOCK([Return 401 Unauthorized JSON])
```

---

## 9. Demo Mode vs Live Mode

When `TELEGRAM_API_ID` or other credentials are missing, the app runs in **Demo Mode**:

| Operation | Live Mode | Demo Mode |
| :--- | :--- | :--- |
| File Upload | Sends bytes to Telegram Channel via MTProto | Copies file to `demo_storage/` folder locally |
| File Download | Fetches from Telegram Data Center | Reads from `demo_storage/` folder |
| Todo/Note send | Creates a real Telegram message | Increments a local counter, no message sent |
| Edit/Delete msg | Calls Telegram API | No-op (does nothing) |
| Sync | Scans real Telegram channels | Returns 0, skips |

This allows the app to function as a regular local file manager even without Telegram credentials.

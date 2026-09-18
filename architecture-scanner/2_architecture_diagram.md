# SS Workspace — Architecture Diagram

---

## Full System Architecture

```mermaid
graph TB
    subgraph CLIENT["🌐 Client Layer (Any Browser, Any Device)"]
        UI["Static SPA — index.html\nTailwind CSS, Vanilla JS\nTabs: Files | Photos | Videos | Software | Notes | Tasks"]
    end

    subgraph RENDER["☁️ Cloud — Render (Python Web Service, Free)"]
        UVICORN["Uvicorn ASGI Server\nPort = $PORT (auto-assigned by Render)"]
        FASTAPI["FastAPI Application\nmain.py\nRoutes + Auth Middleware + Progress Tracker"]
        TG_CLIENT["TelegramStorageClient\ntelegram_client.py\nMTProto Engine + 6-Worker Upload/Download"]
        DB["SQLite3 — cloud_storage.db\naiosqlite async driver\nTables: files, todos, notes"]
        CONFIG["config.py\nEnvironment Variables Loader"]
        DATABASE["database.py\nCRUD Functions + Category Detector"]
        STATIC["static/\nindex.html — Web UI\nfavicon.svg — App Icon"]
    end

    subgraph TELEGRAM["📡 Telegram Infrastructure (External)"]
        CH1["Channel 1\nFiles, Photos, Videos Vault\nUp to 2 GB per file"]
        CH2["Channel 2\nTo-Do Tasks and Text Notes"]
        CH3["Channel 3\nSoftware and Installers"]
        BOT["@my_cloud_vault_drive_bot\nMTProto Bot Authentication"]
        DC["Telegram Data Centers\nDC1 US / DC4 EU / DC5 Singapore\nActual file binary storage"]
    end

    subgraph GITHUB["🐙 GitHub (Source Control)"]
        REPO["Private Repo\n120198subham/telegram-cloud-drive\nCI/CD trigger on git push to main"]
    end

    CLIENT -->|"HTTPS REST API\nJSON + Multipart"| UVICORN
    UVICORN --> FASTAPI
    FASTAPI --> TG_CLIENT
    FASTAPI --> DATABASE
    DATABASE --> DB
    TG_CLIENT --> CONFIG
    FASTAPI --> CONFIG
    FASTAPI --> STATIC
    TG_CLIENT -->|"MTProto Binary Protocol\nTCP over Port 443 (Encrypted)"| BOT
    BOT --> CH1
    BOT --> CH2
    BOT --> CH3
    CH1 --> DC
    CH2 --> DC
    CH3 --> DC
    GITHUB -->|"Auto-Deploy webhook\non every push"| RENDER
```

---

## Component Responsibility Map

```mermaid
graph LR
    subgraph ENTRY["Entry Points"]
        A["/ → index.html"]
        B["/favicon.ico → favicon.svg"]
        C["/static/* → Tailwind, Assets"]
    end

    subgraph AUTH["Authentication Gate"]
        D["auth_middleware\nChecks tg_auth cookie\nX-Auth-Token header\nor ?auth=Allow query"]
        E["POST /api/auth/verify\nIssues 365-day cookie"]
    end

    subgraph FILES["File Vault Endpoints"]
        F["GET /api/files\nList with category and search filter"]
        G["POST /api/upload\nStream → Temp → Telegram → SQLite"]
        H["GET /api/download/id\nSQLite → Telegram → StreamingResponse"]
        I["DELETE /api/files/id\nTelegram Delete → SQLite Delete"]
        J["GET /api/upload-progress/id\nReal-time JSON progress"]
    end

    subgraph PRODUCTIVITY["Productivity Endpoints"]
        K["GET/POST /api/todos\nTask list or create task"]
        L["PATCH /api/todos/id\nToggle complete status"]
        M["DELETE /api/todos/id\nRemove task"]
        N["GET/POST /api/notes\nNote list or create note"]
        O["DELETE /api/notes/id\nRemove note"]
    end

    subgraph SYSTEM["System Endpoints"]
        P["GET /api/status\nHealth + channel resolution status"]
        Q["GET POST /api/prefetch\nDebounced Telegram sync"]
        R["POST /api/sync\nForce full channel scan"]
    end

    ENTRY --> AUTH
    AUTH --> FILES
    AUTH --> PRODUCTIVITY
    AUTH --> SYSTEM
```

---

## MTProto Upload Architecture (6-Worker Pool)

```mermaid
graph TD
    FILE["Local Temp File\n2 GB max"]
    SPLIT["Split into 512 KB parts\npart_count = ceil size / 512KB"]
    QUEUE["asyncio.Queue\npart_0, part_1, ..., part_N"]
    W1["Worker 1\nSeek + Read\nSaveBigFilePartRequest"]
    W2["Worker 2\nSeek + Read\nSaveBigFilePartRequest"]
    W3["Worker 3\nSeek + Read\nSaveBigFilePartRequest"]
    W4["Worker 4\nSeek + Read\nSaveBigFilePartRequest"]
    W5["Worker 5\nSeek + Read\nSaveBigFilePartRequest"]
    W6["Worker 6\nSeek + Read\nSaveBigFilePartRequest"]
    RETRY["3x Retry on\nTransient Failures"]
    PROGRESS["Progress Callback\nUpdate upload_progress_tracker\nkB/s, Mbps, ETA"]
    RESULT["InputFileBig\nfile_id + part_count"]
    SENDFILE["client.send_file\nPost message to Channel"]

    FILE --> SPLIT --> QUEUE
    QUEUE --> W1 & W2 & W3 & W4 & W5 & W6
    W1 & W2 & W3 & W4 & W5 & W6 --> RETRY --> PROGRESS
    PROGRESS --> RESULT --> SENDFILE
```

---

## MTProto Download Architecture (6-Worker Sliding Window)

```mermaid
graph TD
    MSG["Telegram Message\nget_messages"]
    INFO["Extract location + dc_id"]
    DC_CHECK{"dc_id != session DC?"}
    BORROW["borrow_exported_sender\nfor foreign DC"]
    DEFAULT["Use default _sender"]
    QUEUE["Bounded asyncio.Queue\nmaxsize = workers × 2 = 12"]
    FEEDER["Feeder coroutine\nEnqueues 0, 1, 2, ... N"]
    W1["Worker 1\nGetFileRequest\noffset = idx × 512KB"]
    W2["Worker 2"]
    W3["Worker 3"]
    W4["Worker 4"]
    W5["Worker 5"]
    W6["Worker 6"]
    COND["asyncio.Condition\ncompleted dict"]
    SEQ["Sequential Yield\ncurrent_part = 0, 1, 2, ...\nBlocks until next part ready"]
    STREAM["StreamingResponse\nHTTP chunked transfer\nto browser"]

    MSG --> INFO --> DC_CHECK
    DC_CHECK -->|Yes| BORROW
    DC_CHECK -->|No| DEFAULT
    BORROW & DEFAULT --> QUEUE
    FEEDER --> QUEUE
    QUEUE --> W1 & W2 & W3 & W4 & W5 & W6
    W1 & W2 & W3 & W4 & W5 & W6 --> COND --> SEQ --> STREAM
```

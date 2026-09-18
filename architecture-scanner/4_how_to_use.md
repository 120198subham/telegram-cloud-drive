# SS Workspace — How to Use Guide

---

## What Is SS Workspace?

SS Workspace is a personal cloud drive and productivity hub that uses **Telegram's private channels as unlimited, free cloud storage**. You can upload any file (up to 2 GB), manage tasks, and write notes — all accessible from any browser, anywhere in the world.

**Live URL:** `https://drive-ssworkspace.onrender.com`

---

## Accessing the Site

### On Any Laptop or Phone:
1. Open a browser (Chrome, Firefox, Edge, Safari).
2. Go to your live URL.
3. A **password prompt** will appear on the screen.
4. Type the password: **`Allow`** and click **Unlock**.
5. You're in! The password is remembered for **1 year** in your browser.

> **Note:** The Software tab is publicly accessible without a password so anyone can download installers directly.

---

## Tab-by-Tab Feature Guide

### 📁 Files Tab
Everything that is not a photo, video, or software — documents, PDFs, spreadsheets, archives, etc.

| Action | How to Do It |
| :--- | :--- |
| **See your files** | Open the Files tab — it auto-loads from your Telegram channel |
| **Search** | Type in the search bar at the top to filter by filename |
| **Upload a file** | Click the **Upload** button or drag and drop a file onto the page |
| **Download a file** | Click the **Download** icon button next to any file |
| **Delete a file** | Click the **Trash** icon button next to any file — removes from both the site and Telegram |

---

### 🖼️ Photos Tab
For image files (`.jpg`, `.png`, `.gif`, `.webp`, `.heic`, etc.)

- Same Upload, Download, Delete as the Files tab.
- Only image files are shown here automatically based on the file extension.

---

### 🎬 Videos Tab
For video files (`.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`, etc.)

- Same Upload, Download, Delete as the Files tab.
- Only video files are shown here automatically.

---

### 💻 Software Tab
For software installers and packages (`.exe`, `.apk`, `.msi`, `.dmg`, `.iso`, `.zip`, `.rar`, `.7z`, etc.)

- **No password needed** to browse and download from this tab.
- Anyone with your URL can directly download software installers.
- You (the owner) can upload to this tab with your password.
- Files in this tab are stored in your dedicated Telegram Software Channel.

---

### 📝 Notes Tab
A quick clipboard / text pad backed by Telegram.

| Action | How to Do It |
| :--- | :--- |
| **Write a note** | Click the text area, type your note, click **Save Note** |
| **View notes** | All saved notes appear as cards below |
| **Delete a note** | Click the **×** button on any note card |

- Notes are sent to your Telegram Channel 2 and also saved locally.
- If you write a note in Telegram directly, it won't appear here (only notes created via the site are tracked).

---

### ✅ Tasks Tab
A full To-Do list, stored in Telegram.

| Action | How to Do It |
| :--- | :--- |
| **Add a task** | Type in the task input field and press Enter or click **Add Task** |
| **Mark complete** | Click the checkbox next to a task |
| **Delete a task** | Click the Trash icon next to a task |
| **Clear all completed** | Click **Clear Completed** to bulk delete done tasks |

- When a task is completed, its Telegram message is automatically edited to show ✅ with a strikethrough.

---

## Real-Time Upload Progress

When uploading a large file (e.g., a 1.97 GB installer):

1. A **progress card** appears showing **Stage 1: Browser → Server** (your local upload speed).
2. Once received, it shows **Stage 2: Server → Telegram Vault**:
   - Live part number (e.g. Part 62 / 3,940)
   - Transfer speed in MB/s and Mbps
   - Estimated time remaining
3. A green **Completed** badge appears when the file is fully synced to your Telegram channel.

---

## How Files Are Actually Stored

Your files are **not stored on Render** (it has limited disk space). Every file you upload is immediately sent to your **private Telegram channels** using the MTProto protocol. Telegram provides:

- **Unlimited free storage** (no quota)
- **Permanent retention** (files stay as long as your channel exists)
- **Up to 2 GB per single file**
- **Built-in redundancy** across multiple data centers

The local server only keeps a lightweight metadata catalog (filename, size, channel ID, message ID) in SQLite for fast browsing. The actual bytes always live in Telegram.

---

## Adding New Files via Telegram Directly

You can also send files directly into your Telegram channel via the Telegram app:
1. Open your storage channel in Telegram.
2. Send any file as a document.
3. Come back to SS Workspace and click the **Sync** button (or reload the page).
4. The file will appear automatically in the correct tab.

---

## Admin Tips

### Pushing Code Changes:
```powershell
git add .
git commit -m "Your change description"
git push
```
Render auto-deploys within 60 seconds.

### Running Tests Locally:
```powershell
.\venv\Scripts\python.exe -m pytest test_app.py -v
```

### Checking Server Health:
```
https://drive-ssworkspace.onrender.com/api/status
```
Returns: `status`, `mode` (live/demo), `bot_username`, and channel resolution status.

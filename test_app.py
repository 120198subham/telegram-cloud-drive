import pytest
import asyncio
import os
from httpx import AsyncClient, ASGITransport
from main import app
from database import init_db, DB_PATH
from config import BASE_DIR, UPLOAD_DIR, DEMO_STORAGE_DIR

import database
TEST_DB_PATH = BASE_DIR / "test_cloud_storage.db"
database.DB_PATH = TEST_DB_PATH

@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DEMO_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    yield
    if TEST_DB_PATH.exists():
        try:
            TEST_DB_PATH.unlink()
        except Exception:
            pass

@pytest.mark.asyncio
async def test_full_workspace_api():
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Status (Public)
        status_resp = await client.get("/api/status")
        assert status_resp.status_code == 200

        # 2. Test unauthorized access
        unauth_resp = await client.get("/api/files")
        assert unauth_resp.status_code == 401

        # 3. Test wrong password
        bad_auth = await client.post("/api/auth/verify", json={"password": "wrong"})
        assert bad_auth.status_code == 401

        # 4. Test correct password "Allow"
        auth_resp = await client.post("/api/auth/verify", json={"password": "Allow"})
        assert auth_resp.status_code == 200

        # Authenticated client with cookie
        client.cookies.set("tg_auth", "Allow")

        # 5. Upload file, photo, video
        # File
        doc_resp = await client.post("/api/upload", files={"file": ("project.pdf", b"PDF file", "application/pdf")})
        assert doc_resp.status_code == 200
        assert doc_resp.json()["category"] == "file"
        doc_id = doc_resp.json()["id"]

        # Photo
        photo_resp = await client.post("/api/upload", files={"file": ("scenery.jpg", b"JPEG image bytes", "image/jpeg")})
        assert photo_resp.status_code == 200
        assert photo_resp.json()["category"] == "photo"

        # Video
        video_resp = await client.post("/api/upload", files={"file": ("clip.mp4", b"MP4 video bytes", "video/mp4")})
        assert video_resp.status_code == 200
        assert video_resp.json()["category"] == "video"

        # 6. Category Filter
        photos_list = await client.get("/api/files?category=photo")
        assert photos_list.status_code == 200
        assert len(photos_list.json()["files"]) == 1
        assert photos_list.json()["files"][0]["filename"] == "scenery.jpg"

        # 7. To-Do List APIs
        todo1 = await client.post("/api/todos", json={"title": "Buy groceries"})
        assert todo1.status_code == 200
        t1_id = todo1.json()["id"]

        todo2 = await client.post("/api/todos", json={"title": "Finish report"})
        assert todo2.status_code == 200
        t2_id = todo2.json()["id"]

        patch_resp = await client.patch(f"/api/todos/{t1_id}", json={"completed": True})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["completed"] is True

        active_resp = await client.get("/api/todos?status=active")
        assert active_resp.status_code == 200
        assert len(active_resp.json()["todos"]) == 1
        assert active_resp.json()["todos"][0]["id"] == t2_id

        clear_resp = await client.post("/api/todos/clear-completed")
        assert clear_resp.status_code == 200
        assert clear_resp.json()["cleared_count"] == 1

        all_todos = await client.get("/api/todos")
        assert len(all_todos.json()["todos"]) == 1

        # 8. Text Notes APIs
        note1 = await client.post("/api/notes", json={"content": "Important meeting at 3 PM today."})
        assert note1.status_code == 200
        n1_id = note1.json()["id"]

        notes_list = await client.get("/api/notes")
        assert notes_list.status_code == 200
        assert len(notes_list.json()["notes"]) == 1
        assert notes_list.json()["notes"][0]["id"] == n1_id

        del_note = await client.delete(f"/api/notes/{n1_id}")
        assert del_note.status_code == 200

        # 9. Test Softwares Tab (Public Access - No Password Required)
        # Clear auth cookie to test as unauthenticated guest
        client.cookies.clear()

        # Public user can list softwares
        soft_list = await client.get("/api/files?category=software")
        assert soft_list.status_code == 200

        # Public user cannot list private files
        priv_list = await client.get("/api/files?category=file")
        assert priv_list.status_code == 401

        # Upload software
        soft_upload = await client.post("/api/upload?category=software", files={"file": ("setup.exe", b"installer bytes", "application/x-msdownload")})
        assert soft_upload.status_code == 200
        assert soft_upload.json()["category"] == "software"
        soft_id = soft_upload.json()["id"]

        # Public user can view the uploaded software
        soft_list2 = await client.get("/api/files?category=software")
        assert soft_list2.status_code == 200
        assert any(f["id"] == soft_id for f in soft_list2.json()["files"])

        # Public user can download software without password
        soft_download = await client.get(f"/api/download/{soft_id}")
        assert soft_download.status_code == 200
        assert soft_download.content == b"installer bytes"

        # 10. Test Prefetch API (Public access on site open)
        prefetch_resp = await client.get("/api/prefetch")
        assert prefetch_resp.status_code == 200
        assert "imported_files" in prefetch_resp.json()

        soft_prefetch = await client.get("/api/files?category=software&prefetch=true")
        assert soft_prefetch.status_code == 200
        assert any(f["id"] == soft_id for f in soft_prefetch.json()["files"])

        # 11. Test Upload with upload_id tracking & /api/upload-progress/{upload_id}
        test_uid = "test-upload-uuid-1234"
        up_track_resp = await client.post(
            f"/api/upload?category=software&upload_id={test_uid}",
            files={"file": ("dual_progress_test.bin", b"A" * 1024 * 100, "application/octet-stream")}
        )
        assert up_track_resp.status_code == 200

        progress_resp = await client.get(f"/api/upload-progress/{test_uid}")
        assert progress_resp.status_code == 200
        pdata = progress_resp.json()
        assert pdata["status"] == "completed"
        assert pdata["percent"] == 100.0
        assert "speed_mbs" in pdata
        assert "eta_seconds" in pdata


@pytest.mark.asyncio
async def test_fast_upload_file_parallel():
    from telegram_client import TelegramStorageClient
    from telethon.tl.types import InputFileBig
    from unittest.mock import AsyncMock

    client_instance = TelegramStorageClient()
    client_instance.is_demo = False
    
    saved_requests = []
    async def mock_call(req):
        saved_requests.append(req)
        await asyncio.sleep(0.005)
        return True

    mock_client = AsyncMock()
    mock_client.side_effect = mock_call
    client_instance.client = mock_client

    test_file = BASE_DIR / "test_big_upload.bin"
    chunk_size = 512 * 1024
    total_chunks = 4
    test_size = chunk_size * total_chunks
    with open(test_file, "wb") as f:
        f.write(b"Z" * test_size)

    try:
        progress_calls = []
        def on_progress(cur, tot):
            progress_calls.append((cur, tot))

        input_big = await client_instance.fast_upload_file(
            file_path=test_file,
            filename="test_big_upload.bin",
            progress_callback=on_progress,
            max_workers=4
        )

        assert isinstance(input_big, InputFileBig)
        assert input_big.name == "test_big_upload.bin"
        assert input_big.parts == total_chunks
        assert len(saved_requests) == total_chunks
        assert len(progress_calls) == total_chunks
        assert progress_calls[-1][0] == test_size
        assert progress_calls[-1][1] == test_size
    finally:
        if test_file.exists():
            test_file.unlink()


@pytest.mark.asyncio
async def test_fast_download_stream_parallel():
    from telegram_client import TelegramStorageClient
    from telethon.tl import types
    from unittest.mock import AsyncMock, MagicMock

    client_instance = TelegramStorageClient()
    client_instance.is_demo = False

    chunk_size = 512 * 1024
    total_parts = 4
    total_size = chunk_size * total_parts

    mock_doc = types.Document(
        id=12345678,
        access_hash=87654321,
        file_reference=b"ref123",
        date=None,
        mime_type="application/octet-stream",
        size=total_size,
        dc_id=2,
        attributes=[]
    )

    async def mock_call(sender, req):
        part_idx = req.offset // chunk_size
        await asyncio.sleep(0.01)
        res = MagicMock()
        res.bytes = f"CHUNK_{part_idx}_DATA_".encode().ljust(chunk_size, b"X")
        return res

    mock_client = MagicMock()
    mock_client._sender = MagicMock()
    mock_client.session.dc_id = 2
    mock_client._call = AsyncMock(side_effect=mock_call)
    client_instance.client = mock_client

    received_chunks = []
    async for chunk in client_instance.fast_download_stream(mock_doc, file_size=total_size, max_workers=4):
        received_chunks.append(chunk)

    assert len(received_chunks) == total_parts
    for i, c in enumerate(received_chunks):
        assert c.startswith(f"CHUNK_{i}_DATA_".encode())
    total_received_bytes = sum(len(c) for c in received_chunks)
    assert total_received_bytes == total_size



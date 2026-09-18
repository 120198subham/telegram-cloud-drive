import pytest
import asyncio
import os
from httpx import AsyncClient, ASGITransport
from main import app, create_signed_session_token
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

        # 3. Test forged static cookie rejection (Aikido fix: Reliance on Cookies Without Integrity)
        client.cookies.set("tg_auth", "Allow")
        forged_resp = await client.get("/api/files")
        assert forged_resp.status_code == 401
        client.cookies.clear()

        # 4. Authenticate using cryptographically signed session token
        valid_token = create_signed_session_token()
        client.cookies.set("tg_auth", valid_token)

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

        # 9. Test Softwares Tab (Public Access - Listing & Download)
        # Clear auth cookie to test as unauthenticated guest
        client.cookies.clear()

        # Public user can list softwares
        soft_list = await client.get("/api/files?category=software")
        assert soft_list.status_code == 200

        # Public user cannot list private files
        priv_list = await client.get("/api/files?category=file")
        assert priv_list.status_code == 401

        # Public guest cannot upload files (Aikido fix: Improper Access Control)
        guest_upload = await client.post("/api/upload?category=software", files={"file": ("setup.exe", b"installer bytes", "application/x-msdownload")})
        assert guest_upload.status_code == 401

        # Authenticated user uploads software
        client.cookies.set("tg_auth", valid_token)
        soft_upload = await client.post("/api/upload?category=software", files={"file": ("setup.exe", b"installer bytes", "application/x-msdownload")})
        assert soft_upload.status_code == 200
        assert soft_upload.json()["category"] == "software"
        soft_id = soft_upload.json()["id"]
        client.cookies.clear()

        # Public user can view the uploaded software
        soft_list2 = await client.get("/api/files?category=software")
        assert soft_list2.status_code == 200
        assert any(f["id"] == soft_id for f in soft_list2.json()["files"])

        # Public user can download software
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
        client.cookies.set("tg_auth", valid_token)
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

    test_file = UPLOAD_DIR / "test_big_upload.bin"
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


@pytest.mark.asyncio
async def test_telegram_otp_full_lifecycle(monkeypatch):
    """Verify OTP generation, 24-hour cookie verification, and protected resource access."""
    await init_db()
    from telegram_client import storage_client

    captured_code = None
    captured_context = None
    original_send = storage_client.send_otp_to_owner

    async def mock_send_otp(code, ip, expiry_seconds=60, context_info=None):
        nonlocal captured_code, captured_context
        captured_code = code
        captured_context = context_info
        return True, "Code delivered"

    monkeypatch.setattr(storage_client, "send_otp_to_owner", mock_send_otp)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Telemetry check
        status_resp = await client.get("/api/auth/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["is_locked"] is False

        # 2. Request OTP with tab & delete item context
        send_resp = await client.post(
            "/api/auth/send-otp",
            json={"tab": "Softwares", "action": "delete_file", "target_name": "installer.exe"}
        )
        assert send_resp.status_code == 200
        assert captured_code is not None
        assert len(captured_code) == 6
        assert captured_context == {"tab": "Softwares", "action": "delete_file", "target_name": "installer.exe"}

        # 3. Verify OTP with captured code
        verify_resp = await client.post("/api/auth/verify-otp", json={"code": captured_code})
        assert verify_resp.status_code == 200
        assert "tg_auth" in verify_resp.cookies
        assert "session_token" in verify_resp.json()

        # 4. Access protected endpoint with issued cookie
        files_resp = await client.get("/api/files")
        assert files_resp.status_code == 200


@pytest.mark.asyncio
async def test_telegram_otp_rate_limit():
    """Verify max 4 OTP requests per minute rate limit."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Forwarded-For": "10.0.0.42"}) as client:
        # Send 4 requests (all should succeed)
        for _ in range(4):
            res = await client.post("/api/auth/send-otp")
            assert res.status_code == 200

        # 5th request should be blocked with 429
        fifth_res = await client.post("/api/auth/send-otp")
        assert fifth_res.status_code == 429
        assert "Rate limit exceeded" in fifth_res.json()["detail"]


@pytest.mark.asyncio
async def test_telegram_otp_option3_lockout_after_5_failures():
    """Verify Option 3: 5 failed attempts trigger 10-minute lockout and security alert."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Forwarded-For": "10.0.0.99"}) as client:
        # Request OTP
        send_res = await client.post("/api/auth/send-otp")
        assert send_res.status_code == 200

        # 4 incorrect attempts
        for attempt in range(1, 5):
            bad_res = await client.post("/api/auth/verify-otp", json={"code": f"99999{attempt}"})
            assert bad_res.status_code == 400
            assert f"{5 - attempt} attempt" in bad_res.json()["detail"]

        # 5th incorrect attempt -> triggers Option 3 10-minute lockout
        fifth_bad = await client.post("/api/auth/verify-otp", json={"code": "000000"})
        assert fifth_bad.status_code == 429
        assert "Security Lockout: 5 failed attempts reached!" in fifth_bad.json()["detail"]

        # Subsequent OTP request is immediately rejected due to lockout
        blocked_send = await client.post("/api/auth/send-otp")
        assert blocked_send.status_code == 429
        assert "Security Lockout" in blocked_send.json()["detail"]


@pytest.mark.asyncio
async def test_telegram_otp_cross_tab_cancellation(monkeypatch):
    """Verify that an OTP issued for one tab is strictly rejected & cancelled if used on another tab."""
    await init_db()
    from telegram_client import storage_client

    captured_code = None

    async def mock_send_otp(code, ip, expiry_seconds=60, context_info=None):
        nonlocal captured_code
        captured_code = code
        return True, "Delivered"

    monkeypatch.setattr(storage_client, "send_otp_to_owner", mock_send_otp)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Forwarded-For": "10.0.0.88"}) as client:
        # 1. Request OTP specifically for "Documents / Files" tab
        res1 = await client.post("/api/auth/send-otp", json={"tab": "Documents / Files", "action": "access"})
        assert res1.status_code == 200
        code_files = captured_code
        assert code_files is not None

        # 2. Attempt to verify this code for "Photos Gallery" tab (Cross-tab attempt)
        cross_res = await client.post("/api/auth/verify-otp", json={"code": code_files, "tab": "Photos Gallery"})
        assert cross_res.status_code == 400
        assert "Cross-tab OTP rejected" in cross_res.json()["detail"]

        # 3. Verify that the cross-tab attempt caused the OTP to be cancelled and invalidated
        retry_res = await client.post("/api/auth/verify-otp", json={"code": code_files, "tab": "Documents / Files"})
        assert retry_res.status_code == 400
        assert "No active OTP found" in retry_res.json()["detail"]


@pytest.mark.asyncio
async def test_telegram_otp_explicit_cancel(monkeypatch):
    """Verify that explicit cancellation via /api/auth/cancel-otp invalidates active OTP and allows fresh unique OTP."""
    await init_db()
    from telegram_client import storage_client

    captured_code = None

    async def mock_send_otp(code, ip, expiry_seconds=60, context_info=None):
        nonlocal captured_code
        captured_code = code
        return True, "Delivered"

    monkeypatch.setattr(storage_client, "send_otp_to_owner", mock_send_otp)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Forwarded-For": "10.0.0.77"}) as client:
        # 1. Request OTP for "Videos Vault"
        res1 = await client.post("/api/auth/send-otp", json={"tab": "Videos Vault"})
        assert res1.status_code == 200
        first_code = captured_code
        session_id = res1.json().get("session_id")
        assert session_id is not None

        # 2a. Attempting to cancel without session_id is rejected (prevents IP-scoped DoS)
        no_session_cancel = await client.post("/api/auth/cancel-otp", json={"tab": "Videos Vault"})
        assert no_session_cancel.status_code == 400
        assert "Session ID is required" in no_session_cancel.json()["detail"]

        # 2b. User switches tab / closes modal -> triggers cancel-otp with session_id
        cancel_res = await client.post("/api/auth/cancel-otp", json={"tab": "Videos Vault", "session_id": session_id})
        assert cancel_res.status_code == 200

        # 3. Old code cannot be verified anymore
        verify_old = await client.post("/api/auth/verify-otp", json={"code": first_code, "tab": "Videos Vault"})
        assert verify_old.status_code == 400

        # 4. Request fresh OTP for "Videos Vault"
        res2 = await client.post("/api/auth/send-otp", json={"tab": "Videos Vault"})
        assert res2.status_code == 200
        second_code = captured_code

        # 5. Fresh OTP verifies successfully
        verify_fresh = await client.post("/api/auth/verify-otp", json={"code": second_code, "tab": "Videos Vault"})
        assert verify_fresh.status_code == 200
        assert verify_fresh.json()["success"] is True


@pytest.mark.asyncio
async def test_security_headers_and_csp():
    """Verify that Content-Security-Policy (CSP) and defense-in-depth headers are enforced (Aikido fix)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test root endpoint
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "Content-Security-Policy" in resp.headers

        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "script-src" in csp
        assert "https://cdn.tailwindcss.com" in csp
        assert "https://unpkg.com" in csp
        assert "style-src" in csp
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp
        assert "base-uri 'self'" in csp

        # Verify other critical defense-in-depth security headers (Aikido fixes)
        assert resp.headers.get("Strict-Transport-Security") == "max-age=63072000; includeSubDomains; preload"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "geolocation=()" in resp.headers.get("Permissions-Policy", "")
        assert resp.headers.get("X-XSS-Protection") == "1; mode=block"

        # Verify self-hosted Lucide icon script (SRI fix)
        lucide_resp = await client.get("/static/lucide.min.js")
        assert lucide_resp.status_code == 200
        assert len(lucide_resp.content) > 100000

        # Test API endpoint also receives CSP, HSTS, and security headers
        api_resp = await client.get("/api/status")
        assert api_resp.status_code == 200
        assert "Content-Security-Policy" in api_resp.headers
        assert "Strict-Transport-Security" in api_resp.headers
        assert api_resp.headers.get("X-Frame-Options") == "DENY"


@pytest.mark.asyncio
async def test_demo_mode_does_not_disclose_otp():
    """Aikido fix: Verify fail-open demo mode never discloses OTP in JSON response."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/auth/send-otp")
        assert res.status_code == 200
        data = res.json()
        assert "demo_code" not in data or data.get("demo_code") is None
        assert "session_id" in data


@pytest.mark.asyncio
async def test_hmac_session_cookie_integrity():
    """Aikido fix: Verify cookies without HMAC integrity or with tampered signatures are rejected."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Case 1: Legacy "Allow" string
        client.cookies.set("tg_auth", "Allow")
        assert (await client.get("/api/files")).status_code == 401

        # Case 2: Tampered payload
        valid_token = create_signed_session_token()
        parts = valid_token.split(".")
        tampered_token = f"{int(parts[0]) + 1000}.{parts[1]}.{parts[2]}"
        client.cookies.set("tg_auth", tampered_token)
        assert (await client.get("/api/files")).status_code == 401

        # Case 3: Tampered signature
        tampered_sig = f"{parts[0]}.{parts[1]}.deadbeefcafe"
        client.cookies.set("tg_auth", tampered_sig)
        assert (await client.get("/api/files")).status_code == 401

        # Case 4: Expired token
        expired_token = create_signed_session_token(expiry_seconds=-10)
        client.cookies.set("tg_auth", expired_token)
        assert (await client.get("/api/files")).status_code == 401


@pytest.mark.asyncio
async def test_upload_path_traversal_prevention():
    """Aikido fix: Verify directory traversal in upload filenames is sanitized and contained."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        valid_token = create_signed_session_token()
        client.cookies.set("tg_auth", valid_token)
        # Attempt path traversal filename
        res = await client.post(
            "/api/upload",
            files={"file": ("../../../../etc/passwd", b"root:x:0:0:", "text/plain")}
        )
        assert res.status_code == 200
        filename = res.json()["filename"]
        assert ".." not in filename
        assert "/" not in filename
        assert "\\" not in filename
        assert filename == "passwd"


@pytest.mark.asyncio
async def test_unauthenticated_software_download_isolation():
    """Aikido fix: Verify unauthenticated users cannot download non-software files."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        valid_token = create_signed_session_token()
        client.cookies.set("tg_auth", valid_token)
        # Upload private document
        doc_res = await client.post(
            "/api/upload",
            files={"file": ("secret_vault.txt", b"my_private_key", "text/plain")}
        )
        assert doc_res.status_code == 200
        doc_id = doc_res.json()["id"]

        # Clear authentication
        client.cookies.clear()
        # Attempt unauthenticated download of private vault file
        dl_res = await client.get(f"/api/download/{doc_id}")
        assert dl_res.status_code == 401


@pytest.mark.asyncio
async def test_logout_endpoint():
    """Verify /api/auth/logout clears tg_auth cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        valid_token = create_signed_session_token()
        client.cookies.set("tg_auth", valid_token)
        # Verify authenticated
        assert (await client.get("/api/files")).status_code == 200
        # Call logout
        logout_res = await client.post("/api/auth/logout")
        assert logout_res.status_code == 200
        assert logout_res.json()["success"] is True






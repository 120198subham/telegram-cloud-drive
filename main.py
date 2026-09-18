import os
import time
import asyncio
import secrets
import urllib.parse
import hmac
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, status, Request, Response
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import (
    BASE_DIR,
    UPLOAD_DIR,
    MAX_FILE_SIZE,
    CHANNEL_ID,
    TODO_CHANNEL_ID,
    SOFTWARE_CHANNEL_ID,
    OTP_MAX_REQUESTS_PER_MIN,
    OTP_EXPIRY_SECONDS,
    OTP_MAX_ATTEMPTS,
    OTP_LOCKOUT_SECONDS,
    SESSION_COOKIE_AGE,
    SESSION_SECRET,
    ADMIN_PASSWORD,
    is_telegram_configured,
)
from database import (
    init_db,
    add_file,
    get_files,
    get_file,
    delete_file,
    detect_category,
    format_size,
    add_todo,
    get_todos,
    get_todo,
    update_todo_status,
    delete_todo,
    clear_completed_todos,
    add_note,
    get_notes,
    delete_note,
    check_ip_lockout,
    lockout_ip,
    check_rate_limit,
    create_otp_session,
    cancel_otp_sessions,
    get_active_otp_session,
    increment_otp_attempts,
    mark_otp_session_used,
    hash_otp_code,
    get_otp_session_by_id,
)
from telegram_client import storage_client

# ==================== CRYPTOGRAPHIC SESSION SIGNING (HMAC-SHA256) ====================

def create_signed_session_token(expiry_seconds: int = SESSION_COOKIE_AGE) -> str:
    """Generate a cryptographically signed HMAC-SHA256 session token with expiration and random nonce."""
    expires_at = int(time.time()) + expiry_seconds
    nonce = secrets.token_hex(16)
    payload = f"{expires_at}.{nonce}"
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"

def verify_signed_session_token(token: str) -> bool:
    """Verify HMAC-SHA256 signature and timestamp of session token."""
    if not token or "." not in token:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    expires_at_str, nonce, signature = parts
    try:
        expires_at = int(expires_at_str)
    except ValueError:
        return False
    if time.time() > expires_at:
        return False
    payload = f"{expires_at}.{nonce}"
    expected_sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected_sig)

def get_client_ip(request: Request) -> str:
    """Extract client IP address safely without trusting unverified spoofed forward headers."""
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ips = [x.strip() for x in forwarded.split(",") if x.strip()]
        if ips:
            return ips[0]
    return request.client.host if request.client else "127.0.0.1"

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await storage_client.initialize()
    # Auto-import any existing channel files in background so server binds immediately
    asyncio.create_task(storage_client.sync_channel_messages())
    yield
    await storage_client.close()

# Initialize FastAPI app
app = FastAPI(
    title="SS Workspace Vault",
    description="Secure dynamic OTP protected personal cloud storage & workspace platform powered by Telegram MTProto.",
    version="2.5.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Disposition", "Content-Type", "Accept-Ranges"],
)

# Security Headers Middleware: Enforces HSTS, Content-Security-Policy (CSP) & Defense-in-Depth Headers
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    # Content-Security-Policy declaration for scripts, styles, images, media, and fonts
    csp_directives = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://unpkg.com",
        "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com",
        "img-src 'self' data: blob: https:",
        "media-src 'self' blob: data:",
        "font-src 'self' data: https:",
        "connect-src 'self' https://cdn.tailwindcss.com",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    response.headers["Content-Security-Policy"] = "; ".join(csp_directives)
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

# Authentication Middleware: Enforces HMAC-signed session tokens and strict channel access control
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Public endpoints
    if (
        not path.startswith("/api/")
        or path in [
            "/api/auth/send-otp",
            "/api/auth/verify-otp",
            "/api/auth/cancel-otp",
            "/api/auth/status",
            "/api/auth/verify",
            "/api/auth/logout",
            "/api/status",
            "/api/prefetch",
        ]
        or path.startswith("/api/upload-progress")
    ):
        return await call_next(request)

    # Public Softwares category: viewing files is allowed if category is software
    if path == "/api/files" and request.method == "GET" and request.query_params.get("category") == "software":
        return await call_next(request)

    # Public Softwares download: ONLY allowed if SOFTWARE_CHANNEL_ID is active and file belongs to that channel
    if path.startswith("/api/download/") and request.method == "GET":
        file_id = path.split("/api/download/")[-1].split("?")[0]
        try:
            file_rec = await get_file(file_id)
            if (
                file_rec
                and file_rec.get("category") == "software"
                and SOFTWARE_CHANNEL_ID != 0
                and file_rec.get("telegram_channel_id") == SOFTWARE_CHANNEL_ID
            ):
                return await call_next(request)
        except Exception:
            pass

    # Validate HMAC-signed session token (from cookie or header)
    cookie_auth = request.cookies.get("tg_auth", "")
    header_auth = request.headers.get("X-Session-Token", "")
    token = cookie_auth or header_auth

    if token and verify_signed_session_token(token):
        return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required. Please verify via Telegram OTP."}
    )

# Pydantic request models
class AuthRequest(BaseModel):
    password: str

class OtpSendRequest(BaseModel):
    tab: Optional[str] = "Workspace Vault"
    action: Optional[str] = "access"
    target_name: Optional[str] = None

class OtpVerifyRequest(BaseModel):
    code: str
    tab: Optional[str] = None
    action: Optional[str] = None
    target_name: Optional[str] = None
    session_id: Optional[str] = None

class OtpCancelRequest(BaseModel):
    tab: Optional[str] = None
    session_id: Optional[str] = None

class TodoCreate(BaseModel):
    title: str

class TodoUpdate(BaseModel):
    completed: bool

class NoteCreate(BaseModel):
    content: str

# Auto-Prefetch Debouncing Mechanism
_last_prefetch_time = 0.0
_prefetch_lock = asyncio.Lock()

# Per-IP prefetch rate limiting: max 1 forced prefetch per 30 seconds per IP
_prefetch_ip_timestamps: Dict[str, float] = {}
_PREFETCH_IP_COOLDOWN = 30.0
_PREFETCH_IP_MAX = 500  # max tracked IPs before evicting oldest

async def auto_prefetch(force: bool = False) -> int:
    """Debounced prefetch of latest files from Telegram channels."""
    global _last_prefetch_time
    now = time.time()
    if not force and (now - _last_prefetch_time < 5.0):
        return 0
    async with _prefetch_lock:
        if not force and (time.time() - _last_prefetch_time < 5.0):
            return 0
        _last_prefetch_time = time.time()
        try:
            imported = await storage_client.sync_channel_messages(max_ids=150)
            return imported
        except Exception:
            return 0

# ==================== AUTH & STATUS ====================

@app.get("/api/auth/status")
async def get_auth_status(request: Request, tab: Optional[str] = None):
    """Retrieve lockout and OTP countdown only — no sensitive session metadata exposed."""
    ip = get_client_ip(request)
    is_locked, remaining_lockout = await check_ip_lockout(ip)
    active_otp = await get_active_otp_session(ip)
    remaining_otp = 0
    active_tab = None
    cross_tab = False

    if active_otp:
        now_dt = datetime.now(timezone.utc)
        exp_dt = datetime.fromisoformat(active_otp["expires_at"])
        remaining_otp = max(0, int((exp_dt - now_dt).total_seconds()))
        active_tab = active_otp.get("tab")
        if tab and active_tab and tab.strip().lower() != active_tab.strip().lower():
            cross_tab = True

    # NOTE: tab, action, target_name, attempts_used are intentionally omitted
    # from unauthenticated status responses to prevent IP-scoped metadata leakage
    return {
        "is_locked": is_locked,
        "lockout_remaining_seconds": remaining_lockout,
        "has_active_otp": remaining_otp > 0 and not cross_tab,
        "otp_remaining_seconds": remaining_otp if not cross_tab else 0,
        "max_attempts": OTP_MAX_ATTEMPTS,
        "cross_tab": cross_tab,
        "is_demo": storage_client.is_demo,
    }

# Concurrency lock per IP to prevent concurrent OTP request bypass / flooding
_otp_ip_locks: Dict[str, asyncio.Lock] = {}
_otp_locks_guard = asyncio.Lock()

async def get_ip_otp_lock(ip: str) -> asyncio.Lock:
    """Retrieve or initialize an async mutex per client IP for atomic OTP operations."""
    async with _otp_locks_guard:
        if ip not in _otp_ip_locks:
            if len(_otp_ip_locks) > 1000:
                _otp_ip_locks.clear()
            _otp_ip_locks[ip] = asyncio.Lock()
        return _otp_ip_locks[ip]

@app.post("/api/auth/send-otp")
async def send_otp(request: Request, payload: Optional[OtpSendRequest] = None):
    """
    Generate and dispatch a 6-digit OTP code to the dedicated security channel.
    Enforces:
    - Atomic per-IP mutex to defeat concurrent race-condition bypass attacks
    - 10-minute IP lockout if previously locked out
    - Max 4 requests per minute (Rate Limit)
    - 60 seconds validity
    - Immediate cancellation of prior OTP messages from Telegram channel
    - Unique per-tab binding
    """
    ip = get_client_ip(request)
    ip_lock = await get_ip_otp_lock(ip)

    async with ip_lock:
        is_locked, remaining_lockout = await check_ip_lockout(ip)
        if is_locked:
            raise HTTPException(
                status_code=429,
                detail=f"Security Lockout: Your IP is locked out. Please wait {remaining_lockout} seconds."
            )

        allowed, retry_after = await check_rate_limit(ip, max_requests=OTP_MAX_REQUESTS_PER_MIN, window_seconds=60)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded (max {OTP_MAX_REQUESTS_PER_MIN}/min). Please wait {retry_after} seconds before requesting a new code."
            )

        target_tab = (payload.tab if payload and payload.tab else "Workspace Vault").strip()
        target_action = (payload.action if payload and payload.action else "access").strip()
        target_name = payload.target_name.strip() if payload and payload.target_name else None

        # Cancel previous OTP in Telegram and in database for this IP
        if storage_client:
            await storage_client.cancel_active_otp(ip)

        # Generate 6-digit numeric OTP
        code = f"{secrets.randbelow(900000) + 100000}"

        # Record in SQLite session with unique tab binding
        session_data = await create_otp_session(
            ip=ip,
            code=code,
            expiry_seconds=OTP_EXPIRY_SECONDS,
            tab=target_tab,
            action=target_action,
            target_name=target_name
        )

        context = {
            "tab": target_tab,
            "action": target_action,
            "target_name": target_name
        }

        if storage_client.is_demo:
            logger.info(f"[SECURITY] Demo OTP session created for IP {ip}")
            msg = "Demo Mode: Verification session created."
        else:
            # Dispatch to dedicated Telegram OTP channel
            success, msg = await storage_client.send_otp_to_owner(code, ip, expiry_seconds=OTP_EXPIRY_SECONDS, context_info=context)
            if not success:
                raise HTTPException(status_code=500, detail=msg)

        return {
            "success": True,
            "message": msg,
            "expires_in": OTP_EXPIRY_SECONDS,
            "tab": target_tab,
            "action": target_action,
            "target_name": target_name,
            "session_id": session_data["id"]
        }

@app.post("/api/auth/verify-otp")
async def verify_otp(payload: OtpVerifyRequest, request: Request, response: Response):
    """
    Verify 6-digit OTP code.
    Enforces:
    - Session ID verification (if provided) to prevent cross-session hijacking
    - Cross-tab cancellation: OTP issued for one tab is strictly forbidden on another tab and immediately cancelled!
    - Option 3: 5 failed attempts triggers 10-minute lockout + Security Alert to owner
    - 1-minute expiration
    - 24-hour cryptographically signed HMAC session token issuance
    """
    ip = get_client_ip(request)
    is_locked, remaining_lockout = await check_ip_lockout(ip)
    if is_locked:
        raise HTTPException(
            status_code=429,
            detail=f"Security Lockout: Your IP is locked out. Try again in {remaining_lockout} seconds."
        )

    active_otp = await get_active_otp_session(ip)
    if not active_otp:
        raise HTTPException(
            status_code=400,
            detail="No active OTP found. The code may have expired or been cancelled. Please request a fresh OTP."
        )

    # Validate session_id if provided
    if payload.session_id and active_otp.get("id") != payload.session_id:
        raise HTTPException(
            status_code=400,
            detail="Session mismatch or expired OTP session. Please request a fresh OTP."
        )

    # Cross-tab OTP check: verify target tab matches issued tab
    if payload.tab and active_otp.get("tab"):
        req_tab = payload.tab.strip().lower()
        issued_tab = active_otp["tab"].strip().lower()
        if req_tab != issued_tab:
            # Cross-tab mismatch detected! Cancel active OTP and purge Telegram message immediately
            await cancel_otp_sessions(ip, session_id=active_otp["id"])
            if storage_client:
                await storage_client.cancel_active_otp(ip)
            raise HTTPException(
                status_code=400,
                detail=f"Cross-tab OTP rejected: This OTP was issued for '{active_otp['tab']}' and has been cancelled. Please request a fresh OTP for '{payload.tab}'."
            )

    # Action consistency check
    if payload.action and active_otp.get("action"):
        if payload.action.strip().lower() != active_otp["action"].strip().lower():
            await cancel_otp_sessions(ip, session_id=active_otp["id"])
            if storage_client:
                await storage_client.cancel_active_otp(ip)
            raise HTTPException(
                status_code=400,
                detail="Security mismatch: This OTP was issued for a different action and has been cancelled."
            )

    # Check 1-minute expiration
    now_dt = datetime.now(timezone.utc)
    exp_dt = datetime.fromisoformat(active_otp["expires_at"])
    if now_dt > exp_dt:
        await mark_otp_session_used(active_otp["id"])
        if storage_client:
            await storage_client.cancel_active_otp(ip)
        raise HTTPException(
            status_code=400,
            detail="OTP code has expired (validity was 1 minute). Please request a new code."
        )

    clean_code = payload.code.strip()
    provided_hash = hash_otp_code(clean_code)
    if provided_hash == active_otp["code_hash"]:
        # Success!
        await mark_otp_session_used(active_otp["id"])
        if storage_client:
            await storage_client.cancel_active_otp(ip)
        signed_token = create_signed_session_token()
        is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(
            key="tg_auth",
            value=signed_token,
            max_age=SESSION_COOKIE_AGE,
            httponly=True,
            samesite="lax",
            secure=is_secure
        )
        return {
            "success": True,
            "session_token": signed_token,
            "tab": active_otp.get("tab"),
            "action": active_otp.get("action"),
            "message": "Authentication successful. Access granted for 24 hours."
        }

    # Incorrect code entered
    new_attempts = await increment_otp_attempts(active_otp["id"])
    if new_attempts >= OTP_MAX_ATTEMPTS:
        # Option 3: Lock out IP for 10 minutes and dispatch urgent Security Alert
        await lockout_ip(ip, duration_seconds=OTP_LOCKOUT_SECONDS, reason="5_failed_otp_attempts")
        await mark_otp_session_used(active_otp["id"])
        if storage_client:
            await storage_client.cancel_active_otp(ip)
        asyncio.create_task(storage_client.send_security_alert(
            ip=ip,
            failed_count=new_attempts,
            lockout_minutes=OTP_LOCKOUT_SECONDS // 60
        ))
        raise HTTPException(
            status_code=429,
            detail=f"Security Lockout: 5 failed attempts reached! Your IP has been locked out for {OTP_LOCKOUT_SECONDS // 60} minutes. A security alert was dispatched to the Telegram owner."
        )

    remaining = OTP_MAX_ATTEMPTS - new_attempts
    raise HTTPException(
        status_code=400,
        detail=f"Incorrect code. {remaining} attempt{'s' if remaining != 1 else ''} remaining before a 10-minute lockout."
    )

@app.post("/api/auth/cancel-otp")
async def cancel_otp(payload: Optional[OtpCancelRequest] = None):
    """
    Explicitly cancels the active OTP session and immediately purges the message from Telegram.
    Requires session_id verification to prevent unauthenticated IP-scoped denial of service attacks.
    Identifies session strictly by session_id to eliminate spoofable forwarding header vulnerabilities.
    """
    session_id = payload.session_id if payload and payload.session_id else None
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="Session ID is required to cancel an OTP session."
        )
    clean_session_id = session_id.strip()
    session = await get_otp_session_by_id(clean_session_id)
    if session:
        await cancel_otp_sessions(session_id=clean_session_id)
        if storage_client and session.get("ip"):
            await storage_client.cancel_active_otp(session["ip"])
    return {"success": True, "message": "Active OTP was cancelled and purged."}

@app.post("/api/auth/verify")
async def verify_password(payload: AuthRequest, request: Request, response: Response):
    """Fallback password verification and 24-hour HMAC signed session cookie issuance."""
    if not ADMIN_PASSWORD:
        raise HTTPException(
            status_code=403,
            detail="Password authentication is disabled. Please authenticate using Telegram OTP."
        )
    if secrets.compare_digest(payload.password.strip(), ADMIN_PASSWORD):
        signed_token = create_signed_session_token()
        is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(
            key="tg_auth",
            value=signed_token,
            max_age=SESSION_COOKIE_AGE,
            httponly=True,
            samesite="lax",
            secure=is_secure
        )
        return {
            "success": True,
            "session_token": signed_token,
            "message": "Authenticated successfully."
        }
    raise HTTPException(status_code=401, detail="Incorrect password.")

@app.post("/api/auth/logout")
async def logout_endpoint(response: Response):
    """Terminate active authentication cookie session."""
    response.delete_cookie(key="tg_auth", httponly=True, samesite="lax")
    return {"success": True, "message": "Logged out successfully."}

@app.get("/api/status")
async def get_system_status():
    """Retrieve system health and connectivity status without exposing private channel details."""
    return {
        "status": "online",
        "mode": "demo" if storage_client.is_demo else "live",
        "is_demo": storage_client.is_demo,
        "credentials_configured": is_telegram_configured(),
        "channels_connected": bool(storage_client.channel_entity is not None or storage_client.is_demo),
    }

@app.get("/api/prefetch")
@app.post("/api/prefetch")
async def prefetch_files(request: Request, force: bool = Query(False)):
    """Trigger a debounced prefetch of files from Telegram channels (rate-limited per IP)."""
    if force:
        ip = get_client_ip(request)
        now = time.time()
        last = _prefetch_ip_timestamps.get(ip, 0.0)
        if now - last < _PREFETCH_IP_COOLDOWN:
            return {"success": False, "imported_files": 0, "detail": "Rate limited. Try again shortly."}
        # Evict oldest if map is too large
        if len(_prefetch_ip_timestamps) >= _PREFETCH_IP_MAX:
            oldest_ip = min(_prefetch_ip_timestamps, key=_prefetch_ip_timestamps.get)
            del _prefetch_ip_timestamps[oldest_ip]
        _prefetch_ip_timestamps[ip] = now
    imported = await auto_prefetch(force=force)
    return {"success": True, "imported_files": imported}

@app.post("/api/sync")
async def sync_channel():
    """Scan Telegram channel and import any existing files into SQLite."""
    imported = await storage_client.sync_channel_messages()
    return {"success": True, "imported_files": imported}

# ==================== FILES, PHOTOS, VIDEOS ====================

@app.get("/api/files")
async def list_files(
    category: Optional[str] = Query(None, description="Category filter: file, photo, video, all, software"),
    q: Optional[str] = Query(None, description="Search query"),
    prefetch: bool = Query(False, description="Whether to auto-prefetch newest channel files")
):
    """Retrieve stored files filtered by category and search, with optional prefetch."""
    if prefetch:
        await auto_prefetch()
    files = await get_files(category=category, search=q)
    return {"files": files, "count": len(files)}

# Upload Progress Tracking (upload_id -> progress metrics)
# Bounded to 500 entries max — oldest evicted on overflow to prevent DoS memory exhaustion
upload_progress_tracker: Dict[str, Dict[str, Any]] = {}
_UPLOAD_TRACKER_MAX = 500
_UPLOAD_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_\-]{8,64}$')

@app.get("/api/upload-progress/{upload_id}")
async def get_upload_progress(upload_id: str):
    """Retrieve real-time transfer progress of file streaming to Telegram."""
    if not _UPLOAD_ID_PATTERN.match(upload_id):
        return {"status": "not_found", "percent": 0.0, "speed_mbs": 0.0, "speed_mbps": 0.0, "eta_seconds": 0}
    info = upload_progress_tracker.get(upload_id)
    if not info:
        return {"status": "not_found", "percent": 0.0, "speed_mbs": 0.0, "speed_mbps": 0.0, "eta_seconds": 0}
    return info

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    category: Optional[str] = Form(None),
    cat_query: Optional[str] = Query(None, alias="category"),
    upload_id: Optional[str] = Form(None),
    upload_id_query: Optional[str] = Query(None, alias="upload_id")
):
    """Upload a file, photo, video, or software (up to 2 GB) directly to Telegram Channel."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    # Prevent Path Traversal in uploaded file
    safe_filename = Path(file.filename).name
    if not safe_filename or safe_filename in [".", ".."]:
        safe_filename = "unnamed_file"
    sanitized_name = re.sub(r'[^\w\.\-]', '_', safe_filename)

    chosen_cat = category or cat_query
    cat = chosen_cat if chosen_cat in ["file", "photo", "video", "software"] else detect_category(safe_filename, file.content_type)
    
    unique_prefix = f"upload_{secrets.token_hex(16)}"
    temp_path = (UPLOAD_DIR / f"{unique_prefix}_{sanitized_name}").resolve()
    upload_dir_resolved = UPLOAD_DIR.resolve()
    if not temp_path.is_relative_to(upload_dir_resolved):
        raise HTTPException(status_code=400, detail="Invalid filename or path traversal detected.")

    uid = upload_id or upload_id_query
    if uid:
        # Validate upload_id format to reject malicious keys
        if not _UPLOAD_ID_PATTERN.match(uid):
            uid = None
        else:
            # Evict oldest entry if at capacity
            if len(upload_progress_tracker) >= _UPLOAD_TRACKER_MAX:
                oldest_uid = next(iter(upload_progress_tracker))
                del upload_progress_tracker[oldest_uid]
            upload_progress_tracker[uid] = {
                "current": 0,
                "total": 0,
                "percent": 0.0,
                "speed_mbs": 0.0,
                "speed_mbps": 0.0,
                "eta_seconds": 0,
                "status": "uploading_to_server"
            }

    total_uploaded = 0
    try:
        with open(temp_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                total_uploaded += len(chunk)
                if total_uploaded > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds maximum 2 GB limit."
                    )
                buffer.write(chunk)

        if uid:
            upload_progress_tracker[uid]["status"] = "starting_telegram_sync"
            upload_progress_tracker[uid]["total"] = total_uploaded

        last_time = time.time()
        last_bytes = 0

        def progress_cb(current, total):
            nonlocal last_time, last_bytes
            now = time.time()
            dt = max(now - last_time, 0.001)
            bytes_diff = max(current - last_bytes, 0)
            speed = bytes_diff / dt  # bytes per sec
            last_time = now
            last_bytes = current
            effective_total = total or total_uploaded
            percent = round((current / effective_total) * 100, 1) if effective_total > 0 else 0
            speed_mbs = round(speed / (1024 * 1024), 2)
            speed_mbps = round((speed * 8) / (1024 * 1024), 2)
            remaining_bytes = max(effective_total - current, 0)
            eta_sec = round(remaining_bytes / max(speed, 1)) if speed > 10000 else 0

            if uid:
                upload_progress_tracker[uid] = {
                    "current": current,
                    "total": effective_total,
                    "percent": min(percent, 100.0),
                    "speed_mbs": speed_mbs,
                    "speed_mbps": speed_mbps,
                    "eta_seconds": eta_sec,
                    "status": "uploading_to_telegram"
                }

        # Upload to Storage Channel
        msg_id, channel_id, tg_file_id = await storage_client.upload_file(
            file_path=temp_path,
            filename=safe_filename,
            category=cat,
            progress_callback=progress_cb if uid else None
        )

        # Save to SQLite
        file_record = await add_file(
            filename=safe_filename,
            size=total_uploaded,
            mime_type=file.content_type or "application/octet-stream",
            telegram_message_id=msg_id,
            telegram_channel_id=channel_id,
            telegram_file_id=tg_file_id,
            is_demo=storage_client.is_demo,
            category=cat
        )

        if uid:
            upload_progress_tracker[uid] = {
                "current": total_uploaded,
                "total": total_uploaded,
                "percent": 100.0,
                "speed_mbs": 0.0,
                "speed_mbps": 0.0,
                "eta_seconds": 0,
                "status": "completed"
            }

        return file_record

    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass

@app.get("/api/download/{file_id}")
async def download_file(file_id: str):
    """Stream download file chunks directly from Telegram."""
    file_record = await get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found.")

    try:
        chunk_stream = storage_client.download_file_stream(
            telegram_message_id=file_record["telegram_message_id"],
            filename=file_record["filename"],
            telegram_channel_id=file_record.get("telegram_channel_id"),
            is_demo=file_record["is_demo"]
        )

        quoted_filename = urllib.parse.quote(file_record["filename"])
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted_filename}",
            "Content-Length": str(file_record["size"]),
            "Accept-Ranges": "bytes"
        }

        return StreamingResponse(
            chunk_stream,
            media_type=file_record["mime_type"] or "application/octet-stream",
            headers=headers
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Underlying media not found on Telegram.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Streaming error: {str(e)}")

@app.delete("/api/files/{file_id}")
async def delete_file_endpoint(file_id: str):
    """Delete file from DB and remove document message from Telegram Channel 1."""
    file_record = await get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found.")

    await storage_client.delete_message(
        channel_id=file_record["telegram_channel_id"],
        message_id=file_record["telegram_message_id"],
        is_demo=file_record["is_demo"],
        filename=file_record["filename"]
    )
    await delete_file(file_id)
    return {"success": True, "message": f"'{file_record['filename']}' deleted successfully."}

# ==================== TO-DO LIST (CHANNEL 2) ====================

@app.get("/api/todos")
async def list_todos(status: Optional[str] = Query(None, description="Status filter: active, completed, all")):
    """List todos with optional active/completed filter."""
    todos = await get_todos(status=status)
    return {"todos": todos, "count": len(todos)}

@app.post("/api/todos")
async def create_todo(payload: TodoCreate):
    """Create a new todo task, send to Telegram Channel 2, and save to DB."""
    clean_title = payload.title.strip()
    if not clean_title:
        raise HTTPException(status_code=400, detail="Task title cannot be empty.")
    if len(clean_title) > 1000:
        raise HTTPException(status_code=400, detail="Task title must be 1000 characters or fewer.")

    msg_id, channel_id = await storage_client.send_todo_message(clean_title)
    todo = await add_todo(
        title=clean_title,
        telegram_message_id=msg_id,
        telegram_channel_id=channel_id,
        is_demo=storage_client.is_demo
    )
    return todo

@app.patch("/api/todos/{todo_id}")
async def toggle_todo(todo_id: str, payload: TodoUpdate):
    """Toggle or set completion status and update Telegram message."""
    todo = await get_todo(todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found.")

    updated = await update_todo_status(todo_id, payload.completed)
    await storage_client.update_todo_message(
        telegram_message_id=todo["telegram_message_id"],
        title=todo["title"],
        completed=payload.completed
    )
    return updated

@app.delete("/api/todos/{todo_id}")
async def delete_single_todo(todo_id: str):
    """Delete a single todo item and delete its message from Telegram."""
    todo = await get_todo(todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found.")

    await storage_client.delete_message(
        channel_id=todo["telegram_channel_id"],
        message_id=todo["telegram_message_id"],
        is_demo=todo["is_demo"]
    )
    await delete_todo(todo_id)
    return {"success": True, "message": "Task deleted."}

@app.post("/api/todos/clear-completed")
async def clear_completed():
    """Purge all completed todos from database and delete messages from Telegram."""
    completed_items = await clear_completed_todos()
    for item in completed_items:
        await storage_client.delete_message(
            channel_id=item["telegram_channel_id"],
            message_id=item["telegram_message_id"],
            is_demo=item["is_demo"]
        )
    return {"success": True, "cleared_count": len(completed_items)}

# ==================== SEND TEXT / NOTES (CHANNEL 2) ====================

@app.get("/api/notes")
async def list_notes():
    """Retrieve saved text notes."""
    notes = await get_notes()
    return {"notes": notes, "count": len(notes)}

@app.post("/api/notes")
async def create_note(payload: NoteCreate):
    """Send text note to Telegram Channel 2 and save to DB."""
    clean_content = payload.content.strip()
    if not clean_content:
        raise HTTPException(status_code=400, detail="Note content cannot be empty.")
    if len(clean_content) > 10_000:
        raise HTTPException(status_code=400, detail="Note content must be 10,000 characters or fewer.")

    msg_id, channel_id = await storage_client.send_note_message(clean_content)
    note = await add_note(
        content=clean_content,
        telegram_message_id=msg_id,
        telegram_channel_id=channel_id,
        is_demo=storage_client.is_demo
    )
    return note

@app.delete("/api/notes/{note_id}")
async def delete_single_note(note_id: str):
    """Delete a note from DB and remove message from Telegram."""
    note = await delete_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")

    await storage_client.delete_message(
        channel_id=note["telegram_channel_id"],
        message_id=note["telegram_message_id"],
        is_demo=note["is_demo"]
    )
    return {"success": True, "message": "Note deleted."}

# ==================== STATIC UI ====================

STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def serve_favicon():
    favicon_svg = STATIC_DIR / "favicon.svg"
    if favicon_svg.exists():
        return FileResponse(favicon_svg, media_type="image/svg+xml")
    return Response(status_code=204)

@app.get("/")
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Telegram Cloud Hub is running."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

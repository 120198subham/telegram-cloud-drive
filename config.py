import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

def get_env_int(key: str, default: int = 0) -> int:
    val = os.getenv(key, "")
    try:
        return int(val) if val else default
    except ValueError:
        return default

# Telegram MTProto API credentials
# Obtain API_ID and API_HASH from https://my.telegram.org (under 'API development tools')
API_ID: int = get_env_int("TELEGRAM_API_ID", get_env_int("API_ID", 0))
API_HASH: str = os.getenv("TELEGRAM_API_HASH", os.getenv("API_HASH", "")).strip()

# Bot Token from @BotFather
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", os.getenv("BOT_TOKEN", "")).strip()

# The target private channel ID where files (files/photos/videos) are stored
CHANNEL_ID: int = get_env_int("TELEGRAM_CHANNEL_ID", get_env_int("CHANNEL_ID", 0))

# The target private channel ID for To-Do tasks and Text Notes
# If not provided, it falls back to CHANNEL_ID
TODO_CHANNEL_ID: int = get_env_int("TELEGRAM_TODO_CHANNEL_ID", get_env_int("TODO_CHANNEL_ID", CHANNEL_ID))

# The target private channel ID for Softwares and Installers
# If not provided, it falls back to CHANNEL_ID
SOFTWARE_CHANNEL_ID: int = get_env_int("TELEGRAM_SOFTWARE_CHANNEL_ID", get_env_int("SOFTWARE_CHANNEL_ID", CHANNEL_ID))

# The target dedicated channel ID for OTP codes and security alerts
OTP_CHANNEL_ID: int = get_env_int("TELEGRAM_OTP_CHANNEL_ID", 0)

# Telethon session file name
SESSION_NAME: str = os.getenv("TELEGRAM_SESSION_NAME", "telegram_cloud_session")

# Maximum upload limit: exactly 2 GB in bytes (Telegram standard MTProto limit)
# 2 * 1024 * 1024 * 1024 = 2,147,483,648 bytes
MAX_FILE_SIZE: int = get_env_int("MAX_FILE_SIZE", 2 * 1024 * 1024 * 1024)

# Directory configurations
UPLOAD_DIR: Path = BASE_DIR / "uploads_temp"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DEMO_STORAGE_DIR: Path = BASE_DIR / "demo_storage"
DEMO_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH: Path = BASE_DIR / "cloud_storage.db"

# Telegram Owner User ID (where OTP codes & security alerts will be sent)
# Obtain your User ID by messaging @userinfobot on Telegram
OWNER_ID: int = get_env_int("TELEGRAM_OWNER_ID", get_env_int("OWNER_ID", 0))

# OTP Authentication Security Parameters
OTP_MAX_REQUESTS_PER_MIN: int = get_env_int("OTP_MAX_REQUESTS_PER_MIN", 4)
OTP_EXPIRY_SECONDS: int = get_env_int("OTP_EXPIRY_SECONDS", 60)
OTP_MAX_ATTEMPTS: int = get_env_int("OTP_MAX_ATTEMPTS", 5)
OTP_LOCKOUT_SECONDS: int = get_env_int("OTP_LOCKOUT_SECONDS", 600)  # 10 minutes
OTP_AUTO_DELETE_SECONDS: int = get_env_int("OTP_AUTO_DELETE_SECONDS", 180)  # 3 minutes auto-remove
SESSION_COOKIE_AGE: int = get_env_int("SESSION_COOKIE_AGE", 24 * 60 * 60)  # 24 hours

def is_telegram_configured() -> bool:
    """Returns True if all required credentials are present."""
    return bool(API_ID and API_HASH and BOT_TOKEN and CHANNEL_ID != 0)

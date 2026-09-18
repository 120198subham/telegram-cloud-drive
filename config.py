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

def is_telegram_configured() -> bool:
    """Returns True if all required credentials are present."""
    return bool(API_ID and API_HASH and BOT_TOKEN and CHANNEL_ID != 0)

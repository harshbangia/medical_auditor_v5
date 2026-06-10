import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"

load_dotenv(ENV_PATH)
if not ENV_PATH.is_file():
    load_dotenv()


def env(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.getenv(key, default)

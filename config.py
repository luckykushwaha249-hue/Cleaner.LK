"""
config.py
---------
Central configuration for LLB Notes Cleaner.

IMPORTANT SECURITY NOTE:
Never hard-code real API keys in this file or in main.py.
Keys are read from environment variables at runtime.

On Android (Buildozer build), you cannot rely on OS environment
variables the way you can on a desktop. The recommended options are:

  1. (Recommended for production) Do NOT ship keys in the APK at all.
     Route all Gemini/Claude calls through your own backend/proxy server
     that holds the real keys. The app only talks to your backend.
     See the "Security" section in README.md.

  2. (OK for personal/testing builds only) Create a local file called
     `secrets.json` next to this file (it is already git-ignored) and
     this module will load values from it if the env vars are not set.
     NEVER commit secrets.json to GitHub.

  3. Inject keys at build time via GitHub Actions "Secrets" and write
     them into secrets.json during the CI build step (see
     .github/workflows/main.yml). This keeps keys out of the repo while
     still letting the CI-built APK work end-to-end for personal use.
"""

import json
import os

# ---------------------------------------------------------------------------
# Base directory (so this works both on desktop and on Android)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRETS_FILE = os.path.join(BASE_DIR, "secrets.json")


def _load_secrets_file():
    """Load optional local secrets.json (never commit this file)."""
    if os.path.exists(SECRETS_FILE):
        try:
            with open(SECRETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


_secrets = _load_secrets_file()


def _get_key(env_name):
    """Env var takes priority over secrets.json."""
    return os.environ.get(env_name) or _secrets.get(env_name, "")


# ---------------------------------------------------------------------------
# API Keys (DO NOT hard-code real values here)
# ---------------------------------------------------------------------------
GEMINI_API_KEY = _get_key("GEMINI_API_KEY")
CLAUDE_API_KEY = _get_key("CLAUDE_API_KEY")

# Optional: only needed if you want live web verification of unclear
# legal terms via a search API (e.g. Google Custom Search / Serper.dev).
# Leave blank to disable web verification (app will still work fine).
WEB_SEARCH_API_KEY = _get_key("WEB_SEARCH_API_KEY")
WEB_SEARCH_ENGINE_ID = _get_key("WEB_SEARCH_ENGINE_ID")  # for Google CSE

# ---------------------------------------------------------------------------
# Model names (update here if Google/Anthropic release newer versions)
# ---------------------------------------------------------------------------
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# ---------------------------------------------------------------------------
# App-level settings
# ---------------------------------------------------------------------------
APP_NAME = "LLB Notes Cleaner"

# Local storage paths (created at runtime under the app's private data dir)
DATA_DIR_NAME = "llb_notes_cleaner_data"
CHAT_HISTORY_FILE = "chat_history.json"
ORIGINAL_IMAGES_DIR = "originals"
CLEAN_IMAGES_DIR = "clean"

# Handwriting-style font used to render the final clean image.
# Put a .ttf file that supports Hindi (Devanagari) + English in assets/fonts/
# e.g. NotoSansDevanagari-Regular.ttf. If not found, a system default is used.
FONT_DIR = os.path.join(BASE_DIR, "assets", "fonts")
HANDWRITING_FONT_PATH = os.path.join(FONT_DIR, "NotoSansDevanagari-Regular.ttf")

# Max image dimension before we downscale for AI upload / processing
MAX_IMAGE_DIMENSION = 1600

# Whether unclear terms may be verified using a web search API
ENABLE_WEB_VERIFICATION = bool(WEB_SEARCH_API_KEY and WEB_SEARCH_ENGINE_ID)


def keys_configured():
    """Quick check used by the UI to warn the user if keys are missing."""
    return bool(GEMINI_API_KEY) and bool(CLAUDE_API_KEY)

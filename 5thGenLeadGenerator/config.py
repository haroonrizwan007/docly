"""
config.py
=========

5thGenLeadGenerator — Central Configuration

Supports:
    Phase 1
    Phase 2
    Phase 3
    Phase 4
    Phase 5 — Real AI Provider

Architecture
------------

LOCAL:
    Laptop runs Streamlit + lightweight HTTP/client operations.

COLAB:
    Google Colab runs the remote AI endpoint / application components.

AI providers:
    mock
        Local deterministic provider.
        No network.
        No API key.

    gemini
        Google Gemini generateContent API provider.
        One sequential HTTPS request per lead.
        HTTP 429/503 responses retried with exponential backoff.

Important:
    - No AI model is installed by this module.
    - No Ollama process is started by this module.
    - No GPU work is performed here.
    - Laptop performs HTTP requests only.
    - Processing remains sequential.
    - Maximum workers/concurrency are hard-limited to 1.
    - Email sending is disabled.
    - Outreach generation creates drafts only.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


# ============================================================================
# LOAD ENVIRONMENT
# ============================================================================

load_dotenv()


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _get_bool(name: str, default: bool) -> bool:
    """Safely read a boolean environment variable."""

    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _get_int(name: str, default: int) -> int:
    """Safely read an integer environment variable."""

    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    try:
        return int(value)

    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    """Safely read a float environment variable."""

    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    try:
        return float(value)

    except (TypeError, ValueError):
        return default


def _get_string(name: str, default: str = "") -> str:
    """Safely read and strip a string environment variable."""

    value = os.getenv(name)

    if value is None:
        return default

    return value.strip()


def _clamp_int(
    value: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    """Clamp an integer to a safe range."""

    value = max(value, minimum)

    if maximum is not None:
        value = min(value, maximum)

    return value


def _clamp_float(
    value: float,
    minimum: float,
    maximum: float | None = None,
) -> float:
    """Clamp a float to a safe range."""

    value = max(value, minimum)

    if maximum is not None:
        value = min(value, maximum)

    return value


# ============================================================================
# ENVIRONMENT
# ============================================================================

APP_ENV = _get_string(
    "APP_ENV",
    "local",
).lower()

if APP_ENV not in {
    "local",
    "colab",
}:
    APP_ENV = "local"

IS_COLAB = APP_ENV == "colab"


# ============================================================================
# APP ACCESS (optional password gate — used when deployed publicly, e.g.
# Streamlit Community Cloud, so the dashboard isn't wide open to anyone
# with the link). Empty string = no password required (local default).
# On Community Cloud, set this via the app's "Secrets" panel instead of
# .env — app.py checks st.secrets first, then falls back to this.
# ============================================================================

APP_PASSWORD = _get_string("APP_PASSWORD", "")


# ============================================================================
# PROJECT PATHS
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent

_DEFAULT_DATA_DIR = BASE_DIR / "data"
_DEFAULT_LOG_DIR = BASE_DIR / "logs"


# ============================================================================
# DATABASE
# ============================================================================

DATABASE_PATH = _get_string(
    "DATABASE_PATH",
    str(_DEFAULT_DATA_DIR / "app.db"),
)

# ============================================================================
# PERSISTENT DATABASE (Turso) — OPTIONAL but strongly recommended once
# deployed. Streamlit Community Cloud's local filesystem (including the
# SQLite file at DATABASE_PATH above) is NOT persistent: it is wiped on
# every redeploy and on every sleep/wake cycle. Setting these two makes
# the app connect to a free Turso (libSQL, SQLite-compatible) database
# instead, which survives both. Leave both blank for local development —
# the app falls back to the plain local SQLite file with zero changes
# needed. Get these two values from your Turso database's "Connect"
# panel (Database URL + a generated token) — set them via Streamlit's
# Secrets panel when deployed, never commit them to the repo.
TURSO_DATABASE_URL = _get_string("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN = _get_string("TURSO_AUTH_TOKEN", "")


def turso_configured() -> bool:
    return bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)


# ============================================================================
# CSV STORAGE
# ============================================================================

CSV_INPUT_DIR = _get_string(
    "CSV_INPUT_DIR",
    str(_DEFAULT_DATA_DIR / "csv_input"),
)

CSV_OUTPUT_DIR = _get_string(
    "CSV_OUTPUT_DIR",
    str(_DEFAULT_DATA_DIR / "csv_output"),
)


# ============================================================================
# LOGGING
# ============================================================================

LOG_DIR = _get_string(
    "LOG_DIR",
    str(_DEFAULT_LOG_DIR),
)

LOG_FILE = _get_string(
    "LOG_FILE",
    str(Path(LOG_DIR) / "app.log"),
)


# ============================================================================
# CREATE REQUIRED DIRECTORIES
# ============================================================================

_REQUIRED_DIRECTORIES = (
    Path(DATABASE_PATH).parent,
    Path(CSV_INPUT_DIR),
    Path(CSV_OUTPUT_DIR),
    Path(LOG_DIR),
)

for _directory in _REQUIRED_DIRECTORIES:
    try:
        _directory.mkdir(
            parents=True,
            exist_ok=True,
        )
    except OSError:
        # Do not crash configuration import because of a filesystem
        # problem. The actual component using the path can report
        # the concrete error later.
        pass


# ============================================================================
# PERFORMANCE MODE
# ============================================================================

PERFORMANCE_MODE = _get_string(
    "PERFORMANCE_MODE",
    "LOW",
).upper()

_ALLOWED_PERFORMANCE_MODES = {
    "LOW",
    "NORMAL",
}

if PERFORMANCE_MODE not in _ALLOWED_PERFORMANCE_MODES:
    PERFORMANCE_MODE = "LOW"


# ============================================================================
# WEBSITE RESEARCH SETTINGS
# ============================================================================

WEBSITE_WORKERS = _get_int(
    "WEBSITE_WORKERS",
    1,
)

MAX_CONCURRENT_TASKS = _get_int(
    "MAX_CONCURRENT_TASKS",
    1,
)

MAX_PAGES_PER_WEBSITE = _get_int(
    "MAX_PAGES_PER_WEBSITE",
    5,
)

REQUEST_DELAY_SECONDS = _get_float(
    "REQUEST_DELAY_SECONDS",
    2.0,
)

REQUEST_TIMEOUT_SECONDS = _get_int(
    "REQUEST_TIMEOUT_SECONDS",
    15,
)


# ============================================================================
# AI PERFORMANCE SETTINGS
# ============================================================================

AI_WORKERS = _get_int(
    "AI_WORKERS",
    1,
)


# ============================================================================
# HARD LOW-LOAD SAFETY CLAMPS
# ============================================================================

# The entire application is intentionally sequential.
#
# Even if .env contains:
#
#     WEBSITE_WORKERS=20
#     AI_WORKERS=10
#     MAX_CONCURRENT_TASKS=10
#
# the application will still operate at maximum 1 worker/task.

WEBSITE_WORKERS = _clamp_int(
    WEBSITE_WORKERS,
    minimum=1,
    maximum=1,
)

AI_WORKERS = _clamp_int(
    AI_WORKERS,
    minimum=1,
    maximum=1,
)

MAX_CONCURRENT_TASKS = _clamp_int(
    MAX_CONCURRENT_TASKS,
    minimum=1,
    maximum=1,
)

MAX_PAGES_PER_WEBSITE = _clamp_int(
    MAX_PAGES_PER_WEBSITE,
    minimum=1,
)

REQUEST_DELAY_SECONDS = _clamp_float(
    REQUEST_DELAY_SECONDS,
    minimum=0.0,
)

REQUEST_TIMEOUT_SECONDS = _clamp_int(
    REQUEST_TIMEOUT_SECONDS,
    minimum=1,
)


# ============================================================================
# AI PROVIDER
# ============================================================================

"""
Supported providers:

    mock
        Local deterministic provider.
        No network.
        No API key.

    gemini
        Google Gemini generateContent API provider.

        Endpoint:

            https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent

        One sequential HTTPS request per lead. HTTP 429/503 responses
        are retried with exponential backoff (see GEMINI_MAX_RETRIES).
        API key is sent as the endpoint's `key` query parameter, never
        logged, and never returned by summary()/get_ai_status().
"""


AI_PROVIDER = _get_string(
    "AI_PROVIDER",
    "gemini",
).lower()


_ALLOWED_AI_PROVIDERS = {
    "mock",
    "gemini",
}

if AI_PROVIDER not in _ALLOWED_AI_PROVIDERS:
    AI_PROVIDER = "gemini"


# ============================================================================
# GEMINI AI PROVIDER
# ============================================================================

"""
Google Gemini generateContent API configuration.

Required:

    GEMINI_API_KEY

Optional:

    GEMINI_MODEL          (default: gemini-3.6-flash)
    GEMINI_TEMPERATURE    (default: 0.7)
    GEMINI_MAX_RETRIES    (default: 3)

Get an API key from Google AI Studio and keep it in .env only --
never commit it.
"""

GEMINI_API_KEY = _get_string(
    "GEMINI_API_KEY",
    "",
)

GEMINI_MODEL = _get_string(
    "GEMINI_MODEL",
    "gemini-3.6-flash",
)

GEMINI_TEMPERATURE = _get_float(
    "GEMINI_TEMPERATURE",
    0.7,
)

GEMINI_TEMPERATURE = _clamp_float(
    GEMINI_TEMPERATURE,
    minimum=0.0,
    maximum=2.0,
)

GEMINI_MAX_RETRIES = _get_int(
    "GEMINI_MAX_RETRIES",
    3,
)

GEMINI_MAX_RETRIES = _clamp_int(
    GEMINI_MAX_RETRIES,
    minimum=0,
    maximum=8,
)


# ============================================================================
# GEMINI CONFIGURATION STATUS
# ============================================================================

GEMINI_CONFIGURED = bool(
    GEMINI_API_KEY
    and GEMINI_MODEL
)


# ============================================================================
# GOOGLE PAGESPEED INSIGHTS (OPTIONAL)
# ============================================================================

"""
Optional free Google PageSpeed Insights REST API check, run once per
lead during Website Research (mobile strategy: performance score +
Core Web Vitals).

Leave PAGESPEED_API_KEY blank to disable — research then works exactly
as before with no extra request. Get a free key from Google Cloud
Console (PageSpeed Insights API).
"""

PAGESPEED_API_KEY = _get_string(
    "PAGESPEED_API_KEY",
    "",
)

PAGESPEED_ENABLED = bool(PAGESPEED_API_KEY)


# ============================================================================
# LEAD STATUS VALUES
# ============================================================================

LEAD_STATUSES = (
    "NEW",
    "IMPORTED",
    "PENDING",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    "SKIPPED",
)


# ============================================================================
# CSV SCHEMA
# ============================================================================

CSV_REQUIRED_COLUMNS = (
    "business_name",
    "website",
)

CSV_OPTIONAL_COLUMNS = (
    "category",
    "address",
    "city",
    "country",
    "phone",
    "owner_name",
    "email",
    "linkedin",
)

CSV_ALL_COLUMNS = (
    CSV_REQUIRED_COLUMNS
    + CSV_OPTIONAL_COLUMNS
)


# ============================================================================
# OUTREACH SETTINGS
# ============================================================================

# Outreach is draft-only.
# No email is ever automatically sent.

OUTREACH_MIN_WORDS = _get_int(
    "OUTREACH_MIN_WORDS",
    40,
)

OUTREACH_TARGET_MIN_WORDS = _get_int(
    "OUTREACH_TARGET_MIN_WORDS",
    100,
)

OUTREACH_TARGET_MAX_WORDS = _get_int(
    "OUTREACH_TARGET_MAX_WORDS",
    150,
)


OUTREACH_MIN_WORDS = _clamp_int(
    OUTREACH_MIN_WORDS,
    minimum=1,
)

OUTREACH_TARGET_MIN_WORDS = _clamp_int(
    OUTREACH_TARGET_MIN_WORDS,
    minimum=1,
)

OUTREACH_TARGET_MAX_WORDS = _clamp_int(
    OUTREACH_TARGET_MAX_WORDS,
    minimum=OUTREACH_TARGET_MIN_WORDS,
)


# ============================================================================
# PHASE CONTROL
# ============================================================================

CURRENT_PHASE = "5"

PHASE_1_ENABLED = True
PHASE_2_ENABLED = True
PHASE_3_ENABLED = True
PHASE_4_ENABLED = True
PHASE_5_ENABLED = True


# ============================================================================
# SECURITY / AUTOMATION SAFETY
# ============================================================================

# Real sending stays OFF unless explicitly turned on in .env AND in the
# Docly tab's "Enable Sending" toggle (both must be true — belt and braces).
EMAIL_SENDING_ENABLED = _get_bool("EMAIL_SENDING_ENABLED", False)
SMTP_ENABLED = _get_bool("SMTP_ENABLED", False)
IMAP_ENABLED = False

WHATSAPP_AUTOMATION_ENABLED = False
LINKEDIN_AUTOMATION_ENABLED = False
CRM_INTEGRATION_ENABLED = False


# ============================================================================
# DOCLY — BUSINESS-MAIL SENDING + FOLLOW-UP SEQUENCE (Day 0 / Day 2 / Day 7)
# ============================================================================
# Fill these in with your real business mailbox to enable Docly sending.
# Gmail: use an "App Password", not your normal login password.
# Nothing sends until BOTH SMTP_ENABLED=true in .env AND the "Enable Sending"
# toggle inside the Docly tab are on.

SMTP_HOST = _get_string("SMTP_HOST", "")
SMTP_PORT = _get_int("SMTP_PORT", 587)
SMTP_USER = _get_string("SMTP_USER", "")
SMTP_PASSWORD = _get_string("SMTP_PASSWORD", "")
SMTP_FROM_NAME = _get_string("SMTP_FROM_NAME", "5thGen Technologies")
SMTP_FROM_EMAIL = _get_string("SMTP_FROM_EMAIL", SMTP_USER)
SMTP_USE_TLS = _get_bool("SMTP_USE_TLS", True)

# Safety throttle — a new/young business mailbox should not blast a large
# batch on day one (deliverability / spam risk).
DOCLY_DAILY_SEND_LIMIT = _get_int("DOCLY_DAILY_SEND_LIMIT", 30)

# How often the background scheduler thread checks for due follow-ups.
DOCLY_POLL_SECONDS = _get_int("DOCLY_POLL_SECONDS", 60)

# Follow-up timing, in days after the contact was imported (Day 0 / 2 / 7).
DOCLY_FOLLOWUP_DAY_2 = _get_int("DOCLY_FOLLOWUP_DAY_2", 2)
DOCLY_FOLLOWUP_DAY_7 = _get_int("DOCLY_FOLLOWUP_DAY_7", 7)

# ---- OPEN TRACKING -------------------------------------------------------
# A tiny invisible pixel is embedded in each Docly email. When the
# recipient's email client loads it, a hit lands on this local server and
# gets recorded as "opened".
#
# IMPORTANT: recipients are on their own devices/networks, not your
# laptop's. For the pixel to ever be reached, TRACKING_BASE_URL must be a
# PUBLIC address (e.g. an ngrok tunnel or your VPS) — a bare
# "http://localhost:8502" only works for opens on this same machine and
# will never register real prospects' opens. Leave TRACKING_ENABLED=false
# until you have a public URL.
TRACKING_ENABLED = _get_bool("TRACKING_ENABLED", False)
TRACKING_PORT = _get_int("TRACKING_PORT", 8502)
TRACKING_BASE_URL = _get_string("TRACKING_BASE_URL", "")


def docly_tracking_ready() -> bool:
    return TRACKING_ENABLED and bool(TRACKING_BASE_URL)


def docly_smtp_configured() -> bool:
    """True once host/user/password/from-email are all filled in."""
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD and SMTP_FROM_EMAIL)


# ============================================================================
# PROVIDER HELPER FUNCTIONS
# ============================================================================

def is_gemini_available() -> bool:
    """
    Return True when the Gemini provider has enough configuration
    to make a request.
    """

    return bool(
        GEMINI_API_KEY
        and GEMINI_MODEL
    )


def active_ai_provider() -> str:
    """
    Return the configured provider name.

    Actual fallback behavior is handled by ai_provider.get_provider().
    """

    return AI_PROVIDER


# ============================================================================
# SAFE CONFIG SUMMARY
# ============================================================================

def summary() -> dict:
    """
    Return safe configuration information for Streamlit.

    Secrets are NEVER returned.
    """

    return {
        "Environment": (
            "COLAB"
            if IS_COLAB
            else "LOCAL"
        ),

        "App Env": APP_ENV,

        "Current Phase": CURRENT_PHASE,

        "Performance Mode": PERFORMANCE_MODE,

        "Website Workers": WEBSITE_WORKERS,

        "AI Workers": AI_WORKERS,

        "Max Concurrent Tasks": MAX_CONCURRENT_TASKS,

        "Request Delay (s)": REQUEST_DELAY_SECONDS,

        "Request Timeout (s)": REQUEST_TIMEOUT_SECONDS,

        "Max Pages per Website": MAX_PAGES_PER_WEBSITE,

        "Database Path": DATABASE_PATH,

        "CSV Input Directory": CSV_INPUT_DIR,

        "CSV Output Directory": CSV_OUTPUT_DIR,

        "Log Directory": LOG_DIR,

        "AI Provider": AI_PROVIDER,

        "Gemini Configured": GEMINI_CONFIGURED,

        "Gemini Model": (
            GEMINI_MODEL
            if GEMINI_MODEL
            else "Not configured"
        ),

        "Gemini API Key": (
            "Configured"
            if GEMINI_API_KEY
            else "Not configured"
        ),

        "Gemini Temperature": GEMINI_TEMPERATURE,

        "Gemini Max Retries": GEMINI_MAX_RETRIES,

        "PageSpeed Insights": (
            "Enabled"
            if PAGESPEED_ENABLED
            else "Disabled (no PAGESPEED_API_KEY)"
        ),

        "Email Sending": EMAIL_SENDING_ENABLED,

        "SMTP": SMTP_ENABLED,

        "IMAP": IMAP_ENABLED,

        "WhatsApp Automation": WHATSAPP_AUTOMATION_ENABLED,

        "LinkedIn Automation": LINKEDIN_AUTOMATION_ENABLED,

        "CRM Integration": CRM_INTEGRATION_ENABLED,
    }


# ============================================================================
# AI STATUS
# ============================================================================

def get_ai_status() -> dict:
    """
    Return AI configuration status without exposing secrets.
    """

    return {
        "configured_provider": AI_PROVIDER,

        "gemini_available": is_gemini_available(),

        "gemini_model": GEMINI_MODEL or None,

        "gemini_api_key_configured": bool(
            GEMINI_API_KEY
        ),

        "gemini_temperature": GEMINI_TEMPERATURE,

        "gemini_max_retries": GEMINI_MAX_RETRIES,

        "ai_workers": AI_WORKERS,

        "max_concurrent_tasks": MAX_CONCURRENT_TASKS,
    }


# ============================================================================
# STARTUP VALIDATION
# ============================================================================

def validate_config() -> list[str]:
    """
    Return non-fatal configuration warnings.

    This function intentionally does NOT crash the application.
    """

    warnings = []

    # ------------------------------------------------------------------
    # Provider validation
    # ------------------------------------------------------------------

    if AI_PROVIDER == "gemini" and not GEMINI_CONFIGURED:

        warnings.append(
            "AI_PROVIDER=gemini but GEMINI_API_KEY is missing. "
            "The provider will fall back to mock."
        )

    # ------------------------------------------------------------------
    # Website request load
    # ------------------------------------------------------------------

    if REQUEST_DELAY_SECONDS < 1:

        warnings.append(
            "REQUEST_DELAY_SECONDS is below 1 second. "
            "This may increase website request load."
        )

    # ------------------------------------------------------------------
    # Timeout
    # ------------------------------------------------------------------

    if REQUEST_TIMEOUT_SECONDS < 10:

        warnings.append(
            "REQUEST_TIMEOUT_SECONDS is below 10 seconds. "
            "Some websites may fail before they have enough time "
            "to respond."
        )

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    if EMAIL_SENDING_ENABLED:

        warnings.append(
            "EMAIL_SENDING_ENABLED is True. "
            "This project is designed for draft-only outreach."
        )

    if SMTP_ENABLED:

        warnings.append(
            "SMTP_ENABLED is True. "
            "Automatic email sending should remain disabled."
        )

    return warnings


# ============================================================================
# MODULE-LEVEL VALIDATION
# ============================================================================

CONFIG_WARNINGS = validate_config()


# ============================================================================
# CONFIG VERSION
# ============================================================================

CONFIG_VERSION = "5.2.0"


# ============================================================================
# PUBLIC EXPORTS
# ============================================================================

__all__ = [
    # Environment
    "APP_ENV",
    "IS_COLAB",

    # Paths
    "BASE_DIR",
    "DATABASE_PATH",
    "CSV_INPUT_DIR",
    "CSV_OUTPUT_DIR",
    "LOG_DIR",
    "LOG_FILE",

    # Performance
    "PERFORMANCE_MODE",
    "WEBSITE_WORKERS",
    "AI_WORKERS",
    "MAX_CONCURRENT_TASKS",
    "MAX_PAGES_PER_WEBSITE",
    "REQUEST_DELAY_SECONDS",
    "REQUEST_TIMEOUT_SECONDS",

    # AI provider
    "AI_PROVIDER",

    # Gemini AI
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GEMINI_TEMPERATURE",
    "GEMINI_MAX_RETRIES",
    "GEMINI_CONFIGURED",

    # PageSpeed Insights
    "PAGESPEED_API_KEY",
    "PAGESPEED_ENABLED",

    # Outreach
    "OUTREACH_MIN_WORDS",
    "OUTREACH_TARGET_MIN_WORDS",
    "OUTREACH_TARGET_MAX_WORDS",

    # Lead statuses
    "LEAD_STATUSES",

    # CSV
    "CSV_REQUIRED_COLUMNS",
    "CSV_OPTIONAL_COLUMNS",
    "CSV_ALL_COLUMNS",

    # Phase
    "CURRENT_PHASE",
    "PHASE_1_ENABLED",
    "PHASE_2_ENABLED",
    "PHASE_3_ENABLED",
    "PHASE_4_ENABLED",
    "PHASE_5_ENABLED",

    # Security
    "EMAIL_SENDING_ENABLED",
    "SMTP_ENABLED",
    "IMAP_ENABLED",
    "WHATSAPP_AUTOMATION_ENABLED",
    "LINKEDIN_AUTOMATION_ENABLED",
    "CRM_INTEGRATION_ENABLED",

    # Docly
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM_NAME",
    "SMTP_FROM_EMAIL",
    "SMTP_USE_TLS",
    "DOCLY_DAILY_SEND_LIMIT",
    "DOCLY_POLL_SECONDS",
    "DOCLY_FOLLOWUP_DAY_2",
    "DOCLY_FOLLOWUP_DAY_7",
    "docly_smtp_configured",
    "TRACKING_ENABLED",
    "TRACKING_PORT",
    "TRACKING_BASE_URL",
    "docly_tracking_ready",

    # Helpers
    "is_gemini_available",
    "active_ai_provider",
    "summary",
    "get_ai_status",
    "validate_config",
    "CONFIG_WARNINGS",

    # Version
    "CONFIG_VERSION",
]
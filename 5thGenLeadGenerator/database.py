"""
database.py
============
SQLite data access layer for 5thGenLeadGenerator.

- campaigns / leads (Phase 1): duplicate detection helpers (normalized
  website / email / business_name), status tracking.
- website_research (Phase 2): one row per lead, sequential fetch results.
- lead_analysis (Phase 3): one row per lead, AI-assisted business
  analysis — kept in its own table/status so it never overwrites Phase 2
  research state.

No scraping or AI computation happens in this module — it only stores
and retrieves data that website_research.py / ai_analysis.py produce.
"""

import sqlite3
import re
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id         INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    business_name       TEXT NOT NULL,
    category            TEXT,
    address             TEXT,
    city                TEXT,
    country             TEXT,
    phone               TEXT,
    website             TEXT,
    owner_name          TEXT,
    email               TEXT,
    linkedin            TEXT,
    status              TEXT NOT NULL DEFAULT 'NEW',
    normalized_website   TEXT,
    normalized_email     TEXT,
    normalized_name      TEXT,
    error_message        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_campaign ON leads(campaign_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_norm_website ON leads(normalized_website);
CREATE INDEX IF NOT EXISTS idx_leads_norm_email ON leads(normalized_email);
CREATE INDEX IF NOT EXISTS idx_leads_norm_name ON leads(normalized_name);

-- ---------------------------------------------------------------------
-- PHASE 2 — Website Research (additive only; Phase 1 tables above are
-- untouched). One row per lead, upserted on re-run/retry so "completed"
-- research is never silently duplicated.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS website_research (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id                 INTEGER NOT NULL UNIQUE REFERENCES leads(id) ON DELETE CASCADE,
    campaign_id             INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    status                  TEXT NOT NULL DEFAULT 'PENDING',
    website_url             TEXT,
    final_url               TEXT,
    http_status             INTEGER,
    page_title              TEXT,
    meta_description        TEXT,
    business_description    TEXT,
    services_text           TEXT,
    contact_text            TEXT,
    contact_page_url        TEXT,
    location_text           TEXT,
    emails_found            TEXT,
    phones_found            TEXT,
    technology_detected     TEXT,
    pages_checked           INTEGER DEFAULT 0,
    research_started_at     TEXT,
    research_completed_at   TEXT,
    error_message           TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_research_lead ON website_research(lead_id);
CREATE INDEX IF NOT EXISTS idx_research_campaign ON website_research(campaign_id);
CREATE INDEX IF NOT EXISTS idx_research_status ON website_research(status);

-- ---------------------------------------------------------------------
-- PHASE 3 — AI Lead Analysis (additive only; Phase 1 + Phase 2 tables
-- above are untouched). One row per lead, upserted on retry/re-run so
-- a completed analysis is never silently duplicated. This table is
-- deliberately separate from leads.status (which tracks Phase 2 research
-- progress) so Phase 3 processing never overwrites Phase 2 state.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lead_analysis (
    id                                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id                             INTEGER NOT NULL UNIQUE REFERENCES leads(id) ON DELETE CASCADE,
    campaign_id                         INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    status                              TEXT NOT NULL DEFAULT 'PENDING',
    business_category                   TEXT,
    business_summary                    TEXT,
    services_detected                   TEXT,
    website_platform                    TEXT,
    website_quality_observations        TEXT,
    seo_opportunities                   TEXT,
    digital_marketing_opportunities     TEXT,
    website_development_opportunities   TEXT,
    shopify_opportunities               TEXT,
    software_development_opportunities  TEXT,
    recommended_service                 TEXT,
    secondary_services                  TEXT,
    lead_score                          INTEGER,
    lead_grade                          TEXT,
    opportunity_level                   TEXT,
    sales_angle                         TEXT,
    analysis_reason                     TEXT,
    confidence                          TEXT,
    ai_provider                         TEXT,
    ai_model                            TEXT,
    error_message                       TEXT,
    analysis_started_at                 TEXT,
    analysis_completed_at               TEXT,
    created_at                          TEXT NOT NULL,
    updated_at                          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analysis_lead ON lead_analysis(lead_id);
CREATE INDEX IF NOT EXISTS idx_analysis_campaign ON lead_analysis(campaign_id);
CREATE INDEX IF NOT EXISTS idx_analysis_status ON lead_analysis(status);

-- ---------------------------------------------------------------------
-- PHASE 4.1 — Outreach email drafts (additive only; Phase 1/2/3 tables
-- above are untouched). One row per lead, upserted on regenerate/edit so
-- a draft is never silently duplicated. GENERATE -> REVIEW -> EDIT ->
-- SAVE -> APPROVE only — no sending fields (no SMTP account, no send
-- timestamp, no provider message id) exist in this table by design.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outreach_emails (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id               INTEGER NOT NULL UNIQUE REFERENCES leads(id) ON DELETE CASCADE,
    campaign_id           INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    generation_status     TEXT NOT NULL DEFAULT 'DRAFT',
    subject               TEXT,
    subject_options       TEXT,
    email_body            TEXT,
    recommended_service   TEXT,
    confidence            TEXT,
    ai_provider           TEXT,
    ai_model              TEXT,
    error_message         TEXT,
    generated_at          TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outreach_lead ON outreach_emails(lead_id);
CREATE INDEX IF NOT EXISTS idx_outreach_campaign ON outreach_emails(campaign_id);
CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach_emails(generation_status);

-- ---------------------------------------------------------------------
-- DOCLY — CSV-import contact list + automatic Day 0 / Day 2 / Day 7
-- follow-up sequence, sent via the configured business mailbox. Fully
-- separate from campaigns/leads above so Docly never touches Phase 1-4
-- data. Duplicate contacts are prevented via UNIQUE(normalized_email).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS docly_contacts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name       TEXT,
    email               TEXT NOT NULL,
    normalized_email    TEXT NOT NULL UNIQUE,
    source_batch        TEXT,
    crm_stage           TEXT NOT NULL DEFAULT 'New',
    -- Funnel: New -> Contacted (auto, on first send) -> Opened (auto, on
    -- first tracked open) -> Replied / Interested / Meeting Booked / Won /
    -- Lost (all manual, set from the Docly tab -- the app has no reply
    -- detection, so these always require a human to mark them).
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_docly_contacts_email ON docly_contacts(normalized_email);

CREATE TABLE IF NOT EXISTS docly_sequences (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id          INTEGER NOT NULL UNIQUE REFERENCES docly_contacts(id) ON DELETE CASCADE,
    status              TEXT NOT NULL DEFAULT 'ACTIVE',   -- ACTIVE / STOPPED / COMPLETED
    current_step        INTEGER NOT NULL DEFAULT 0,       -- 0=not sent, 1=Day0 sent, 2=Day2 sent, 3=Day7 sent
    next_send_at        TEXT NOT NULL,
    last_sent_at        TEXT,
    stop_reason         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_docly_seq_status ON docly_sequences(status);
CREATE INDEX IF NOT EXISTS idx_docly_seq_next ON docly_sequences(next_send_at);

CREATE TABLE IF NOT EXISTS docly_send_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id          INTEGER NOT NULL REFERENCES docly_contacts(id) ON DELETE CASCADE,
    step                INTEGER NOT NULL,
    subject             TEXT,
    body                TEXT,
    status              TEXT NOT NULL,   -- SENT / FAILED / SKIPPED_DISABLED / SKIPPED_LIMIT
    error_message       TEXT,
    sent_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_docly_log_contact ON docly_send_log(contact_id);
CREATE INDEX IF NOT EXISTS idx_docly_log_sent_at ON docly_send_log(sent_at);

CREATE TABLE IF NOT EXISTS docly_settings (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

CREATE TABLE IF NOT EXISTS docly_tracking_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id      INTEGER NOT NULL REFERENCES docly_contacts(id) ON DELETE CASCADE,
    step            INTEGER NOT NULL,
    ip_address      TEXT,
    user_agent      TEXT,
    occurred_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_docly_tracking_contact ON docly_tracking_events(contact_id);


-- ---------------------------------------------------------------------
-- BULKREACH — large-CSV (500-1000+ leads), HTML template, paced daily
-- sending. Fully separate from Docly: own contacts/sequences/log/tracking
-- tables. Key difference from Docly: newly imported contacts start life
-- QUEUED (not immediately due) and are released N-per-day up to a
-- configurable daily limit; once released a contact follows the exact
-- same Day 0 -> Day 2 -> Day 7 automatic flow as Docly.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bulkreach_contacts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name       TEXT,
    email               TEXT NOT NULL,
    normalized_email    TEXT NOT NULL UNIQUE,
    source_batch        TEXT,
    crm_stage           TEXT NOT NULL DEFAULT 'New',
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bulkreach_contacts_email ON bulkreach_contacts(normalized_email);

CREATE TABLE IF NOT EXISTS bulkreach_sequences (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id          INTEGER NOT NULL UNIQUE REFERENCES bulkreach_contacts(id) ON DELETE CASCADE,
    status              TEXT NOT NULL DEFAULT 'QUEUED',   -- QUEUED / ACTIVE / STOPPED / COMPLETED
    current_step        INTEGER NOT NULL DEFAULT 0,       -- 0=not sent, 1=Day0 sent, 2=Day2 sent, 3=Day7 sent
    next_send_at        TEXT NOT NULL,                    -- irrelevant while QUEUED
    last_sent_at        TEXT,
    stop_reason         TEXT,
    queued_at           TEXT NOT NULL,                    -- FIFO order for daily release
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bulkreach_seq_status ON bulkreach_sequences(status);
CREATE INDEX IF NOT EXISTS idx_bulkreach_seq_next ON bulkreach_sequences(next_send_at);
CREATE INDEX IF NOT EXISTS idx_bulkreach_seq_queued ON bulkreach_sequences(queued_at);

CREATE TABLE IF NOT EXISTS bulkreach_send_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id          INTEGER NOT NULL REFERENCES bulkreach_contacts(id) ON DELETE CASCADE,
    step                INTEGER NOT NULL,
    subject             TEXT,
    body                TEXT,
    status              TEXT NOT NULL,   -- SENT / FAILED / SKIPPED_DISABLED / SKIPPED_LIMIT
    error_message       TEXT,
    sent_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bulkreach_log_contact ON bulkreach_send_log(contact_id);
CREATE INDEX IF NOT EXISTS idx_bulkreach_log_sent_at ON bulkreach_send_log(sent_at);

CREATE TABLE IF NOT EXISTS bulkreach_settings (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

CREATE TABLE IF NOT EXISTS bulkreach_tracking_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id      INTEGER NOT NULL REFERENCES bulkreach_contacts(id) ON DELETE CASCADE,
    step            INTEGER NOT NULL,
    ip_address      TEXT,
    user_agent      TEXT,
    occurred_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bulkreach_tracking_contact ON bulkreach_tracking_events(contact_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_connection():
    """Yield a SQLite connection with sane defaults, always closed after use."""
    Path(config.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables/indexes if they don't already exist. Safe to call every run."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)
    _migrate_schema()


def _migrate_schema():
    """
    Lightweight, additive migrations for databases created before a column
    existed. CREATE TABLE IF NOT EXISTS never adds columns to an existing
    table, so any new column needed on an already-deployed .db file goes
    here as an ALTER TABLE guarded by a duplicate-column check.
    """
    with get_connection() as conn:
        existing_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(docly_contacts)").fetchall()
        }
        if "crm_stage" not in existing_cols:
            conn.execute(
                "ALTER TABLE docly_contacts ADD COLUMN crm_stage TEXT NOT NULL DEFAULT 'New'"
            )


# ---------------------------------------------------------------------------
# Normalization helpers (used for duplicate detection)
# ---------------------------------------------------------------------------
def normalize_website(url: str) -> str:
    if not url:
        return ""
    url = url.strip().lower()
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    url = url.rstrip("/")
    return url


def normalize_email(email: str) -> str:
    if not email:
        return ""
    return email.strip().lower()


def normalize_business_name(name: str) -> str:
    if not name:
        return ""
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------
def create_campaign(name: str, description: str = "") -> int:
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO campaigns (name, description, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (name.strip(), description.strip(), now, now),
        )
        return cur.lastrowid


def list_campaigns():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT c.*, "
            "(SELECT COUNT(*) FROM leads l WHERE l.campaign_id = c.id) AS lead_count "
            "FROM campaigns c ORDER BY c.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_campaign(campaign_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        return dict(row) if row else None


def campaign_name_exists(name: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM campaigns WHERE lower(name) = lower(?)", (name.strip(),)
        ).fetchone()
        return row is not None


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
def get_existing_keys(campaign_id: int):
    """Return sets of normalized website/email/name already stored for a campaign,
    used for duplicate detection during CSV import."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT normalized_website, normalized_email, normalized_name "
            "FROM leads WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchall()
    websites = {r["normalized_website"] for r in rows if r["normalized_website"]}
    emails = {r["normalized_email"] for r in rows if r["normalized_email"]}
    names = {r["normalized_name"] for r in rows if r["normalized_name"]}
    return websites, emails, names


def insert_lead(campaign_id: int, row: dict, status: str = "IMPORTED") -> int:
    now = _now()
    norm_website = normalize_website(row.get("website", ""))
    norm_email = normalize_email(row.get("email", ""))
    norm_name = normalize_business_name(row.get("business_name", ""))

    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO leads (
                campaign_id, business_name, category, address, city, country,
                phone, website, owner_name, email, linkedin, status,
                normalized_website, normalized_email, normalized_name,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                row.get("business_name", "").strip(),
                row.get("category", "").strip(),
                row.get("address", "").strip(),
                row.get("city", "").strip(),
                row.get("country", "").strip(),
                row.get("phone", "").strip(),
                row.get("website", "").strip(),
                row.get("owner_name", "").strip(),
                row.get("email", "").strip(),
                row.get("linkedin", "").strip(),
                status,
                norm_website,
                norm_email,
                norm_name,
                now,
                now,
            ),
        )
        return cur.lastrowid


def bulk_insert_leads(campaign_id: int, rows: list) -> int:
    """Insert many leads in a single transaction. Returns count inserted."""
    now = _now()
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO leads (
                campaign_id, business_name, category, address, city, country,
                phone, website, owner_name, email, linkedin, status,
                normalized_website, normalized_email, normalized_name,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    campaign_id,
                    r.get("business_name", "").strip(),
                    r.get("category", "").strip(),
                    r.get("address", "").strip(),
                    r.get("city", "").strip(),
                    r.get("country", "").strip(),
                    r.get("phone", "").strip(),
                    r.get("website", "").strip(),
                    r.get("owner_name", "").strip(),
                    r.get("email", "").strip(),
                    r.get("linkedin", "").strip(),
                    "IMPORTED",
                    normalize_website(r.get("website", "")),
                    normalize_email(r.get("email", "")),
                    normalize_business_name(r.get("business_name", "")),
                    now,
                    now,
                )
                for r in rows
            ],
        )
    return len(rows)


def list_leads(campaign_id: int, status: str = None, limit: int = 500):
    query = "SELECT * FROM leads WHERE campaign_id = ?"
    params = [campaign_id]
    if status and status != "ALL":
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def count_leads_by_status(campaign_id: int) -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM leads WHERE campaign_id = ? "
            "GROUP BY status",
            (campaign_id,),
        ).fetchall()
    counts = {status: 0 for status in config.LEAD_STATUSES}
    for r in rows:
        counts[r["status"]] = r["cnt"]
    return counts


def update_lead_status(lead_id: int, status: str, error_message: str = None):
    with get_connection() as conn:
        conn.execute(
            "UPDATE leads SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (status, error_message, _now(), lead_id),
        )


def get_lead(lead_id: int):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Website Research — Phase 2
# ---------------------------------------------------------------------------
def get_leads_eligible_for_research(campaign_id: int, include_failed: bool = False):
    """Leads with a non-blank website that haven't completed research yet
    (or have failed, if include_failed=True). COMPLETED/SKIPPED/PROCESSING
    leads are excluded so a normal Start never re-researches them."""
    statuses = ["NEW", "IMPORTED", "PENDING"]
    if include_failed:
        statuses.append("FAILED")
    placeholders = ",".join("?" for _ in statuses)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM leads
            WHERE campaign_id = ?
              AND TRIM(website) != ''
              AND status IN ({placeholders})
            ORDER BY id ASC
            """,
            [campaign_id, *statuses],
        ).fetchall()
        return [dict(r) for r in rows]


def set_research_status(campaign_id: int, lead_id: int, website_url: str, status: str):
    """Ensure a website_research row exists for this lead and set its status.
    Used to mark PROCESSING before a fetch starts, and PENDING again if a
    run is stopped mid-lead so the next Start can pick it back up."""
    now = _now()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM website_research WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE website_research SET status = ?, updated_at = ? WHERE lead_id = ?",
                (status, now, lead_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO website_research (
                    lead_id, campaign_id, status, website_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (lead_id, campaign_id, status, website_url, now, now),
            )


def save_research_result(lead_id: int, campaign_id: int, result: dict):
    """Upsert the full research result for a lead (one row per lead)."""
    now = _now()
    common = (
        result.get("status", "FAILED"),
        result.get("website_url"),
        result.get("final_url"),
        result.get("http_status"),
        result.get("page_title"),
        result.get("meta_description"),
        result.get("business_description"),
        result.get("services_text"),
        result.get("contact_text"),
        result.get("contact_url"),
        result.get("location_text"),
        json.dumps(result.get("emails_found") or []),
        json.dumps(result.get("phones_found") or []),
        json.dumps(result.get("technology_detected") or []),
        result.get("pages_checked", 0),
        result.get("research_started_at"),
        result.get("research_completed_at"),
        result.get("error_message"),
        now,
    )
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM website_research WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE website_research SET
                    status = ?, website_url = ?, final_url = ?, http_status = ?,
                    page_title = ?, meta_description = ?, business_description = ?,
                    services_text = ?, contact_text = ?, contact_page_url = ?,
                    location_text = ?, emails_found = ?, phones_found = ?,
                    technology_detected = ?, pages_checked = ?,
                    research_started_at = ?, research_completed_at = ?,
                    error_message = ?, updated_at = ?
                WHERE lead_id = ?
                """,
                common + (lead_id,),
            )
        else:
            conn.execute(
                """
                INSERT INTO website_research (
                    status, website_url, final_url, http_status, page_title,
                    meta_description, business_description, services_text,
                    contact_text, contact_page_url, location_text, emails_found,
                    phones_found, technology_detected, pages_checked,
                    research_started_at, research_completed_at, error_message,
                    updated_at, lead_id, campaign_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                common + (lead_id, campaign_id, now),
            )


def count_research_by_status(campaign_id: int) -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM website_research "
            "WHERE campaign_id = ? GROUP BY status",
            (campaign_id,),
        ).fetchall()
    counts = {s: 0 for s in ("PENDING", "PROCESSING", "COMPLETED", "FAILED", "SKIPPED")}
    for r in rows:
        counts[r["status"]] = r["cnt"]
    return counts


def research_summary_counts(campaign_id: int) -> dict:
    """High-level numbers for the Phase 2 dashboard section."""
    with get_connection() as conn:
        total_leads = conn.execute(
            "SELECT COUNT(*) AS c FROM leads WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()["c"]
        with_website = conn.execute(
            "SELECT COUNT(*) AS c FROM leads WHERE campaign_id = ? AND TRIM(website) != ''",
            (campaign_id,),
        ).fetchone()["c"]
    summary = {"total_leads": total_leads, "with_website": with_website}
    summary.update(count_research_by_status(campaign_id))
    return summary


def list_research(campaign_id: int, status: str = None, limit: int = 200):
    query = """
        SELECT wr.*, l.business_name
        FROM website_research wr
        JOIN leads l ON l.id = wr.lead_id
        WHERE wr.campaign_id = ?
    """
    params = [campaign_id]
    if status and status != "ALL":
        query += " AND wr.status = ?"
        params.append(status)
    query += " ORDER BY wr.updated_at DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def reset_leads_for_rerun(lead_ids: list):
    """Used by 'Re-run Selected': puts leads (and their existing research
    row, if any) back to PENDING so the next Start picks them up again."""
    if not lead_ids:
        return
    now = _now()
    placeholders = ",".join("?" for _ in lead_ids)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE leads SET status = 'PENDING', updated_at = ? WHERE id IN ({placeholders})",
            [now, *lead_ids],
        )
        conn.execute(
            f"UPDATE website_research SET status = 'PENDING', updated_at = ? "
            f"WHERE lead_id IN ({placeholders})",
            [now, *lead_ids],
        )


# ---------------------------------------------------------------------------
# AI Lead Analysis — Phase 3
# ---------------------------------------------------------------------------
def get_leads_eligible_for_analysis(campaign_id: int, include_failed: bool = False):
    """Leads that have a Phase 2 website_research row (any research
    outcome — COMPLETED, FAILED, or SKIPPED all carry useful signal for
    Phase 3, e.g. an unreachable site is itself a Website Development
    opportunity) and don't already have a COMPLETED/PROCESSING analysis.
    FAILED analyses are only included when include_failed=True, matching
    the Website Research tab's Retry Failed pattern."""
    statuses = ["PENDING"]
    if include_failed:
        statuses.append("FAILED")
    placeholders = ",".join("?" for _ in statuses)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT l.*, wr.status AS research_status
            FROM leads l
            JOIN website_research wr ON wr.lead_id = l.id
            LEFT JOIN lead_analysis la ON la.lead_id = l.id
            WHERE l.campaign_id = ?
              AND (la.id IS NULL OR la.status IN ({placeholders}))
            ORDER BY l.id ASC
            """,
            [campaign_id, *statuses],
        ).fetchall()
        return [dict(r) for r in rows]


def set_analysis_status(campaign_id: int, lead_id: int, status: str):
    """Ensure a lead_analysis row exists for this lead and set its status.
    Used to mark PROCESSING before analysis starts, and PENDING again if a
    run is stopped mid-lead so the next Start can pick it back up."""
    now = _now()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM lead_analysis WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE lead_analysis SET status = ?, updated_at = ? WHERE lead_id = ?",
                (status, now, lead_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO lead_analysis (
                    lead_id, campaign_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (lead_id, campaign_id, status, now, now),
            )


def save_analysis_result(lead_id: int, campaign_id: int, result: dict):
    """Upsert the full Phase 3 analysis for a lead (one row per lead)."""
    now = _now()
    common = (
        result.get("status", "FAILED"),
        result.get("business_category"),
        result.get("business_summary"),
        json.dumps(result.get("services_detected") or []),
        result.get("website_platform"),
        result.get("website_quality_observations"),
        result.get("seo_opportunities"),
        result.get("digital_marketing_opportunities"),
        result.get("website_development_opportunities"),
        result.get("shopify_opportunities"),
        result.get("software_development_opportunities"),
        result.get("recommended_service"),
        json.dumps(result.get("secondary_services") or []),
        result.get("lead_score"),
        result.get("lead_grade"),
        result.get("opportunity_level"),
        result.get("sales_angle"),
        result.get("analysis_reason"),
        result.get("confidence"),
        result.get("ai_provider"),
        result.get("ai_model"),
        result.get("error_message"),
        result.get("analysis_started_at"),
        result.get("analysis_completed_at"),
        now,
    )
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM lead_analysis WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE lead_analysis SET
                    status = ?, business_category = ?, business_summary = ?,
                    services_detected = ?, website_platform = ?,
                    website_quality_observations = ?, seo_opportunities = ?,
                    digital_marketing_opportunities = ?,
                    website_development_opportunities = ?, shopify_opportunities = ?,
                    software_development_opportunities = ?, recommended_service = ?,
                    secondary_services = ?, lead_score = ?, lead_grade = ?,
                    opportunity_level = ?, sales_angle = ?, analysis_reason = ?,
                    confidence = ?, ai_provider = ?, ai_model = ?, error_message = ?,
                    analysis_started_at = ?, analysis_completed_at = ?, updated_at = ?
                WHERE lead_id = ?
                """,
                common + (lead_id,),
            )
        else:
            conn.execute(
                """
                INSERT INTO lead_analysis (
                    status, business_category, business_summary, services_detected,
                    website_platform, website_quality_observations, seo_opportunities,
                    digital_marketing_opportunities, website_development_opportunities,
                    shopify_opportunities, software_development_opportunities,
                    recommended_service, secondary_services, lead_score, lead_grade,
                    opportunity_level, sales_angle, analysis_reason, confidence,
                    ai_provider, ai_model, error_message, analysis_started_at,
                    analysis_completed_at, updated_at, lead_id, campaign_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                common + (lead_id, campaign_id, now),
            )


def count_analysis_by_status(campaign_id: int) -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM lead_analysis "
            "WHERE campaign_id = ? GROUP BY status",
            (campaign_id,),
        ).fetchall()
    counts = {s: 0 for s in ("PENDING", "PROCESSING", "COMPLETED", "FAILED", "SKIPPED")}
    for r in rows:
        counts[r["status"]] = r["cnt"]
    return counts


def analysis_summary_counts(campaign_id: int) -> dict:
    """High-level numbers for the Phase 3 dashboard section."""
    with get_connection() as conn:
        eligible_total = conn.execute(
            """
            SELECT COUNT(*) AS c FROM leads l
            JOIN website_research wr ON wr.lead_id = l.id
            WHERE l.campaign_id = ?
            """,
            (campaign_id,),
        ).fetchone()["c"]
    summary = {"total_eligible": eligible_total}
    summary.update(count_analysis_by_status(campaign_id))
    return summary


def list_analysis(campaign_id: int, status: str = None, limit: int = 200):
    query = """
        SELECT la.*, l.business_name, l.website
        FROM lead_analysis la
        JOIN leads l ON l.id = la.lead_id
        WHERE la.campaign_id = ?
    """
    params = [campaign_id]
    if status and status != "ALL":
        query += " AND la.status = ?"
        params.append(status)
    query += " ORDER BY la.updated_at DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_analysis(lead_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM lead_analysis WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        return dict(row) if row else None


def get_research(lead_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM website_research WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        return dict(row) if row else None


def reset_leads_for_analysis_rerun(lead_ids: list):
    """Used by AI Lead Analysis 'Re-run Selected': puts the analysis row
    back to PENDING so the next Start picks it up again. Does NOT touch
    leads.status or website_research — Phase 2 state is untouched."""
    if not lead_ids:
        return
    now = _now()
    placeholders = ",".join("?" for _ in lead_ids)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE lead_analysis SET status = 'PENDING', updated_at = ? "
            f"WHERE lead_id IN ({placeholders})",
            [now, *lead_ids],
        )


# ---------------------------------------------------------------------------
# AI Outreach — Phase 4.1
# ---------------------------------------------------------------------------
def get_leads_eligible_for_outreach(campaign_id: int):
    """Leads with a COMPLETED Phase 3 analysis — including recommended_
    service == 'Needs Review', since generating a draft for those still
    produces a useful, explicit NEEDS_REVIEW outreach record rather than
    silently skipping the lead. Does not require the lead to lack a draft
    already — re-selecting a lead here is how 'Regenerate' is offered."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT l.*, la.recommended_service, la.confidence AS analysis_confidence,
                   la.status AS analysis_status
            FROM leads l
            JOIN lead_analysis la ON la.lead_id = l.id
            WHERE l.campaign_id = ? AND la.status = 'COMPLETED'
            ORDER BY l.id ASC
            """,
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def save_outreach_draft(lead_id: int, campaign_id: int, result: dict):
    """Upsert the Phase 4 outreach draft for a lead (one row per lead).
    Called by both initial Generate and Regenerate — Regenerate always
    overwrites the previous draft's generated content (subject/body),
    matching the spec's 'regenerate using the SAME stored research/
    analysis data, never re-scrape' requirement."""
    now = _now()
    common = (
        result.get("generation_status", "NEEDS_REVIEW"),
        result.get("subject"),
        json.dumps(result.get("subject_options") or []),
        result.get("email_body"),
        result.get("recommended_service"),
        result.get("confidence"),
        result.get("ai_provider"),
        result.get("ai_model"),
        result.get("error_message"),
        result.get("generated_at"),
        now,
    )
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM outreach_emails WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE outreach_emails SET
                    generation_status = ?, subject = ?, subject_options = ?, email_body = ?,
                    recommended_service = ?, confidence = ?, ai_provider = ?, ai_model = ?,
                    error_message = ?, generated_at = ?, updated_at = ?
                WHERE lead_id = ?
                """,
                common + (lead_id,),
            )
        else:
            conn.execute(
                """
                INSERT INTO outreach_emails (
                    generation_status, subject, subject_options, email_body,
                    recommended_service, confidence, ai_provider, ai_model,
                    error_message, generated_at, updated_at, lead_id, campaign_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                common + (lead_id, campaign_id, now),
            )


def update_outreach_content(lead_id: int, subject: str, email_body: str):
    """Manual 'Save Draft' after editing — updates only the human-edited
    text, not the generation metadata (ai_provider/confidence/etc. stay
    as a record of what generated the original draft)."""
    now = _now()
    with get_connection() as conn:
        conn.execute(
            "UPDATE outreach_emails SET subject = ?, email_body = ?, updated_at = ? WHERE lead_id = ?",
            (subject, email_body, now, lead_id),
        )


def update_outreach_status(lead_id: int, generation_status: str):
    """Mark Approved / Rejected (or back to DRAFT) — this is a review
    decision, never a send action; no email leaves the system at this
    stage regardless of status."""
    now = _now()
    with get_connection() as conn:
        conn.execute(
            "UPDATE outreach_emails SET generation_status = ?, updated_at = ? WHERE lead_id = ?",
            (generation_status, now, lead_id),
        )


def outreach_summary_counts(campaign_id: int) -> dict:
    with get_connection() as conn:
        eligible_total = conn.execute(
            "SELECT COUNT(*) AS c FROM lead_analysis WHERE campaign_id = ? AND status = 'COMPLETED'",
            (campaign_id,),
        ).fetchone()["c"]
        rows = conn.execute(
            "SELECT generation_status, COUNT(*) AS cnt FROM outreach_emails "
            "WHERE campaign_id = ? GROUP BY generation_status",
            (campaign_id,),
        ).fetchall()
    counts = {s: 0 for s in ("DRAFT", "APPROVED", "REJECTED", "NEEDS_REVIEW")}
    for r in rows:
        counts[r["generation_status"]] = r["cnt"]
    counts["total_eligible"] = eligible_total
    return counts


def list_outreach(campaign_id: int, status: str = None, limit: int = 200):
    query = """
        SELECT oe.*, l.business_name, l.website
        FROM outreach_emails oe
        JOIN leads l ON l.id = oe.lead_id
        WHERE oe.campaign_id = ?
    """
    params = [campaign_id]
    if status and status != "ALL":
        query += " AND oe.generation_status = ?"
        params.append(status)
    query += " ORDER BY oe.updated_at DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_outreach(lead_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM outreach_emails WHERE lead_id = ?", (lead_id,)
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# DOCLY — contacts, follow-up sequence, send log, settings
# ---------------------------------------------------------------------------

def docly_get_setting(key: str, default=None):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM docly_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def docly_set_setting(key: str, value: str):
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO docly_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def docly_sending_enabled() -> bool:
    """The Docly-tab toggle. SMTP_ENABLED in .env is checked separately."""
    return docly_get_setting("sending_enabled", "false") == "true"


def docly_set_sending_enabled(enabled: bool):
    docly_set_setting("sending_enabled", "true" if enabled else "false")


def docly_get_template(step: int, default: str = "") -> str:
    return docly_get_setting(f"template_step_{step}", default)


def docly_set_template(step: int, text: str):
    docly_set_setting(f"template_step_{step}", text)


DOCLY_CRM_STAGES = [
    "New", "Contacted", "Opened", "Replied",
    "Interested", "Meeting Booked", "Won", "Lost",
]


def docly_set_crm_stage(contact_id: int, stage: str):
    """Manual (or auto) funnel-stage update for one contact."""
    if stage not in DOCLY_CRM_STAGES:
        raise ValueError(f"Unknown CRM stage: {stage!r}")
    with get_connection() as conn:
        conn.execute(
            "UPDATE docly_contacts SET crm_stage = ? WHERE id = ?",
            (stage, contact_id),
        )


def _docly_auto_advance_stage(conn, contact_id: int, at_least: str):
    """
    Bump crm_stage forward to `at_least` only if the contact hasn't already
    progressed past it manually (e.g. don't downgrade 'Interested' back to
    'Contacted' just because another follow-up went out).
    """
    order = DOCLY_CRM_STAGES
    row = conn.execute(
        "SELECT crm_stage FROM docly_contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    current = row["crm_stage"] if row else "New"
    if order.index(current) < order.index(at_least):
        conn.execute(
            "UPDATE docly_contacts SET crm_stage = ? WHERE id = ?",
            (at_least, contact_id),
        )


def docly_get_crm_stage_counts() -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT crm_stage, COUNT(*) AS cnt FROM docly_contacts GROUP BY crm_stage"
        ).fetchall()
    counts = {stage: 0 for stage in DOCLY_CRM_STAGES}
    for r in rows:
        counts[r["crm_stage"]] = r["cnt"]
    return counts


def docly_import_contacts(rows: list[dict], source_batch: str = "") -> dict:
    """
    rows: list of {"business_name": ..., "email": ...}
    Skips duplicates (by normalized email) against contacts already
    imported in any previous batch. Creates an ACTIVE sequence (due
    immediately, i.e. Day 0) for every newly-added contact.
    Returns {"imported": N, "duplicates_skipped": N, "invalid_skipped": N}.
    """
    imported = 0
    duplicates_skipped = 0
    invalid_skipped = 0
    now = _now()

    with get_connection() as conn:
        for row in rows:
            email = (row.get("email") or "").strip()
            business_name = (row.get("business_name") or "").strip()
            norm_email = normalize_email(email)

            if not norm_email or "@" not in norm_email:
                invalid_skipped += 1
                continue

            existing = conn.execute(
                "SELECT id FROM docly_contacts WHERE normalized_email = ?",
                (norm_email,),
            ).fetchone()
            if existing:
                duplicates_skipped += 1
                continue

            cur = conn.execute(
                """
                INSERT INTO docly_contacts
                    (business_name, email, normalized_email, source_batch, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (business_name, email, norm_email, source_batch, now),
            )
            contact_id = cur.lastrowid

            conn.execute(
                """
                INSERT INTO docly_sequences
                    (contact_id, status, current_step, next_send_at, created_at, updated_at)
                VALUES (?, 'ACTIVE', 0, ?, ?, ?)
                """,
                (contact_id, now, now, now),
            )
            imported += 1

    return {
        "imported": imported,
        "duplicates_skipped": duplicates_skipped,
        "invalid_skipped": invalid_skipped,
    }


def docly_get_due_sequences(limit: int = 50) -> list[dict]:
    """ACTIVE sequences whose next_send_at has arrived, oldest first."""
    now = _now()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.*, c.business_name, c.email, c.normalized_email
            FROM docly_sequences s
            JOIN docly_contacts c ON c.id = s.contact_id
            WHERE s.status = 'ACTIVE' AND s.next_send_at <= ?
            ORDER BY s.next_send_at ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def docly_sent_today_count() -> int:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM docly_send_log
            WHERE status = 'SENT' AND date(sent_at) = date('now')
            """
        ).fetchone()
        return row["n"] if row else 0


def docly_log_send(contact_id: int, step: int, subject: str, body: str,
                    status: str, error_message: str = None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO docly_send_log
                (contact_id, step, subject, body, status, error_message, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (contact_id, step, subject, body, status, error_message, _now()),
        )
        if status == "SENT":
            _docly_auto_advance_stage(conn, contact_id, "Contacted")


def docly_advance_sequence(contact_id: int, new_step: int, next_send_at: str,
                            status: str = "ACTIVE"):
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE docly_sequences
            SET current_step = ?, next_send_at = ?, status = ?,
                last_sent_at = ?, updated_at = ?
            WHERE contact_id = ?
            """,
            (new_step, next_send_at, status, now, now, contact_id),
        )


def docly_stop_sequence(contact_id: int, reason: str = "Manually stopped"):
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE docly_sequences
            SET status = 'STOPPED', stop_reason = ?, updated_at = ?
            WHERE contact_id = ?
            """,
            (reason, now, contact_id),
        )


def docly_get_all_sequences() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.*, c.business_name, c.email, c.crm_stage
            FROM docly_sequences s
            JOIN docly_contacts c ON c.id = s.contact_id
            ORDER BY s.updated_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def docly_get_contact_by_email(email: str):
    norm = normalize_email(email)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM docly_contacts WHERE normalized_email = ?", (norm,)
        ).fetchone()
        return dict(row) if row else None


def docly_get_recent_log(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT l.*, c.business_name, c.email
            FROM docly_send_log l
            JOIN docly_contacts c ON c.id = l.contact_id
            ORDER BY l.sent_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# DOCLY — open tracking
# ---------------------------------------------------------------------------

def docly_record_open(contact_id: int, step: int, ip_address: str = None,
                       user_agent: str = None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO docly_tracking_events
                (contact_id, step, ip_address, user_agent, occurred_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (contact_id, step, ip_address, user_agent, _now()),
        )
        _docly_auto_advance_stage(conn, contact_id, "Opened")


def docly_get_open_status() -> dict:
    """{contact_id: last_opened_at_iso_string} for every contact with >=1 open."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT contact_id, MAX(occurred_at) AS last_opened_at
            FROM docly_tracking_events
            GROUP BY contact_id
            """
        ).fetchall()
        return {r["contact_id"]: r["last_opened_at"] for r in rows}


# ---------------------------------------------------------------------------
# REPORTING — Phase 1-4 campaign pipeline funnel (read-only aggregation,
# no new tables: reuses the per-phase summary counts already tracked)
# ---------------------------------------------------------------------------
def reporting_pipeline_funnel(campaign_id: int) -> dict:
    """
    One row per phase for a campaign: how many leads reached / completed
    that stage. Used by the Reporting tab's funnel chart.
    """
    lead_counts = count_leads_by_status(campaign_id)
    research_counts = research_summary_counts(campaign_id)
    analysis_counts = analysis_summary_counts(campaign_id)
    outreach_counts = outreach_summary_counts(campaign_id)

    imported = sum(lead_counts.values())
    researched = research_counts.get("COMPLETED", 0)
    analyzed = analysis_counts.get("COMPLETED", 0)
    drafted = sum(
        v for k, v in outreach_counts.items() if k in ("DRAFT", "APPROVED")
    )
    approved = outreach_counts.get("APPROVED", 0)

    return {
        "imported": imported,
        "researched": researched,
        "analyzed": analyzed,
        "drafted": drafted,
        "approved": approved,
    }


def reporting_docly_overview() -> dict:
    """Sent / opened / funnel-stage counts across all Docly contacts."""
    with get_connection() as conn:
        sent = conn.execute(
            "SELECT COUNT(*) AS n FROM docly_send_log WHERE status = 'SENT'"
        ).fetchone()["n"]
        opened_contacts = conn.execute(
            "SELECT COUNT(DISTINCT contact_id) AS n FROM docly_tracking_events"
        ).fetchone()["n"]
        total_contacts = conn.execute(
            "SELECT COUNT(*) AS n FROM docly_contacts"
        ).fetchone()["n"]
        by_step = conn.execute(
            """
            SELECT step, COUNT(*) AS sent_cnt
            FROM docly_send_log WHERE status = 'SENT' GROUP BY step
            """
        ).fetchall()
        opens_by_step = conn.execute(
            "SELECT step, COUNT(*) AS open_cnt FROM docly_tracking_events GROUP BY step"
        ).fetchall()

    step_stats = {r["step"]: {"sent": r["sent_cnt"], "opened": 0} for r in by_step}
    for r in opens_by_step:
        step_stats.setdefault(r["step"], {"sent": 0, "opened": 0})
        step_stats[r["step"]]["opened"] = r["open_cnt"]

    return {
        "total_contacts": total_contacts,
        "total_sent": sent,
        "unique_opened_contacts": opened_contacts,
        "stage_counts": docly_get_crm_stage_counts(),
        "step_stats": step_stats,
    }


def docly_get_contact_by_id(contact_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM docly_contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# BULKREACH — settings / templates (subject stored separately from the
# HTML body so a full pasted HTML document never has to double as a
# subject line, unlike Docly's plain-text "first line = subject" trick).
# ---------------------------------------------------------------------------
def bulkreach_get_setting(key: str, default=None):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM bulkreach_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def bulkreach_set_setting(key: str, value: str):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO bulkreach_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def bulkreach_sending_enabled() -> bool:
    return bulkreach_get_setting("sending_enabled", "false") == "true"


def bulkreach_set_sending_enabled(enabled: bool):
    bulkreach_set_setting("sending_enabled", "true" if enabled else "false")


def bulkreach_get_daily_limit() -> int:
    try:
        return int(bulkreach_get_setting("daily_limit", "30"))
    except (TypeError, ValueError):
        return 30


def bulkreach_set_daily_limit(n: int):
    bulkreach_set_setting("daily_limit", str(max(1, int(n))))


def bulkreach_get_template(step: int, default: str = "") -> str:
    return bulkreach_get_setting(f"template_step_{step}", default)


def bulkreach_set_template(step: int, html: str):
    bulkreach_set_setting(f"template_step_{step}", html)


def bulkreach_get_subject(step: int, default: str = "") -> str:
    return bulkreach_get_setting(f"subject_step_{step}", default)


def bulkreach_set_subject(step: int, text: str):
    bulkreach_set_setting(f"subject_step_{step}", text)


BULKREACH_CRM_STAGES = [
    "New", "Contacted", "Opened", "Replied",
    "Interested", "Meeting Booked", "Won", "Lost",
]


def bulkreach_set_crm_stage(contact_id: int, stage: str):
    if stage not in BULKREACH_CRM_STAGES:
        raise ValueError(f"Unknown CRM stage: {stage!r}")
    with get_connection() as conn:
        conn.execute(
            "UPDATE bulkreach_contacts SET crm_stage = ? WHERE id = ?",
            (stage, contact_id),
        )


def _bulkreach_auto_advance_stage(conn, contact_id: int, at_least: str):
    order = BULKREACH_CRM_STAGES
    row = conn.execute(
        "SELECT crm_stage FROM bulkreach_contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    current = row["crm_stage"] if row else "New"
    if order.index(current) < order.index(at_least):
        conn.execute(
            "UPDATE bulkreach_contacts SET crm_stage = ? WHERE id = ?",
            (at_least, contact_id),
        )


def bulkreach_get_crm_stage_counts() -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT crm_stage, COUNT(*) AS cnt FROM bulkreach_contacts GROUP BY crm_stage"
        ).fetchall()
    counts = {stage: 0 for stage in BULKREACH_CRM_STAGES}
    for r in rows:
        counts[r["crm_stage"]] = r["cnt"]
    return counts


# ---------------------------------------------------------------------------
# BULKREACH — import (contacts start QUEUED, not immediately due)
# ---------------------------------------------------------------------------
def bulkreach_import_contacts(rows: list[dict], source_batch: str = "") -> dict:
    """
    Same dedup rules as Docly, but every newly-added contact's sequence
    starts life QUEUED (not ACTIVE) — it only starts receiving Day 0 once
    bulkreach_release_queue_batch() releases it, which the scheduler does
    N-per-day up to the configured daily limit.
    """
    imported = 0
    duplicates_skipped = 0
    invalid_skipped = 0
    now = _now()

    with get_connection() as conn:
        for row in rows:
            email = (row.get("email") or "").strip()
            business_name = (row.get("business_name") or "").strip()
            norm_email = normalize_email(email)

            if not norm_email or "@" not in norm_email:
                invalid_skipped += 1
                continue

            existing = conn.execute(
                "SELECT id FROM bulkreach_contacts WHERE normalized_email = ?",
                (norm_email,),
            ).fetchone()
            if existing:
                duplicates_skipped += 1
                continue

            cur = conn.execute(
                """
                INSERT INTO bulkreach_contacts
                    (business_name, email, normalized_email, source_batch, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (business_name, email, norm_email, source_batch, now),
            )
            contact_id = cur.lastrowid

            conn.execute(
                """
                INSERT INTO bulkreach_sequences
                    (contact_id, status, current_step, next_send_at,
                     queued_at, created_at, updated_at)
                VALUES (?, 'QUEUED', 0, ?, ?, ?, ?)
                """,
                (contact_id, now, now, now, now),
            )
            imported += 1

    return {
        "imported": imported,
        "duplicates_skipped": duplicates_skipped,
        "invalid_skipped": invalid_skipped,
    }


def bulkreach_get_queued_count() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM bulkreach_sequences WHERE status = 'QUEUED'"
        ).fetchone()
        return row["n"] if row else 0


def bulkreach_release_queue_batch(n: int) -> list[dict]:
    """
    Move up to n oldest-queued sequences to ACTIVE with next_send_at=now,
    so the very next due-check picks them up as a Day 0 send. Returns the
    released rows (joined with contact info) so the caller can send them
    in the same pass without a second query.
    """
    if n <= 0:
        return []
    now = _now()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.id AS seq_id, s.contact_id
            FROM bulkreach_sequences s
            WHERE s.status = 'QUEUED'
            ORDER BY s.queued_at ASC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
        ids = [r["seq_id"] for r in rows]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"""
            UPDATE bulkreach_sequences
            SET status = 'ACTIVE', next_send_at = ?, updated_at = ?
            WHERE id IN ({placeholders})
            """,
            (now, now, *ids),
        )
        released = conn.execute(
            f"""
            SELECT s.*, c.business_name, c.email, c.normalized_email
            FROM bulkreach_sequences s
            JOIN bulkreach_contacts c ON c.id = s.contact_id
            WHERE s.id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        return [dict(r) for r in released]


def bulkreach_get_due_sequences(limit: int = 50) -> list[dict]:
    """ACTIVE sequences whose next_send_at has arrived (follow-ups, and
    anything just released this run), oldest first. Never includes QUEUED."""
    now = _now()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.*, c.business_name, c.email, c.normalized_email
            FROM bulkreach_sequences s
            JOIN bulkreach_contacts c ON c.id = s.contact_id
            WHERE s.status = 'ACTIVE' AND s.next_send_at <= ?
            ORDER BY s.next_send_at ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def bulkreach_sent_today_count() -> int:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM bulkreach_send_log
            WHERE status = 'SENT' AND date(sent_at) = date('now')
            """
        ).fetchone()
        return row["n"] if row else 0


def bulkreach_log_send(contact_id: int, step: int, subject: str, body: str,
                        status: str, error_message: str = None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO bulkreach_send_log
                (contact_id, step, subject, body, status, error_message, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (contact_id, step, subject, body, status, error_message, _now()),
        )
        if status == "SENT":
            _bulkreach_auto_advance_stage(conn, contact_id, "Contacted")


def bulkreach_advance_sequence(contact_id: int, new_step: int, next_send_at: str,
                                status: str = "ACTIVE"):
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE bulkreach_sequences
            SET current_step = ?, next_send_at = ?, status = ?,
                last_sent_at = ?, updated_at = ?
            WHERE contact_id = ?
            """,
            (new_step, next_send_at, status, now, now, contact_id),
        )


def bulkreach_stop_sequence(contact_id: int, reason: str = "Manually stopped"):
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE bulkreach_sequences
            SET status = 'STOPPED', stop_reason = ?, updated_at = ?
            WHERE contact_id = ?
            """,
            (reason, now, contact_id),
        )


def bulkreach_get_all_sequences() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.*, c.business_name, c.email, c.crm_stage
            FROM bulkreach_sequences s
            JOIN bulkreach_contacts c ON c.id = s.contact_id
            ORDER BY
                CASE s.status WHEN 'QUEUED' THEN 1 ELSE 0 END,
                s.updated_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def bulkreach_get_contact_by_email(email: str):
    norm = normalize_email(email)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM bulkreach_contacts WHERE normalized_email = ?", (norm,)
        ).fetchone()
        return dict(row) if row else None


def bulkreach_get_recent_log(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT l.*, c.business_name, c.email
            FROM bulkreach_send_log l
            JOIN bulkreach_contacts c ON c.id = l.contact_id
            ORDER BY l.sent_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# BULKREACH — open tracking
# ---------------------------------------------------------------------------
def bulkreach_record_open(contact_id: int, step: int, ip_address: str = None,
                           user_agent: str = None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO bulkreach_tracking_events
                (contact_id, step, ip_address, user_agent, occurred_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (contact_id, step, ip_address, user_agent, _now()),
        )
        _bulkreach_auto_advance_stage(conn, contact_id, "Opened")


def bulkreach_get_open_status() -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT contact_id, MAX(occurred_at) AS last_opened_at
            FROM bulkreach_tracking_events
            GROUP BY contact_id
            """
        ).fetchall()
        return {r["contact_id"]: r["last_opened_at"] for r in rows}


# ---------------------------------------------------------------------------
# BULKREACH — reporting overview (mirrors reporting_docly_overview)
# ---------------------------------------------------------------------------
def reporting_bulkreach_overview() -> dict:
    with get_connection() as conn:
        sent = conn.execute(
            "SELECT COUNT(*) AS n FROM bulkreach_send_log WHERE status = 'SENT'"
        ).fetchone()["n"]
        opened_contacts = conn.execute(
            "SELECT COUNT(DISTINCT contact_id) AS n FROM bulkreach_tracking_events"
        ).fetchone()["n"]
        total_contacts = conn.execute(
            "SELECT COUNT(*) AS n FROM bulkreach_contacts"
        ).fetchone()["n"]
        queued = conn.execute(
            "SELECT COUNT(*) AS n FROM bulkreach_sequences WHERE status = 'QUEUED'"
        ).fetchone()["n"]
        by_step = conn.execute(
            "SELECT step, COUNT(*) AS sent_cnt FROM bulkreach_send_log "
            "WHERE status = 'SENT' GROUP BY step"
        ).fetchall()
        opens_by_step = conn.execute(
            "SELECT step, COUNT(*) AS open_cnt FROM bulkreach_tracking_events GROUP BY step"
        ).fetchall()

    step_stats = {r["step"]: {"sent": r["sent_cnt"], "opened": 0} for r in by_step}
    for r in opens_by_step:
        step_stats.setdefault(r["step"], {"sent": 0, "opened": 0})
        step_stats[r["step"]]["opened"] = r["open_cnt"]

    return {
        "total_contacts": total_contacts,
        "queued": queued,
        "total_sent": sent,
        "unique_opened_contacts": opened_contacts,
        "stage_counts": bulkreach_get_crm_stage_counts(),
        "step_stats": step_stats,
    }

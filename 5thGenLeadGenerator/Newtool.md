# Newtool.md — 5thGenLeadGenerator

## What is this tool?

**5thGenLeadGenerator** is a local lead-generation and lead-qualification tool built for
**5thGen Technologies** (a web development / SEO / digital marketing / Shopify / software
development agency).

You give it a CSV of businesses (name + website). It then:

1. Stores the leads in a local SQLite database, grouped by campaign.
2. Visits each lead's website and collects facts (page title, meta description, contact
   info, platform/CMS, internal pages).
3. Scores every lead 0–100 and decides **which 5thGen service** the evidence supports
   (SEO, Website Development, Shopify Development, Digital Marketing, Software Development).
4. Writes a **personalized outreach email draft** for each qualified lead.

**It never sends anything.** Outreach is draft-only: GENERATE → REVIEW → EDIT → SAVE →
APPROVE. No SMTP, no auto-follow-ups, no WhatsApp/LinkedIn/CRM automation — by design.

---

## The 5 Phases

| Phase | Name | What it does |
|-------|------|--------------|
| 1 | Dashboard & Import | Campaigns, CSV upload, column validation, duplicate detection, lead status tracking |
| 2 | Website Research | Sequentially fetches each lead's homepage + a few internal pages (About / Services / Contact), extracts facts |
| 3 | AI Lead Analysis | Deterministic, transparent scoring + service matching from stored Phase 2 data (never re-scrapes) |
| 4 | AI Outreach | Evidence-based personalized email drafts (draft-only, no sending) |
| 5 | Real AI Provider | Optional remote OpenAI-compatible LLM (Ollama in Colab, Gemini, etc.) that turns findings into natural language |

### Phase 3 — how scoring works (important)

Scoring is **not** done by the AI model. `ai_analysis.py` computes everything
deterministically from stored facts:

- Each of the 5 services gets an **evidence tier**: STRONG / MODERATE / NONE.
  - STRONG = directly observed fact (site unreachable, ecommerce platform detected,
    title+meta both missing, weak/duplicate meta description, explicit software phrase).
  - MODERATE = softer but real signal (template builder like Wix/Squarespace, no address
    text found, no Contact page, thin services content).
  - NONE = no evidence. Generic guesses (bare words like "shop"/"store") are never used.
- `recommended_service` = highest-tier service (ties broken by fixed priority order).
- If nothing reaches MODERATE/STRONG → **"Needs Review"** with a checklist of exactly
  what was checked.
- `confidence` comes from the tier (STRONG→HIGH, MODERATE→MEDIUM, NONE→LOW).
- The AI provider only **writes the readable text** — it cannot change scores, grades,
  or the recommendation. Swapping providers changes wording, never the score.

---

## Project files

| File | Role |
|------|------|
| `app.py` | Streamlit dashboard — 6 tabs: Campaigns, Import Leads, Leads & Status, Website Research, AI Lead Analysis, AI Outreach |
| `config.py` | Central configuration — reads `.env`, clamps workers to 1 (strictly sequential), never exposes secrets |
| `database.py` | SQLite layer — 4 tables (`campaigns`, `leads`, `website_research`, `lead_analysis`, `outreach_emails`), upsert-per-lead |
| `csv_handler.py` | CSV load/validate/normalize + duplicate detection (normalized website / email / name) |
| `website_research.py` | Phase 2 fetcher — one request at a time, same-domain links only, browser-like headers |
| `ai_analysis.py` | Phase 3 deterministic scoring + evidence tiers + `analyze_one_lead()` |
| `ai_outreach.py` | Phase 4 orchestrator — builds draft via provider, hard-blocks empty pitches |
| `ai_provider/base.py` | Provider contract (ABC), `NEEDS_REVIEW` sentinel, response normalizers |
| `ai_provider/mock_provider.py` | Default provider — rule-based, offline, no key needed, always works |
| `ai_provider/free_provider.py` | Scaffold for a free-tier OpenAI-compatible API |
| `ai_provider/real_provider.py` | Phase 5 — remote OpenAI-compatible endpoint (Ollama/Gemini), HTTP only |
| `logger_setup.py` | File + console logging to `logs/app.log` |

## Data storage

- `data/app.db` — SQLite database (configurable via `DATABASE_PATH`)
- `data/csv_input/`, `data/csv_output/` — CSV folders
- `logs/app.log` — run log
- Lead statuses: `NEW → IMPORTED → PENDING → PROCESSING → COMPLETED / FAILED / SKIPPED`
- Each phase has its own table and status, so re-running one phase never damages another.

---

## Configuration (`.env`)

Copy `.env.example` → `.env`. Key settings:

```ini
# Provider: mock (default, offline) | free | real
AI_PROVIDER=mock

# Phase 5 — real provider (Ollama in Colab, or any OpenAI-compatible endpoint)
REAL_AI_BASE_URL=http://COLAB_HOST:11434/v1
REAL_AI_MODEL=qwen3:8b
REAL_AI_API_KEY=            # optional for Ollama

# Load limits (hard-clamped to 1 — the app is always sequential)
WEBSITE_WORKERS=1
AI_WORKERS=1
MAX_CONCURRENT_TASKS=1
MAX_PAGES_PER_WEBSITE=5
REQUEST_DELAY_SECONDS=2
REQUEST_TIMEOUT_SECONDS=15
```

Provider fallback: if the configured provider isn't available, the app silently falls
back to the **mock** provider so the dashboard never crashes.

---

## How to run

```bash
# activate the virtual environment
venv\Scripts\activate        # Windows PowerShell
source venv/Scripts/activate # Git Bash

# start the dashboard
streamlit run app.py
```

Then: **Campaigns** → create one → **Import Leads** → upload CSV → **Website Research**
→ Start → **AI Lead Analysis** → Start → **AI Outreach** → Generate / Edit / Approve.

CSV format: required columns `business_name, website`; optional `category, address,
city, country, phone, owner_name, email, linkedin`.

---

---

## Planned Upgrade — From Draft Tool to Full Campaign Tool

The current tool (Phases 1–5 above) only **drafts** outreach emails — nothing is sent
automatically. The next stage of work turns it into a real business-generation tool,
using a real business mailbox (e.g. `info@5thgentechnologies.com`), with actual sending,
automatic follow-ups, open/click tracking, a lead pipeline (CRM), and reporting.

### Target architecture (once fully built)

```
[Streamlit Dashboard]
        |
[SQLite Database]
        |
[Background Scheduler]  -> checks every 30-60 min for due follow-ups
        |
[Real AI Provider]      -> writes email angles (Phase 5, already working)
        |
[SMTP Server]           -> sends the approved email (business mailbox)
        |
[Tracking Endpoint]     -> public URL, records opens/clicks
```

### Roadmap (in order — each stage depends on the one before it)

**Stage 0 — Security fix (do first, before anything else)**
- Rotate the live Gemini API key currently sitting in `.env` (see Known issue #9 below);
  generate a new one and never share a zip/folder that contains it.

**Stage 1 — Deliverability foundation (required before using a business mailbox)**
- SPF, DKIM, DMARC DNS records on the sending domain (e.g. `5thgentechnologies.com`) —
  without these, Gmail/Outlook route the mail straight to spam.
- Mailbox warm-up: start at 5–10 emails/day on a new mailbox, increase gradually.
- Daily sending limit: cap at 30–50 emails/day per mailbox.
- Domain health check: periodic blacklist check for the sending domain.

**Stage 2 — Fix known bugs**
- The 9 issues already documented below (fallback logic, dead config knobs, stuck
  `PROCESSING` leads, Regenerate stale-state, etc.) — fixed before new features are
  layered on top, so the sending stage is built on a stable base.

**Stage 3 — SMTP sending module (new)**
- Business-mailbox SMTP credentials in `.env`.
- Approving a draft (`direct_audit` angle) sends it for real via SMTP.
- Auto-inject an invisible open-tracking pixel and an unsubscribe link into the body.
- New table: `outreach_sequences` — one row per lead once sending starts.

**Stage 4 — Follow-up sequence + tracking (new)**
- Background scheduler process, polls every 30–60 min.
- Day 0 → Day 3 (`case_study` angle) → Day 7 (`short_inquiry` angle), auto-sent if the
  lead hasn't opened/replied yet.
- Sequence stops automatically on reply, or manually via a Stop button.
- New table: `tracking_events` — open/click events from the public tracking endpoint.
- Requires the tracking endpoint to be reachable from the internet (hosting needed).

**Stage 5 — CRM / lead pipeline (new)**
- Funnel stages beyond sent/opened: `Contacted → Opened → Replied → Interested →
  Meeting Booked → Won/Lost`.
- Manual per-lead notes/status field — this is what actually shows real business
  generated, not just email metrics.

**Stage 6 — Reporting / ROI dashboard (new)**
- Per-campaign: sent / opened / replied / meetings / deals-closed counts.
- Best-performing subject line / angle (simple A/B comparison).
- Daily/monthly graph view.

**Stage 7 — Scale & infra**
- Move the tool to the agency VPS so the scheduler + tracking endpoint run 24/7
  (a laptop can't stay on all the time).
- Optional, later: a Google Maps scraper to find new leads instead of relying only
  on CSV import.

### New tables planned (on top of the existing 5)

| Table | Stage | Stores |
|-------|-------|--------|
| `outreach_sequences` | 3 | Which sequence step a lead is on, when the next email goes out |
| `tracking_events` | 4 | Open/click events per lead |

### Why this order

Deliverability (Stage 1) comes first because if the business mailbox lands in spam,
every later stage (sequences, tracking, CRM, reporting) is generating numbers on emails
nobody ever sees. Bug fixes (Stage 2) come next so sending is built on stable code.
After that, sending → sequence → CRM → reporting is the natural build order used by
real outbound tools (Apollo, Instantly, Smartlead).

---

## Known issues (found in code review, 2026-08-28)

1. **`real_provider.py` `is_available()` requires an API key**, contradicting the
   documented keyless-Ollama design → keyless setups silently fall back to mock.
2. **Dead "soft pitch" logic**: `ai_outreach.py` and `real_provider.py` read
   `analysis_reason` / `sales_angle` from the findings dict, but `compute_findings()`
   never returns those keys → every "Needs Review" lead is always hard-blocked.
3. **`free_provider.py` posts to the wrong URL** (`FREE_AI_BASE_URL` instead of
   `config.get_free_ai_endpoint()` — missing `/chat/completions`) → free provider
   cannot work as shipped.
4. **Dead config knobs** in `real_provider.py`: `REAL_AI_HTTP_TIMEOUT_SECONDS`,
   `REAL_AI_MAX_RETRIES`, `REAL_AI_RETRY_DELAY_SECONDS`, `REAL_AI_TEMPERATURE_*`,
   `REAL_AI_MAX_TOKENS_*`, `REAL_AI_JSON_MODE`, `REAL_AI_THINK` are all ignored.
   The provider uses `REQUEST_TIMEOUT_SECONDS` and never retries 5xx (e.g. the
   HTTP 503 errors seen in `logs/app.log`).
5. `normalize_outreach_result()` / `normalize_narrative_result()` safety validators
   in `ai_provider/base.py` are never called — provider output is unvalidated.
6. Narrative parsing stores the literal string `"None"` when the model returns null
   for a field; `generate_narrative` has no malformed-JSON retry (outreach has one).
7. `app.py`: after **Regenerate**, subject/body fields show the old text (Streamlit
   widget-state staleness); "No eligible leads" messages are wiped by instant rerun;
   a lead interrupted mid-run stays `PROCESSING` forever unless re-run manually.
8. `website_research.py`: `pages_checked` counts failed page fetches too.
9. **Security**: `.env` currently contains a live API key — rotate it and never share
   the folder with the key inside.

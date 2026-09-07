# 5thGenLeadGenerator — Phase 1 + Phase 2

**Phase 1** (foundation): Streamlit dashboard + SQLite database +
campaign management + CSV import with duplicate detection + lead
status tracking.

**Phase 2** (new): sequential, low-load **Website Research** — for each
lead with a website, fetch the homepage plus a few internal pages
(About/Services/Contact), extract business info (title, description,
services text, emails, phones, location, detected CMS/technology), and
save it to SQLite with full resume support.

**Still NOT implemented** (later phases): AI analysis, SEO scoring, AI
recommendations, email generation, SMTP/Gmail sending, IMAP, follow-ups,
reply detection, CRM integrations, Ollama / any AI model.

---

## 1. Architecture

- One codebase runs in two modes, controlled by a single env var:
  - `APP_ENV=local` — your laptop
  - `APP_ENV=colab` — Google Colab
- No code branches on environment except `config.py`, which resolves
  paths and settings once at startup. Every other module just imports
  the resolved values.
- **Zero paid cost**: SQLite (no paid DB), no paid APIs, no paid VPS,
  no paid tunneling service required.
- **Low load by default**: 1 website worker, 1 AI worker, 1 concurrent
  task, 5 max pages per site, 2s request delay, 15s timeout. The app
  never raises these automatically — they only change if you edit
  `.env`. Phase 2 actually enforces them: one website at a time, one
  page at a time, with the configured delay between requests.
- **Colab-safe**: no Ollama, no AI model download, no GPU usage, no
  permanent background process. Research only starts when you press
  **Start Website Research** in the dashboard.
- **Resumable by design**: every lead has a `status`
  (`NEW → IMPORTED → PENDING → PROCESSING → COMPLETED / FAILED / SKIPPED`).
  Phase 2 writes each lead's research result to SQLite the moment it
  finishes, so a Colab disconnect or a manual Stop never loses completed
  work — the next Start simply skips anything already COMPLETED.
- **Same-domain only**: Phase 2 only ever follows links on the lead's
  own website. No Google/LinkedIn/social scraping, no robots.txt
  bypassing, no login-protected pages.

## 2. Project structure

```
5thGenLeadGenerator/
├── app.py                  # Streamlit dashboard (entry point — Phase 1 + Phase 2 tabs)
├── config.py                # Central config — APP_ENV, paths, LOW-load settings
├── database.py               # SQLite schema + data access layer (leads + website_research)
├── csv_handler.py            # CSV validation, preview, duplicate detection
├── website_research.py       # Phase 2 — fetch/parse/extract + sequential runner
├── logger_setup.py           # Logging configuration
├── requirements.txt          # Lightweight dependencies only
├── .env.example               # Copy to .env and adjust
├── .gitignore
├── data/                       # SQLite DB + CSV in/out (gitignored contents)
├── logs/                        # App logs (gitignored contents)
└── notebooks/
    └── 5thGenLeadGenerator_Colab.ipynb   # Colab bootstrap + launcher
```

## 3. Setup — Local Development Mode

**Requirements:** Python 3.10+

**Windows (PowerShell / CMD):**

```bat
cd 5thGenLeadGenerator
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

**macOS / Linux:**

```bash
cd 5thGenLeadGenerator
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` already defaults to `APP_ENV=local` with LOW-load settings — no
changes needed to run Phase 1 as-is.

**Run it:**

```bash
streamlit run app.py
```

Streamlit opens at `http://localhost:8501`.

## 4. Setup — Google Colab Execution Mode

Open `notebooks/5thGenLeadGenerator_Colab.ipynb` in Google Colab and run
the cells top to bottom:

1. **Mount Google Drive** (optional but recommended) — makes your SQLite
   database and logs persist across Colab sessions instead of living in
   temporary Colab storage.
2. **Get the project onto the Colab machine** — clone your GitHub repo,
   or upload the project folder as a zip and unzip it.
3. **Install dependencies** — exactly `requirements.txt`: `streamlit`,
   `pandas`, `python-dotenv`, plus `requests` and `beautifulsoup4` for
   Phase 2's website fetching/parsing. No tunnel package, no AI/ML
   libraries.
4. **Configure environment** — sets `APP_ENV=colab` and, if Drive is
   mounted, points `DATABASE_PATH` / `LOG_DIR` at
   `/content/drive/MyDrive/5thGenLeadGenerator/...` so nothing is lost
   when the Colab runtime resets.
5. **Start Streamlit** — runs the dashboard in the background on port
   8501. Nothing runs until you run this cell.
6. **Open the dashboard** — uses Colab's own built-in port forwarding
   (`google.colab.output.serve_kernel_port_as_window` /
   `serve_kernel_port_as_iframe`), which ships with Colab itself. No
   external service, signup, account, or API key required. There's a
   final cell to stop the app cleanly when you're done.

No Ollama, no AI model download, no GPU, and no third-party tunnel —
matches the zero-paid-cost, low-load requirement. (A third-party tunnel
like ngrok would only be needed if you later want to share the dashboard
with someone outside your own Colab session — that's optional and not
part of Phase 1.)

## 5. Using the dashboard

- **Sidebar** — shows current Environment (LOCAL/COLAB), Performance
  Mode (LOW), and all worker/load settings, plus the active database
  path.
- **Campaigns tab** — create a campaign, see existing campaigns and
  their lead counts.
- **Import Leads tab** — pick a campaign, upload a CSV, see a preview
  (valid rows / new rows / duplicates) before committing, then import.
  - Required columns: `business_name`, `website`
  - Optional columns: `category`, `address`, `city`, `country`, `phone`,
    `owner_name`, `email`, `linkedin`
  - Missing optional columns never crash the import — they're just left
    blank.
  - Duplicates are detected by normalized website, email, and business
    name, both within the file and against leads already in the
    campaign.
- **Leads & Status tab** — see lead counts by status and browse/filter
  the lead list. Phase 2 updates a lead's status as it's researched
  (`PENDING → PROCESSING → COMPLETED/FAILED/SKIPPED`).
- **Website Research tab (Phase 2)** — pick a campaign, then:
  - **Status row** shows total leads, leads with a website, and counts
    per research status.
  - **Start Website Research** — processes every eligible lead (has a
    website, not yet completed) one at a time, in order.
  - **Stop After Current Lead** — finishes the lead currently in
    progress, then stops; remaining leads stay `PENDING` for next time.
  - **Retry Failed** — re-queues only leads whose last research attempt
    `FAILED`.
  - **Re-run Selected** — pick any specific leads (including already
    `COMPLETED` ones) and research them again, overwriting their saved
    result.
  - While running, the current business/website and a progress bar
    (processed / success / failed / skipped) update live.
  - **Research Results** — filterable table of every saved result
    (status, HTTP status, page title, pages checked, emails, phones,
    detected technology, last updated).

## 6. Configuration reference (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `local` | `local` or `colab` |
| `DATABASE_PATH` | `data/app.db` | SQLite file path (point at Drive in Colab) |
| `CSV_INPUT_DIR` | `data/csv_input` | Where CSVs are expected/saved |
| `CSV_OUTPUT_DIR` | `data/csv_output` | Where exports would be saved (future phases) |
| `LOG_DIR` / `LOG_FILE` | `logs` / `logs/app.log` | Log location |
| `PERFORMANCE_MODE` | `LOW` | Display label; app ships LOW only |
| `WEBSITE_WORKERS` | `1` | Max simultaneous website fetches — Phase 2 enforces this (sequential) |
| `AI_WORKERS` | `1` | Max simultaneous AI analyses — Phase 3 enforces this (sequential) |
| `MAX_CONCURRENT_TASKS` | `1` | Max simultaneous research/analysis tasks — Phase 2/3 enforce this |
| `MAX_PAGES_PER_WEBSITE` | `5` | Max pages Phase 2 visits per lead's website |
| `REQUEST_DELAY_SECONDS` | `2` | Delay Phase 2 waits between page fetches |
| `REQUEST_TIMEOUT_SECONDS` | `15` | Per-request timeout Phase 2 uses |
| `AI_PROVIDER` | `mock` | `mock` (default, no key needed) or `free` (see Phase 3 details) |
| `FREE_AI_API_KEY` / `FREE_AI_BASE_URL` / `FREE_AI_MODEL` | *(blank)* | Only used by the `free` provider — you supply these yourself |

## 7. Phase 2 details

- **Data extracted per website**: page title, meta description,
  homepage/about description, services page text, contact page text +
  URL, emails and phone numbers found on public pages, a simple
  address/location text match, and lightweight technology detection
  (WordPress, WooCommerce, Shopify, Wix, Squarespace, Webflow, Joomla,
  Drupal — via obvious markers in the HTML, nothing invasive).
- **Only follows internal links** on the same domain as the lead's
  website, prioritizing Contact / About / Services style pages first.
  Never searches Google, never touches LinkedIn or social media, never
  attempts login-protected pages.
- **One row per lead** in the `website_research` table — a re-run
  overwrites the previous result rather than creating duplicates.
- **Errors are captured, not raised**: unreachable sites, DNS failures,
  timeouts, HTTP 4xx/5xx, SSL errors, and malformed HTML all become a
  `FAILED` (or `SKIPPED`, for a missing/unusable URL) result with an
  `error_message` — one bad site never stops the batch or crashes the
  dashboard.
- **Streamlit-friendly design**: the dashboard processes one lead per
  rerun rather than blocking in a long loop, so the Stop button can take
  effect between leads and every finished lead is already saved to
  SQLite before the next one starts (true resume, no in-memory queue to
  lose on a Colab disconnect).

## 8. Phase 3 details — AI Lead Analysis

- **Uses only stored Phase 2 data.** `ai_analysis.py` reads the
  `website_research` row (and the lead's Phase 1 fields) already in
  SQLite — it never re-fetches a website and never adds another crawler.
- **Transparent scoring.** `ai_analysis.compute_findings()` builds a
  0–100 `lead_score` from a documented, additive rubric (site reachable,
  contact info present, meta description present, platform detected,
  pages checked, etc.) with every point paired to a plain-English reason
  stored in `analysis_reason`. Bands: `0–39 LOW`, `40–59 MEDIUM`,
  `60–79 HIGH`, `80–100 VERY HIGH`. This is an **AI-assisted opportunity
  score**, not an objective measurement.
- **Recommended service.** A small keyword/signal rubric checks each of
  the 5 services (Digital Marketing, Software Development, SEO, Shopify
  Development, Website Development) against the stored research text and
  picks the strongest as `recommended_service`, with up to two
  `secondary_services`.
- **Swappable AI provider.** `ai_provider/` defines an `AIProvider`
  interface. The active provider only turns the already-decided
  findings above into readable text (summary, sales angle, per-service
  opportunity notes) — it never changes the score.
  - `MockAIProvider` (default, `AI_PROVIDER=mock`): rule-based templates,
    no model, no network call, no API key. Ships active.
  - `FreeAIProvider` (`AI_PROVIDER=free`): scaffold for a free-tier,
    OpenAI-chat-completions-compatible hosted API. Inert until you set
    `FREE_AI_API_KEY` / `FREE_AI_BASE_URL` / `FREE_AI_MODEL` yourself —
    no credentials are invented by this app, and it automatically falls
    back to `mock` if any of those three are missing.
- **Never invents facts.** Anything not present in the stored data is
  labeled `"Unknown — ..."` in the relevant field and lowers
  `confidence` (`LOW` / `MEDIUM` / `HIGH`) rather than being guessed at.
- **Own status table, own resume/retry/re-run.** `lead_analysis` is a
  separate table from `leads` and `website_research` — analyzing a lead
  never touches its Phase 2 research status. Supports
  PENDING/PROCESSING/COMPLETED/FAILED/SKIPPED, safe resume (a stopped
  run leaves the rest PENDING), Retry Failed, and Re-run Selected —
  same patterns as the Website Research tab.
- **Still strictly sequential / LOW-load.** One lead per Streamlit
  rerun, `AI_WORKERS`/`MAX_CONCURRENT_TASKS` still clamped to 1, no
  background workers, processing only starts on an explicit "Start
  Analysis" click.

## 9. Phase 4 details — AI Outreach (draft generation only, no sending)

- **Uses only stored Phase 1-3 data.** `ai_outreach.py` calls the exact
  same `ai_analysis.build_context()`/`compute_findings()` functions
  Phase 3 uses — no re-scraping, no new analysis logic.
- **GENERATE → REVIEW → EDIT → SAVE → APPROVE only.** Nothing in this
  project sends email. Drafts live in their own `outreach_emails` table
  (statuses: `DRAFT` / `APPROVED` / `REJECTED` / `NEEDS_REVIEW`).
- **NEEDS_REVIEW hard rule.** If Phase 3's `recommended_service` is
  `"Needs Review"`, no sales pitch is ever generated — enforced both by
  the provider and, independently, by `ai_outreach.py` itself.
- Same swappable `AIProvider` used by Phase 3 — see below.

## 10. Phase 5 details — Real Cloud AI Provider

- **Purpose.** Improves the *wording* Phase 3/4 already decided to say —
  it never recalculates `lead_score`, `lead_grade`, `recommended_service`,
  `secondary_services`, or `confidence`. Phase 3 decides WHAT the
  opportunity is; the AI provider only decides HOW to phrase it. This is
  enforced structurally (the provider's return contract has no field
  that could carry a score/service) as well as by prompt instructions.
- **`RealAIProvider`** (`ai_provider/real_provider.py`, `AI_PROVIDER=real`):
  a second, separate OpenAI-chat-completions-compatible HTTP provider
  slot, independent of `FreeAIProvider`/`free` so an existing free-tier
  config is never disturbed. Inert until you set `REAL_AI_API_KEY` /
  `REAL_AI_BASE_URL` / `REAL_AI_MODEL` yourself in your own `.env` —
  no credentials are invented, and it automatically falls back to
  `mock` if any of the three are missing, exactly like `FreeAIProvider`.
- **One sequential HTTPS request per lead**, at most **1 retry** (only
  on a connection/timeout failure — never retried on an API error status
  or a malformed response), governed by the same `REQUEST_TIMEOUT_SECONDS`
  as Phase 2, `AI_WORKERS`/`MAX_CONCURRENT_TASKS` still clamped to 1. No
  local model, no GPU, no background worker, no persistent connection
  pool.
- **NEEDS_REVIEW is still absolute.** `RealAIProvider` checks
  `recommended_service == "Needs Review"` itself, before spending any
  network call, and `ai_outreach.py` enforces the same rule again
  regardless of what any provider returns.
- **Malformed responses never reach the database.** JSON shape is
  validated before being accepted; a bad response raises a clean, key-
  free error that the existing Phase 3/4 error handling already turns
  into a safe `FAILED`/`NEEDS_REVIEW` result — never a crash, never
  malformed data written.
- **Secrets.** `REAL_AI_API_KEY` is read only from `.env`/environment
  variables, used only in the request's `Authorization` header, and
  never appears in a log line, an error message, the database, the UI,
  or a packaged ZIP. In Google Colab, set it as a Colab secret or via
  `os.environ` in the notebook rather than uploading a `.env` file.
- **Google Colab.** No local server, no Ollama, no GPU, no Docker
  required — the real provider is just an HTTPS call, so it works the
  same in Colab as locally once the three `REAL_AI_*` values are set.

## 11. What's next (not in Phase 1-5)

SMTP/Gmail/Outlook sending, IMAP, scheduling, follow-ups, reply/open/click
tracking, WhatsApp/LinkedIn automation, and CRM integrations are all
future phases, built on top of this foundation without changing this
Phase 1-5 code.

"""
app.py
======
5thGenLeadGenerator — Streamlit Dashboard (PHASE 1 + PHASE 2 + PHASE 3 + PHASE 4.1-4.4)

Phase 1 (unchanged): project structure, environment/performance display,
campaign creation, CSV import with preview/validation/duplicate detection,
lead status tracking, logging.

Phase 2 (unchanged, additive): sequential, low-load website research — for
each eligible lead, fetch the homepage + a few internal pages (About/
Services/Contact) and extract business info. See website_research.py for
the fetching/parsing logic.

Phase 3 (unchanged, additive): AI-assisted business analysis / lead
qualification, using ONLY the data Phase 2 already stored — no
re-scraping. Transparent 0-100 opportunity scoring and 5thGen service
matching happen in ai_analysis.py; the swappable AI provider
(ai_provider/) only turns those findings into readable text. See
ai_analysis.py and ai_provider/ for details.

Phase 4.1-4.4 (new, additive): evidence-based outreach draft generation —
GENERATE -> REVIEW -> EDIT -> SAVE -> APPROVE only, using ONLY the data
Phase 1-3 already stored (no re-scraping, no re-analysis logic change).
See ai_outreach.py and ai_provider's generate_outreach() for details.

Explicitly NOT implemented anywhere in this project (later phases):
email SENDING, SMTP/Gmail/Outlook/IMAP, scheduling, follow-ups, reply/
open/click tracking, WhatsApp/LinkedIn automation, CRM integrations.
"""

import json

import streamlit as st
import pandas as pd

import config
import database
import csv_handler
import website_research
import ai_analysis
import ai_outreach
import docly_scheduler
import tracking_server
from ai_provider import get_provider
from logger_setup import get_logger

logger = get_logger()

st.set_page_config(
    page_title="5thGenLeadGenerator",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom theme — 5thGen brand look (dark sidebar, accent color, card style)
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    :root {
        --fivegen-accent: #6C5CE7;
        --fivegen-accent-dark: #4834D4;
        --fivegen-bg: #F7F7FC;
        --fivegen-card: #FFFFFF;
        --fivegen-text: #1E1E2E;
        --fivegen-muted: #6B7280;
    }

    /* Page background */
    .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
        background-color: var(--fivegen-bg) !important;
    }

    /* Force readable text everywhere in the main content area, overriding
       any dark-mode-detected default from the browser/OS. */
    [data-testid="stMain"], [data-testid="stMain"] p, [data-testid="stMain"] span,
    [data-testid="stMain"] label, [data-testid="stMain"] div {
        color: var(--fivegen-text) !important;
    }

    /* Headers */
    h1, h2, h3, [data-testid="stMain"] h1, [data-testid="stMain"] h2, [data-testid="stMain"] h3 {
        color: var(--fivegen-text) !important;
        font-weight: 700 !important;
    }

    /* Text inputs / textareas must stay dark-on-white and clearly bordered */
    [data-testid="stMain"] input, [data-testid="stMain"] textarea, [data-testid="stMain"] select {
        background-color: #FFFFFF !important;
        color: var(--fivegen-text) !important;
        border: 1px solid #D9D6F0 !important;
        border-radius: 8px !important;
    }
    [data-testid="stMain"] input::placeholder, [data-testid="stMain"] textarea::placeholder {
        color: #9CA3AF !important;
    }
    [data-testid="stMain"] label p {
        color: var(--fivegen-text) !important;
        font-weight: 600 !important;
    }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1E1B3A 0%, #2B2559 100%);
    }
    section[data-testid="stSidebar"] * {
        color: #EDEBFF !important;
    }
    section[data-testid="stSidebar"] code {
        background-color: rgba(255,255,255,0.14) !important;
        color: #E8E4FF !important;
    }
    /* st.code() renders as a syntax-highlighted <pre> block that ships its
       own light-theme background/text colors — override those explicitly
       or the text becomes near-invisible on the dark sidebar. */
    section[data-testid="stSidebar"] [data-testid="stCodeBlock"],
    section[data-testid="stSidebar"] [data-testid="stCodeBlock"] pre {
        background-color: rgba(255,255,255,0.10) !important;
        border: 1px solid rgba(255,255,255,0.18) !important;
        border-radius: 8px !important;
    }
    section[data-testid="stSidebar"] [data-testid="stCodeBlock"] * {
        color: #F1EEFF !important;
        background-color: transparent !important;
    }
    section[data-testid="stSidebar"] hr {
        border-color: rgba(255,255,255,0.15);
    }

    /* Headers */
    h1, h2, h3 {
        color: var(--fivegen-text);
        font-weight: 700 !important;
    }

    /* Tabs (kept in case any st.tabs is used elsewhere) */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        background-color: var(--fivegen-card);
        padding: 6px;
        border-radius: 12px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 8px 16px;
        font-weight: 600;
    }
    .stTabs [aria-selected="true"] {
        background-color: var(--fivegen-accent) !important;
        color: white !important;
    }

    /* Sidebar navigation — style the radio group as a vertical nav menu */
    section[data-testid="stSidebar"] div[role="radiogroup"] {
        gap: 2px;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label {
        padding: 9px 12px !important;
        border-radius: 10px !important;
        width: 100%;
        transition: background-color 0.15s ease;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
        background-color: rgba(255,255,255,0.08) !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label[data-checked="true"],
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
        background-color: var(--fivegen-accent) !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label p {
        font-size: 0.95rem !important;
        font-weight: 600 !important;
    }

    /* Buttons */
    .stButton > button, .stFormSubmitButton > button {
        border-radius: 8px;
        font-weight: 600;
        border: 1px solid var(--fivegen-accent);
    }
    .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
        background-color: var(--fivegen-accent);
        border-color: var(--fivegen-accent);
    }
    .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {
        background-color: var(--fivegen-accent-dark);
        border-color: var(--fivegen-accent-dark);
    }

    /* Metric cards */
    div[data-testid="stMetric"] {
        background-color: var(--fivegen-card);
        border: 1px solid #ECECF4;
        border-radius: 12px;
        padding: 14px 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }

    /* Dataframes / tables */
    div[data-testid="stDataFrame"] {
        border-radius: 10px;
        overflow: hidden;
        border: 1px solid #ECECF4;
    }

    /* Expanders (main content area — light card look) */
    details {
        background-color: var(--fivegen-card);
        border: 1px solid #ECECF4;
        border-radius: 10px;
    }

    /* Sidebar expanders (e.g. "System Status") must NOT get the white
       card background above — that combined with the sidebar's light
       text color made the content unreadable (light text on white). */
    section[data-testid="stSidebar"] details {
        background-color: rgba(255, 255, 255, 0.06) !important;
        border: 1px solid rgba(255, 255, 255, 0.18) !important;
        border-radius: 10px !important;
    }
    section[data-testid="stSidebar"] details summary {
        color: #EDEBFF !important;
        font-weight: 600 !important;
    }
    section[data-testid="stSidebar"] details p,
    section[data-testid="stSidebar"] details span,
    section[data-testid="stSidebar"] details div,
    section[data-testid="stSidebar"] details strong {
        color: #EDEBFF !important;
    }
    section[data-testid="stSidebar"] details code {
        background-color: rgba(255,255,255,0.14) !important;
        color: #F1EEFF !important;
    }

    /* Top brand banner */
    .fivegen-banner {
        background: linear-gradient(90deg, var(--fivegen-accent) 0%, var(--fivegen-accent-dark) 100%);
        padding: 18px 24px;
        border-radius: 14px;
        margin-bottom: 22px;
        color: white;
    }
    .fivegen-banner h1 {
        color: white !important;
        margin: 0;
        font-size: 1.6rem;
    }
    .fivegen-banner p {
        color: #E5E1FF;
        margin: 4px 0 0 0;
        font-size: 0.9rem;
    }
    </style>

    <div class="fivegen-banner">
        <h1>📋 5thGenLeadGenerator</h1>
        <p>AI-assisted lead research &amp; outreach for 5thGen Technologies — draft-only, zero paid cost</p>
    </div>
    """,
    unsafe_allow_html=True,
)

database.init_db()


# ---------------------------------------------------------------------------
# Sidebar — environment & performance settings (read-only display, per spec)
# ---------------------------------------------------------------------------
def render_sidebar() -> str:
    st.sidebar.markdown(
        """
        <div style="text-align:center; padding: 6px 0 18px 0;">
            <div style="
                width:64px; height:64px; margin:0 auto 10px auto;
                border-radius:16px;
                background: linear-gradient(135deg, #6C5CE7 0%, #4834D4 100%);
                display:flex; align-items:center; justify-content:center;
                font-size:26px; font-weight:800; color:white;
                box-shadow: 0 4px 14px rgba(108,92,231,0.45);
            ">5G</div>
            <div style="font-weight:800; font-size:1.05rem; letter-spacing:0.3px;">
                5thGen Technologies
            </div>
            <div style="font-size:0.72rem; opacity:0.7; margin-top:2px;">
                Lead Generator &amp; Outreach
            </div>
        </div>
        <hr style="margin: 0 0 14px 0;">
        """,
        unsafe_allow_html=True,
    )

    nav_options = [
        "📁 Campaigns",
        "📥 Import Leads",
        "📊 Leads & Status",
        "🔍 Website Research",
        "🤖 AI Lead Analysis",
        "📧 AI Outreach",
        "📨 Docly",
        "📈 Reporting",
    ]
    selected = st.sidebar.radio(
        "Navigation", nav_options, key="main_nav", label_visibility="collapsed"
    )

    st.sidebar.divider()

    with st.sidebar.expander("⚙️ System Status", expanded=False):
        env_label = "🟢 COLAB" if config.IS_COLAB else "🔵 LOCAL"
        st.markdown(f"**Environment:** {env_label}")
        st.markdown(f"**Performance Mode:** `{config.PERFORMANCE_MODE}`")

        st.divider()
        st.markdown("**Worker / Load Settings**")
        st.write(f"Website Workers: `{config.WEBSITE_WORKERS}`")
        st.write(f"AI Workers: `{config.AI_WORKERS}`")
        st.write(f"Max Concurrent Tasks: `{config.MAX_CONCURRENT_TASKS}`")
        st.write(f"Request Delay: `{config.REQUEST_DELAY_SECONDS}s`")
        st.write(f"Max Pages per Website: `{config.MAX_PAGES_PER_WEBSITE}`")
        st.write(f"Request Timeout: `{config.REQUEST_TIMEOUT_SECONDS}s`")
        st.write(f"AI Provider: `{config.AI_PROVIDER}`")

        st.caption(
            "LOW-load defaults. Change them via `.env` — the app never raises "
            "them automatically. Phase 2's Website Research actually honors "
            "these (sequential, one page at a time)."
        )

        st.divider()
        st.markdown("**Storage**")
        st.code(config.DATABASE_PATH, language=None)
        st.caption("SQLite database path (configurable via DATABASE_PATH)")

    return selected


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------
def render_campaign_section():
    st.header("📁 Campaigns")

    col_create, col_list = st.columns([1, 2])

    with col_create:
        st.subheader("Create Campaign")
        with st.form("create_campaign_form", clear_on_submit=True):
            name = st.text_input("Campaign name *")
            description = st.text_area("Description (optional)", height=80)
            submitted = st.form_submit_button("Create Campaign")

            if submitted:
                if not name.strip():
                    st.error("Campaign name is required.")
                elif database.campaign_name_exists(name):
                    st.error(f"A campaign named '{name}' already exists.")
                else:
                    campaign_id = database.create_campaign(name, description)
                    logger.info(f"Campaign created: id={campaign_id} name={name}")
                    st.success(f"Campaign '{name}' created.")
                    st.rerun()

    with col_list:
        st.subheader("Existing Campaigns")
        campaigns = database.list_campaigns()
        if not campaigns:
            st.info("No campaigns yet. Create one to get started.")
        else:
            df = pd.DataFrame(campaigns)[
                ["id", "name", "description", "lead_count", "created_at"]
            ]
            df.columns = ["ID", "Name", "Description", "Leads", "Created At"]
            st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# CSV Import
# ---------------------------------------------------------------------------
def render_import_section():
    st.header("📥 Import Leads (CSV)")

    campaigns = database.list_campaigns()
    if not campaigns:
        st.warning("Create a campaign first before importing leads.")
        return

    campaign_options = {f"{c['name']} (ID {c['id']})": c["id"] for c in campaigns}
    selected_label = st.selectbox("Target campaign", list(campaign_options.keys()))
    campaign_id = campaign_options[selected_label]

    st.caption(
        f"Required columns: `{'`, `'.join(config.CSV_REQUIRED_COLUMNS)}` — "
        f"Optional: `{'`, `'.join(config.CSV_OPTIONAL_COLUMNS)}`"
    )

    uploaded_file = st.file_uploader("Choose a CSV file", type=["csv"])

    if uploaded_file is None:
        return

    try:
        raw_df = csv_handler.load_csv(uploaded_file)
        warnings = csv_handler.validate_columns(raw_df)
    except csv_handler.CsvValidationError as exc:
        st.error(str(exc))
        logger.warning(f"CSV validation failed: {exc}")
        return

    for w in warnings:
        st.warning(w)

    clean_df, dropped_count = csv_handler.normalize_dataframe(raw_df)
    if dropped_count:
        st.warning(
            f"{dropped_count} row(s) dropped: missing required "
            f"business_name and/or website."
        )

    if clean_df.empty:
        st.error("No valid rows to import after validation.")
        return

    unique_df, duplicate_df = csv_handler.detect_duplicates(clean_df, campaign_id)

    st.subheader("Preview")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total valid rows", len(clean_df))
    c2.metric("New (will import)", len(unique_df))
    c3.metric("Duplicates (skipped)", len(duplicate_df))

    st.markdown("**Rows to import:**")
    st.dataframe(unique_df.head(50), use_container_width=True, hide_index=True)
    if len(unique_df) > 50:
        st.caption(f"Showing first 50 of {len(unique_df)} rows.")

    if not duplicate_df.empty:
        with st.expander(f"⚠️ View {len(duplicate_df)} duplicate row(s) (will be skipped)"):
            st.dataframe(duplicate_df.head(50), use_container_width=True, hide_index=True)

    if unique_df.empty:
        st.info("Nothing new to import — all rows were duplicates.")
        return

    if st.button(f"✅ Import {len(unique_df)} lead(s) into '{selected_label}'"):
        rows = unique_df.to_dict(orient="records")
        inserted = database.bulk_insert_leads(campaign_id, rows)
        logger.info(
            f"Imported {inserted} leads into campaign_id={campaign_id} "
            f"(skipped {len(duplicate_df)} duplicates)"
        )
        st.success(f"Imported {inserted} lead(s). Duplicates skipped: {len(duplicate_df)}.")
        st.rerun()


# ---------------------------------------------------------------------------
# Lead status view
# ---------------------------------------------------------------------------
def render_leads_section():
    st.header("📊 Leads & Status")

    campaigns = database.list_campaigns()
    if not campaigns:
        st.info("No campaigns yet.")
        return

    campaign_options = {f"{c['name']} (ID {c['id']})": c["id"] for c in campaigns}
    selected_label = st.selectbox(
        "Campaign", list(campaign_options.keys()), key="leads_campaign_select"
    )
    campaign_id = campaign_options[selected_label]

    counts = database.count_leads_by_status(campaign_id)
    cols = st.columns(len(config.LEAD_STATUSES))
    for col, status in zip(cols, config.LEAD_STATUSES):
        col.metric(status, counts.get(status, 0))

    status_filter = st.selectbox(
        "Filter by status", ["ALL"] + list(config.LEAD_STATUSES)
    )

    leads = database.list_leads(campaign_id, status=status_filter)
    if not leads:
        st.info("No leads match this filter.")
        return

    df = pd.DataFrame(leads)
    display_cols = [
        "id", "business_name", "website", "email", "phone", "city",
        "country", "status", "updated_at",
    ]
    display_cols = [c for c in display_cols if c in df.columns]
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Website Research — Phase 2
# ---------------------------------------------------------------------------
# Design note: Streamlit runs the whole script top-to-bottom on every
# interaction, so a long blocking loop can't be interrupted by a button
# click mid-loop. Instead this processes exactly ONE lead per rerun, then
# calls st.rerun() to continue — that gives the Stop button a chance to
# take effect between leads, keeps everything single-threaded/sequential
# (no background workers, per spec), and every completed lead is already
# saved to SQLite before the next rerun even starts, so resume is free.
def render_research_section():
    st.header("🔍 Website Research")
    st.caption(
        "Phase 2 — visits each lead's website (homepage + a few internal "
        "pages) and extracts business info. No AI analysis, no SEO scoring, "
        "no email generation/sending here — that's later phases."
    )

    campaigns = database.list_campaigns()
    if not campaigns:
        st.info("No campaigns yet. Create one in the Campaigns tab first.")
        return

    campaign_options = {f"{c['name']} (ID {c['id']})": c["id"] for c in campaigns}
    selected_label = st.selectbox(
        "Campaign", list(campaign_options.keys()), key="research_campaign_select"
    )
    campaign_id = campaign_options[selected_label]

    with st.expander("⚙️ Environment & Performance Settings", expanded=False):
        e1, e2 = st.columns(2)
        e1.write(f"**Environment:** {'COLAB' if config.IS_COLAB else 'LOCAL'}")
        e2.write(f"**Performance Mode:** `{config.PERFORMANCE_MODE}`")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Website Workers", config.WEBSITE_WORKERS)
        p2.metric("Max Concurrent Tasks", config.MAX_CONCURRENT_TASKS)
        p3.metric("Request Delay", f"{config.REQUEST_DELAY_SECONDS}s")
        p4.metric("Max Pages", config.MAX_PAGES_PER_WEBSITE)
        st.caption(f"Request Timeout: {config.REQUEST_TIMEOUT_SECONDS}s — set via `.env`, never auto-raised.")

    summary = database.research_summary_counts(campaign_id)
    st.subheader("Status")
    cols = st.columns(7)
    for col, key, disp in zip(
        cols,
        ["total_leads", "with_website", "PENDING", "PROCESSING", "COMPLETED", "FAILED", "SKIPPED"],
        ["Total Leads", "With Website", "Pending", "Processing", "Completed", "Failed", "Skipped"],
    ):
        col.metric(disp, summary.get(key, 0))

    run_key = f"research_running_{campaign_id}"
    queue_key = f"research_queue_{campaign_id}"
    stop_key = f"research_stop_{campaign_id}"
    stats_key = f"research_stats_{campaign_id}"

    st.session_state.setdefault(run_key, False)
    st.session_state.setdefault(stop_key, False)
    st.session_state.setdefault(
        stats_key, {"processed": 0, "success": 0, "failed": 0, "skipped": 0, "total": 0}
    )

    def _start(lead_ids, log_note):
        st.session_state[queue_key] = lead_ids
        st.session_state[run_key] = bool(lead_ids)
        st.session_state[stop_key] = False
        st.session_state[stats_key] = {
            "processed": 0, "success": 0, "failed": 0, "skipped": 0, "total": len(lead_ids),
        }
        logger.info(f"Website research {log_note}: campaign_id={campaign_id} leads={len(lead_ids)}")

    b1, b2, b3 = st.columns(3)

    if b1.button("▶️ Start Website Research", disabled=st.session_state[run_key]):
        eligible = database.get_leads_eligible_for_research(campaign_id, include_failed=False)
        _start([l["id"] for l in eligible], "started")
        if not st.session_state[queue_key]:
            st.info("No eligible leads (need a non-empty website and status NEW/IMPORTED/PENDING).")
        st.rerun()

    if b2.button("⏹️ Stop After Current Lead", disabled=not st.session_state[run_key]):
        st.session_state[stop_key] = True
        logger.info(f"Website research stop requested: campaign_id={campaign_id}")

    if b3.button("🔁 Retry Failed", disabled=st.session_state[run_key]):
        eligible = database.get_leads_eligible_for_research(campaign_id, include_failed=True)
        failed_ids = [l["id"] for l in eligible if l["status"] == "FAILED"]
        _start(failed_ids, "retry-failed started")
        if not failed_ids:
            st.info("No failed leads to retry.")
        st.rerun()

    with st.expander("🔄 Re-run Selected Leads"):
        leads_with_site = [
            l for l in database.list_leads(campaign_id, status="ALL", limit=500) if l.get("website")
        ]
        options = {
            f"{l['business_name']} (ID {l['id']}, {l['website']})": l["id"]
            for l in leads_with_site
        }
        picked = st.multiselect(
            "Leads to re-run (any status, including already-completed)",
            list(options.keys()),
            key=f"rerun_pick_{campaign_id}",
        )
        if st.button(
            "Re-run Selected", disabled=st.session_state[run_key] or not picked,
            key=f"rerun_btn_{campaign_id}",
        ):
            ids = [options[p] for p in picked]
            database.reset_leads_for_rerun(ids)
            _start(ids, "re-run started")
            st.rerun()

    stats = st.session_state[stats_key]
    if stats["total"]:
        st.progress(min(stats["processed"] / stats["total"], 1.0))
    st.caption(
        f"Processed: {stats['processed']}/{stats['total']} · "
        f"Success: {stats['success']} · Failed: {stats['failed']} · Skipped: {stats['skipped']}"
    )

    if st.session_state[run_key]:
        queue = st.session_state.get(queue_key, [])

        if st.session_state[stop_key] or not queue:
            st.session_state[run_key] = False
            if st.session_state[stop_key] and queue:
                st.warning(
                    f"Stopped. {len(queue)} lead(s) remain PENDING — "
                    f"press Start Website Research to resume."
                )
                logger.info(f"Website research stopped: campaign_id={campaign_id} remaining={len(queue)}")
            else:
                st.success("Website research finished for this batch.")
                logger.info(f"Website research batch complete: campaign_id={campaign_id}")
            st.rerun()
        else:
            lead_id = queue[0]
            lead = database.get_lead(lead_id)
            if not lead:
                st.session_state[queue_key] = queue[1:]
                st.rerun()

            st.info(f"Researching: **{lead['business_name']}** — {lead.get('website') or '(no website)'}")

            database.update_lead_status(lead_id, "PROCESSING")
            database.set_research_status(campaign_id, lead_id, lead.get("website", ""), "PROCESSING")
            logger.info(f"Research started: lead_id={lead_id} business={lead['business_name']}")

            try:
                result = website_research.research_one_lead(lead)
            except Exception as exc:
                # A single unexpected failure must never crash the dashboard.
                result = {
                    "status": "FAILED",
                    "website_url": lead.get("website", ""),
                    "final_url": None,
                    "http_status": None,
                    "pages_checked": 0,
                    "error_message": f"Unexpected error: {exc}",
                }
                logger.error(f"Research crashed for lead_id={lead_id}: {exc}")

            database.save_research_result(lead_id, campaign_id, result)
            database.update_lead_status(
                lead_id, result["status"], error_message=result.get("error_message")
            )

            stats["processed"] += 1
            if result["status"] == "COMPLETED":
                stats["success"] += 1
                logger.info(f"Research completed: lead_id={lead_id} pages={result.get('pages_checked', 0)}")
            elif result["status"] == "FAILED":
                stats["failed"] += 1
                logger.info(f"Research failed: lead_id={lead_id} error={result.get('error_message')}")
            else:
                stats["skipped"] += 1
                logger.info(f"Research skipped: lead_id={lead_id} reason={result.get('error_message')}")

            st.session_state[stats_key] = stats
            st.session_state[queue_key] = queue[1:]
            st.rerun()

    st.divider()
    st.subheader("📄 Research Results")
    status_filter = st.selectbox(
        "Filter by status",
        ["ALL", "PENDING", "PROCESSING", "COMPLETED", "FAILED", "SKIPPED"],
        key=f"research_status_filter_{campaign_id}",
    )
    results = database.list_research(campaign_id, status=status_filter)
    if not results:
        st.info("No research results yet for this filter.")
        return

    df = pd.DataFrame(results)
    display_cols = [
        "lead_id", "business_name", "status", "website_url", "http_status",
        "page_title", "pages_checked", "emails_found", "phones_found",
        "technology_detected", "updated_at",
    ]
    display_cols = [c for c in display_cols if c in df.columns]
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# AI Lead Analysis — Phase 3
# ---------------------------------------------------------------------------
# Same one-lead-per-rerun pattern as Website Research (see the design note
# on render_research_section above) — sequential, resumable, Stop takes
# effect between leads, nothing runs unless the user clicks Start.
def render_analysis_section():
    st.header("🤖 AI Lead Analysis")
    st.caption(
        "Phase 3 — AI-assisted business analysis using ONLY data already "
        "collected by Website Research. No re-scraping, no email "
        "generation/sending here — that's later phases."
    )

    campaigns = database.list_campaigns()
    if not campaigns:
        st.info("No campaigns yet. Create one in the Campaigns tab first.")
        return

    campaign_options = {f"{c['name']} (ID {c['id']})": c["id"] for c in campaigns}
    selected_label = st.selectbox(
        "Campaign", list(campaign_options.keys()), key="analysis_campaign_select"
    )
    campaign_id = campaign_options[selected_label]

    provider = get_provider()

    with st.expander("⚙️ Environment & AI Provider Settings", expanded=False):
        e1, e2 = st.columns(2)
        e1.write(f"**Environment:** {'COLAB' if config.IS_COLAB else 'LOCAL'}")
        e2.write(f"**Performance Mode:** `{config.PERFORMANCE_MODE}`")
        p1, p2 = st.columns(2)
        p1.metric("AI Workers", config.AI_WORKERS)
        p2.metric("Max Concurrent Tasks", config.MAX_CONCURRENT_TASKS)
        st.write(f"**Configured provider:** `{config.AI_PROVIDER}` — **active provider:** `{provider.name}` (`{provider.model_name}`)")
        if provider.name != config.AI_PROVIDER:
            st.caption(
                f"`{config.AI_PROVIDER}` isn't configured/available yet, so this run will "
                f"use the mock provider instead. See `.env.example` (GEMINI_API_KEY / "
                f"GEMINI_MODEL)."
            )
        st.caption("Lead scores are AI-assisted estimates, not an objective measure — see Analysis Reason for how each score was reached.")

    summary = database.analysis_summary_counts(campaign_id)
    st.subheader("Status")
    cols = st.columns(6)
    for col, key, disp in zip(
        cols,
        ["total_eligible", "PENDING", "PROCESSING", "COMPLETED", "FAILED", "SKIPPED"],
        ["Total Eligible", "Pending", "Processing", "Completed", "Failed", "Skipped"],
    ):
        col.metric(disp, summary.get(key, 0))

    run_key = f"analysis_running_{campaign_id}"
    queue_key = f"analysis_queue_{campaign_id}"
    stop_key = f"analysis_stop_{campaign_id}"
    stats_key = f"analysis_stats_{campaign_id}"

    st.session_state.setdefault(run_key, False)
    st.session_state.setdefault(stop_key, False)
    st.session_state.setdefault(
        stats_key, {"processed": 0, "success": 0, "failed": 0, "skipped": 0, "total": 0}
    )

    def _start(lead_ids, log_note):
        st.session_state[queue_key] = lead_ids
        st.session_state[run_key] = bool(lead_ids)
        st.session_state[stop_key] = False
        st.session_state[stats_key] = {
            "processed": 0, "success": 0, "failed": 0, "skipped": 0, "total": len(lead_ids),
        }
        logger.info(f"AI analysis {log_note}: campaign_id={campaign_id} leads={len(lead_ids)}")

    b1, b2, b3 = st.columns(3)

    if b1.button("▶️ Start Analysis", disabled=st.session_state[run_key], key="analysis_start"):
        eligible = database.get_leads_eligible_for_analysis(campaign_id, include_failed=False)
        _start([l["id"] for l in eligible], "started")
        if not st.session_state[queue_key]:
            st.info("No eligible leads (need a completed/failed/skipped Website Research result and no completed analysis yet).")
        st.rerun()

    if b2.button("⏹️ Stop After Current Lead", disabled=not st.session_state[run_key], key="analysis_stop"):
        st.session_state[stop_key] = True
        logger.info(f"AI analysis stop requested: campaign_id={campaign_id}")

    if b3.button("🔁 Retry Failed", disabled=st.session_state[run_key], key="analysis_retry_failed"):
        eligible = database.get_leads_eligible_for_analysis(campaign_id, include_failed=True)
        failed_ids = [
            l["id"] for l in eligible
            if (database.get_analysis(l["id"]) or {}).get("status") == "FAILED"
        ]
        _start(failed_ids, "retry-failed started")
        if not failed_ids:
            st.info("No failed analyses to retry.")
        st.rerun()

    with st.expander("🔄 Re-run Selected Leads"):
        analyzed = database.list_analysis(campaign_id, status="ALL", limit=500)
        options = {
            f"{a['business_name']} (ID {a['lead_id']}, {a.get('recommended_service') or 'not analyzed'})": a["lead_id"]
            for a in analyzed
        }
        picked = st.multiselect(
            "Leads to re-run (any status, including already-completed)",
            list(options.keys()),
            key=f"analysis_rerun_pick_{campaign_id}",
        )
        if st.button(
            "Re-run Selected", disabled=st.session_state[run_key] or not picked,
            key=f"analysis_rerun_selected_{campaign_id}",
        ):
            ids = [options[p] for p in picked]
            database.reset_leads_for_analysis_rerun(ids)
            _start(ids, "re-run started")
            st.rerun()

    stats = st.session_state[stats_key]
    if stats["total"]:
        st.progress(min(stats["processed"] / stats["total"], 1.0))
    st.caption(
        f"Processed: {stats['processed']}/{stats['total']} · "
        f"Success: {stats['success']} · Failed: {stats['failed']} · Skipped: {stats['skipped']}"
    )

    if st.session_state[run_key]:
        queue = st.session_state.get(queue_key, [])

        if st.session_state[stop_key] or not queue:
            st.session_state[run_key] = False
            if st.session_state[stop_key] and queue:
                st.warning(
                    f"Stopped. {len(queue)} lead(s) remain PENDING — "
                    f"press Start Analysis to resume."
                )
                logger.info(f"AI analysis stopped: campaign_id={campaign_id} remaining={len(queue)}")
            else:
                st.success("AI analysis finished for this batch.")
                logger.info(f"AI analysis batch complete: campaign_id={campaign_id}")
            st.rerun()
        else:
            lead_id = queue[0]
            lead = database.get_lead(lead_id)
            if not lead:
                st.session_state[queue_key] = queue[1:]
                st.rerun()
                return

            st.info(f"Analyzing: **{lead['business_name']}**")

            database.set_analysis_status(campaign_id, lead_id, "PROCESSING")
            logger.info(f"AI analysis started: lead_id={lead_id} business={lead['business_name']}")

            research = database.get_research(lead_id)
            result = ai_analysis.analyze_one_lead(lead, research, provider=provider)

            database.save_analysis_result(lead_id, campaign_id, result)

            stats["processed"] += 1
            if result["status"] == "COMPLETED":
                stats["success"] += 1
                logger.info(f"AI analysis completed: lead_id={lead_id} score={result.get('lead_score')} service={result.get('recommended_service')}")
            elif result["status"] == "FAILED":
                stats["failed"] += 1
                logger.info(f"AI analysis failed: lead_id={lead_id} error={result.get('error_message')}")
            else:
                stats["skipped"] += 1

            st.session_state[stats_key] = stats
            st.session_state[queue_key] = queue[1:]
            st.rerun()

    st.divider()
    st.subheader("📄 Analysis Results")
    status_filter = st.selectbox(
        "Filter by status",
        ["ALL", "PENDING", "PROCESSING", "COMPLETED", "FAILED", "SKIPPED"],
        key=f"analysis_status_filter_{campaign_id}",
    )
    results = database.list_analysis(campaign_id, status=status_filter)
    if not results:
        st.info("No analysis results yet for this filter.")
        return

    df = pd.DataFrame(results)
    display_cols = [
        "lead_id", "business_name", "recommended_service", "lead_score",
        "lead_grade", "opportunity_level", "confidence", "status", "updated_at",
    ]
    display_cols = [c for c in display_cols if c in df.columns]
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

    st.markdown("**View full analysis for one lead:**")
    detail_options = {f"{r['business_name']} (ID {r['lead_id']})": r["lead_id"] for r in results}
    detail_label = st.selectbox(
        "Lead", ["—"] + list(detail_options.keys()), key=f"analysis_detail_select_{campaign_id}"
    )
    if detail_label != "—":
        detail = database.get_analysis(detail_options[detail_label])
        if detail:
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("Lead Score", detail.get("lead_score"))
            d2.metric("Grade", detail.get("lead_grade"))
            d3.metric("Opportunity", detail.get("opportunity_level"))
            d4.metric("Confidence", detail.get("confidence"))

            st.write(f"**Business Category:** {detail.get('business_category')}")
            st.write(f"**Summary:** {detail.get('business_summary')}")
            st.write(f"**Website Platform:** {detail.get('website_platform')}")
            st.write(f"**Website Quality Observations:** {detail.get('website_quality_observations')}")
            st.write(f"**Recommended Service:** {detail.get('recommended_service')}")
            try:
                secondary = ", ".join(json.loads(detail.get("secondary_services") or "[]"))
            except (TypeError, ValueError):
                secondary = ""
            st.write(f"**Secondary Services:** {secondary or 'none'}")

            with st.expander("Per-service opportunity notes"):
                st.write(f"**SEO:** {detail.get('seo_opportunities')}")
                st.write(f"**Digital Marketing:** {detail.get('digital_marketing_opportunities')}")
                st.write(f"**Website Development:** {detail.get('website_development_opportunities')}")
                st.write(f"**Shopify Development:** {detail.get('shopify_opportunities')}")
                st.write(f"**Software Development:** {detail.get('software_development_opportunities')}")

            # analysis_reason/sales_angle already carry their own bold labels
            # ("Evidence Found" / "Why This Is An Opportunity" / "What We
            # Should Pitch" / "What's Missing / To Verify" — see
            # ai_provider/mock_provider.py) — st.markdown renders them
            # directly instead of nesting them under a second generic label.
            st.markdown(detail.get("analysis_reason") or "")
            st.markdown(detail.get("sales_angle") or "")
            st.caption(f"AI Provider: `{detail.get('ai_provider')}` · Model: `{detail.get('ai_model')}`")


# ---------------------------------------------------------------------------
# AI Outreach — Phase 4.1-4.4 (GENERATE -> REVIEW -> EDIT -> SAVE -> APPROVE)
# ---------------------------------------------------------------------------
# Single-lead workflow (not a queued batch like Website Research/AI Lead
# Analysis) — generation is one fast, synchronous, offline call per lead
# (no network, no delay needed), matching the spec's per-lead UI steps.
_OUTREACH_STATUS_LABELS = {
    "DRAFT": "Draft", "APPROVED": "Approved", "REJECTED": "Rejected", "NEEDS_REVIEW": "Needs Review",
}


def render_outreach_section():
    st.header("📧 AI Outreach")
    st.caption(
        "Phase 4 — Evidence-based personalized outreach generation. Uses ONLY "
        "data already collected/analyzed by Phases 1-3 — no re-scraping. "
        "GENERATE → REVIEW → EDIT → SAVE → APPROVE only; nothing here sends email."
    )

    campaigns = database.list_campaigns()
    if not campaigns:
        st.info("No campaigns yet. Create one in the Campaigns tab first.")
        return

    campaign_options = {f"{c['name']} (ID {c['id']})": c["id"] for c in campaigns}
    selected_label = st.selectbox(
        "Campaign", list(campaign_options.keys()), key="outreach_campaign_select"
    )
    campaign_id = campaign_options[selected_label]

    provider = get_provider()

    with st.expander("⚙️ AI Provider Settings", expanded=False):
        st.write(f"**Configured provider:** `{config.AI_PROVIDER}` — **active provider:** `{provider.name}` (`{provider.model_name}`)")
        if provider.name != config.AI_PROVIDER:
            st.caption(f"`{config.AI_PROVIDER}` isn't configured/available yet, so generation will use the mock provider instead.")
        st.caption("Drafts are generated offline from stored Phase 2/3 data — no website is re-fetched to write these emails.")

    summary = database.outreach_summary_counts(campaign_id)
    st.subheader("Status")
    cols = st.columns(5)
    for col, key, disp in zip(
        cols,
        ["total_eligible", "DRAFT", "APPROVED", "REJECTED", "NEEDS_REVIEW"],
        ["Total Eligible (analyzed)", "Draft", "Approved", "Rejected", "Needs Review"],
    ):
        col.metric(disp, summary.get(key, 0))

    eligible = database.get_leads_eligible_for_outreach(campaign_id)
    if not eligible:
        st.info("No leads with a completed AI Lead Analysis yet. Run AI Lead Analysis first.")
        return

    lead_options = {
        f"{l['business_name']} — {l.get('recommended_service') or 'Needs Review'} "
        f"({l.get('analysis_confidence') or 'LOW'}) (ID {l['id']})": l["id"]
        for l in eligible
    }
    lead_label = st.selectbox(
        "Select a lead", list(lead_options.keys()), key=f"outreach_lead_select_{campaign_id}"
    )
    lead_id = lead_options[lead_label]
    lead = database.get_lead(lead_id)
    research = database.get_research(lead_id)
    analysis = database.get_analysis(lead_id)

    if not lead or not analysis:
        st.warning("This lead's analysis record is missing — re-run AI Lead Analysis for it.")
        return

    st.divider()
    st.subheader("Why this email was generated")
    a1, a2 = st.columns(2)
    a1.metric("Recommended Service", analysis.get("recommended_service") or "Needs Review")
    a2.metric("Confidence", analysis.get("confidence") or "LOW")
    # Reuses the exact same stored Phase 3 narrative text shown in the AI
    # Lead Analysis tab (analysis_reason = "Evidence Found"; sales_angle =
    # "Why This Is An Opportunity" / "What We Should Pitch" / "What's
    # Missing / To Verify") — never re-rendered differently here, so the
    # email's justification always matches what Phase 3 already told you.
    st.markdown(analysis.get("analysis_reason") or "")
    st.markdown(analysis.get("sales_angle") or "")

    st.divider()
    draft = database.get_outreach(lead_id)
    has_draft = draft is not None

    gen_label = "🔁 Regenerate Email" if has_draft else "✉️ Generate Personalized Email"
    if st.button(gen_label, key=f"outreach_generate_{lead_id}"):
        result = ai_outreach.generate_draft_for_lead(lead, research, provider=provider)
        database.save_outreach_draft(lead_id, campaign_id, result)
        logger.info(
            f"Outreach draft generated: lead_id={lead_id} status={result['generation_status']} "
            f"service={result.get('recommended_service')}"
        )
        st.rerun()

    draft = database.get_outreach(lead_id)
    if not draft:
        st.info("No draft yet for this lead — click Generate above.")
        return

    if draft["generation_status"] == "NEEDS_REVIEW":
        st.warning(
            "**Needs Review** — no sufficiently supported service opportunity was identified "
            "for this lead. No sales pitch was generated; see the note below."
        )
        st.write(draft.get("email_body") or "")
        st.caption("Manual review is required before any outreach to this lead.")
        return

    st.subheader("📄 Preview & Edit")
    try:
        subject_options = json.loads(draft.get("subject_options") or "[]")
    except (TypeError, ValueError):
        subject_options = []

    if subject_options:
        st.caption("3 subject line options (pick one, or edit freely below):")
        picked_subject = st.radio(
            "Subject options", subject_options, index=0,
            key=f"outreach_subject_pick_{lead_id}", label_visibility="collapsed",
        )
    else:
        picked_subject = draft.get("subject") or ""

    subject_edit = st.text_input(
        "Subject", value=draft.get("subject") or picked_subject, key=f"outreach_subject_edit_{lead_id}"
    )
    body_edit = st.text_area(
        "Email body", value=draft.get("email_body") or "", height=220, key=f"outreach_body_edit_{lead_id}"
    )
    word_count = len(body_edit.split())
    st.caption(f"{word_count} words · target ~100-150")

    s1, s2, s3 = st.columns(3)
    if s1.button("💾 Save Draft", key=f"outreach_save_{lead_id}"):
        database.update_outreach_content(lead_id, subject_edit, body_edit)
        logger.info(f"Outreach draft saved (manual edit): lead_id={lead_id}")
        st.success("Saved.")
        st.rerun()
    if s2.button("✅ Mark Approved", key=f"outreach_approve_{lead_id}"):
        database.update_outreach_status(lead_id, "APPROVED")
        logger.info(f"Outreach draft approved: lead_id={lead_id}")
        st.rerun()
    if s3.button("❌ Mark Rejected", key=f"outreach_reject_{lead_id}"):
        database.update_outreach_status(lead_id, "REJECTED")
        logger.info(f"Outreach draft rejected: lead_id={lead_id}")
        st.rerun()

    st.caption(
        f"Status: **{_OUTREACH_STATUS_LABELS.get(draft['generation_status'], draft['generation_status'])}** · "
        f"Generated by `{draft.get('ai_provider')}` (`{draft.get('ai_model')}`) at {draft.get('generated_at')}"
    )

    st.divider()
    st.subheader("📋 All Drafts")
    status_filter = st.selectbox(
        "Filter by status", ["ALL", "DRAFT", "APPROVED", "REJECTED", "NEEDS_REVIEW"],
        key=f"outreach_status_filter_{campaign_id}",
    )
    all_drafts = database.list_outreach(campaign_id, status=status_filter)
    if all_drafts:
        df = pd.DataFrame(all_drafts)
        display_cols = ["lead_id", "business_name", "recommended_service", "confidence", "subject", "generation_status", "updated_at"]
        display_cols = [c for c in display_cols if c in df.columns]
        st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No drafts yet for this filter.")


# ---------------------------------------------------------------------------
# Docly — CSV import + Day 0 / Day 2 / Day 7 auto follow-up via business mail
# ---------------------------------------------------------------------------
@st.cache_resource
def _start_docly_scheduler():
    docly_scheduler.ensure_started()
    tracking_server.ensure_started()
    return True


def render_docly_section():
    st.header("📨 Docly")
    st.caption(
        "Apollo-style auto sequence: upload a CSV → Day 0 first message → "
        "Day 2 follow-up → Day 7 follow-up, sent from your business mailbox. "
        "Runs automatically in the background as long as this app stays "
        "open on your laptop. Duplicate emails are skipped automatically."
    )

    _start_docly_scheduler()

    # --- SMTP status -------------------------------------------------
    smtp_ok = config.docly_smtp_configured()
    col_a, col_b = st.columns(2)
    with col_a:
        if smtp_ok:
            st.success(f"Business mail configured: **{config.SMTP_FROM_EMAIL}**")
        else:
            st.warning(
                "Business mail not configured yet. Fill SMTP_HOST / SMTP_USER / "
                "SMTP_PASSWORD / SMTP_FROM_EMAIL in your `.env` file, then "
                "restart the app."
            )
    with col_b:
        sent_today = database.docly_sent_today_count()
        st.metric("Sent today", f"{sent_today} / {config.DOCLY_DAILY_SEND_LIMIT}")

    # --- Open tracking status --------------------------------------------
    if config.docly_tracking_ready():
        st.success(
            f"👁️ Open tracking is live at `{config.TRACKING_BASE_URL}` "
            f"(port {config.TRACKING_PORT})."
        )
    else:
        st.warning(
            "👁️ Open tracking is **off**. Set `TRACKING_ENABLED=true` and "
            "`TRACKING_BASE_URL` in `.env` to a **publicly reachable** "
            "address (e.g. an ngrok tunnel or your VPS) — recipients open "
            "email on their own device/network, so a plain `localhost` "
            "URL can never register a real open. Until this is set, the "
            "'Opened' column below will stay empty."
        )

    # --- Sending toggle ------------------------------------------------
    currently_on = database.docly_sending_enabled()
    toggle = st.toggle(
        "✅ Enable Sending (turn ON when you're ready to actually send)",
        value=currently_on,
        disabled=not smtp_ok,
        key="docly_sending_toggle",
    )
    if toggle != currently_on:
        database.docly_set_sending_enabled(toggle)
        st.rerun()

    if not config.SMTP_ENABLED:
        st.info(
            "Note: `SMTP_ENABLED=false` in your `.env` — set it to `true` "
            "there as well (in addition to the toggle above) before sending "
            "will actually go out."
        )

    st.divider()

    # --- Message templates ---------------------------------------------
    st.subheader("✉️ Message Templates")
    st.caption("Use `{business_name}` anywhere — it's replaced automatically per contact.")

    t1 = st.text_area(
        "Day 0 — First message", value=database.docly_get_template(1, ""),
        height=120, key="docly_template_1",
    )
    t2 = st.text_area(
        "Day 2 — Follow-up", value=database.docly_get_template(2, ""),
        height=120, key="docly_template_2",
    )
    t3 = st.text_area(
        "Day 7 — Final follow-up", value=database.docly_get_template(3, ""),
        height=120, key="docly_template_3",
    )
    if st.button("💾 Save Templates", key="docly_save_templates"):
        database.docly_set_template(1, t1)
        database.docly_set_template(2, t2)
        database.docly_set_template(3, t3)
        st.success("Templates saved.")

    st.divider()

    # --- CSV import ------------------------------------------------------
    st.subheader("📤 Import Contacts (CSV)")
    st.caption("CSV needs a `business_name` column and an `email` column.")
    uploaded = st.file_uploader("Upload CSV", type=["csv"], key="docly_csv_uploader")
    if uploaded is not None:
        try:
            df = pd.read_csv(uploaded)
        except Exception as exc:
            st.error(f"Could not read CSV: {exc}")
            df = None

        if df is not None:
            cols_lower = {c.lower().strip(): c for c in df.columns}
            name_col = cols_lower.get("business_name") or cols_lower.get("name")
            email_col = cols_lower.get("email")

            if not email_col:
                st.error("CSV must have an `email` column.")
            else:
                st.dataframe(df.head(10), use_container_width=True, hide_index=True)
                if st.button("📥 Import & Start Sequence", key="docly_import_btn"):
                    rows = [
                        {
                            "business_name": str(row.get(name_col, "")).strip() if name_col else "",
                            "email": str(row.get(email_col, "")).strip(),
                        }
                        for _, row in df.iterrows()
                    ]
                    result = database.docly_import_contacts(
                        rows, source_batch=uploaded.name
                    )
                    st.success(
                        f"Imported {result['imported']} new contact(s). "
                        f"Skipped {result['duplicates_skipped']} duplicate(s), "
                        f"{result['invalid_skipped']} invalid row(s)."
                    )

    st.divider()

    # --- Manual check + sequence status ---------------------------------
    st.subheader("🔄 Sequence Status")
    if st.button("Check & Send Due Messages Now", key="docly_check_now"):
        result = docly_scheduler.run_one_check()
        st.info(
            f"Checked {result['checked']} due contact(s) — "
            f"sent {result['sent']}, failed {result['failed']}, "
            f"skipped (sending off) {result['skipped_disabled']}, "
            f"skipped (daily limit) {result['skipped_limit']}."
        )

    sequences = database.docly_get_all_sequences()
    if sequences:
        open_status = database.docly_get_open_status()
        for s in sequences:
            last_open = open_status.get(s["contact_id"])
            s["opened"] = f"👁️ {last_open}" if last_open else "—"

        df_seq = pd.DataFrame(sequences)
        display_cols = [
            "business_name", "email", "crm_stage", "status", "current_step",
            "opened", "next_send_at", "last_sent_at",
        ]
        display_cols = [c for c in display_cols if c in df_seq.columns]
        st.dataframe(
            df_seq[display_cols].rename(columns={"crm_stage": "CRM Stage"}),
            use_container_width=True, hide_index=True,
        )

        col_stop, col_stage = st.columns(2)

        with col_stop:
            stop_email = st.text_input(
                "Stop sequence for this email (e.g. they replied)", key="docly_stop_email"
            )
            if st.button("⛔ Stop Sequence", key="docly_stop_btn") and stop_email:
                norm = database.normalize_email(stop_email)
                match = next(
                    (s for s in sequences if database.normalize_email(s["email"]) == norm),
                    None,
                )
                if match:
                    database.docly_stop_sequence(match["contact_id"], reason="Manually stopped")
                    st.success(f"Sequence stopped for {stop_email}.")
                    st.rerun()
                else:
                    st.warning("No matching contact found.")

        with col_stage:
            st.caption(
                "Update CRM stage manually (Replied/Interested/Meeting Booked/"
                "Won/Lost — the app can't detect these on its own)."
            )
            stage_email = st.text_input("Contact email", key="docly_stage_email")
            new_stage = st.selectbox(
                "New stage", database.DOCLY_CRM_STAGES, key="docly_stage_pick"
            )
            if st.button("📌 Update Stage", key="docly_stage_btn") and stage_email:
                contact = database.docly_get_contact_by_email(stage_email)
                if contact:
                    database.docly_set_crm_stage(contact["id"], new_stage)
                    st.success(f"{stage_email} marked as **{new_stage}**.")
                    st.rerun()
                else:
                    st.warning("No matching contact found.")
    else:
        st.info("No contacts imported yet.")

    st.divider()
    st.subheader("📜 Recent Send Log")
    log = database.docly_get_recent_log(limit=30)
    if log:
        df_log = pd.DataFrame(log)
        display_cols = ["sent_at", "business_name", "email", "step", "subject", "status", "error_message"]
        display_cols = [c for c in display_cols if c in df_log.columns]
        st.dataframe(df_log[display_cols], use_container_width=True, hide_index=True)
    else:
        st.caption("Nothing sent yet.")


# ---------------------------------------------------------------------------
# Reporting — ROI dashboard (Phase 1-4 pipeline funnel + Docly funnel/opens)
# ---------------------------------------------------------------------------
def render_reporting_section():
    st.header("📈 Reporting")
    st.caption(
        "Read-only rollups over data already collected elsewhere in the "
        "app — nothing here sends anything or changes any record."
    )

    campaigns = database.list_campaigns()

    st.subheader("🔻 Campaign Pipeline Funnel (Phases 1–4)")
    if not campaigns:
        st.info("No campaigns yet — create one in the Campaigns tab first.")
    else:
        campaign_map = {c["name"]: c["id"] for c in campaigns}
        picked_name = st.selectbox(
            "Campaign", list(campaign_map.keys()), key="reporting_campaign_pick"
        )
        campaign_id = campaign_map[picked_name]
        funnel = database.reporting_pipeline_funnel(campaign_id)

        cols = st.columns(5)
        labels = [
            ("Imported", "imported"), ("Researched", "researched"),
            ("Analyzed", "analyzed"), ("Drafted", "drafted"),
            ("Approved", "approved"),
        ]
        for col, (label, key) in zip(cols, labels):
            col.metric(label, funnel[key])

        chart_df = pd.DataFrame(
            {"Stage": [l for l, _ in labels], "Count": [funnel[k] for _, k in labels]}
        ).set_index("Stage")
        st.bar_chart(chart_df)

        if funnel["imported"] > 0:
            approval_rate = funnel["approved"] / funnel["imported"] * 100
            st.caption(
                f"{approval_rate:.0f}% of imported leads in this campaign have "
                f"reached an **approved, ready-to-send** outreach draft."
            )

    st.divider()

    st.subheader("📨 Docly Sending & Open-Rate ROI")
    overview = database.reporting_docly_overview()

    col1, col2, col3 = st.columns(3)
    col1.metric("Total contacts", overview["total_contacts"])
    col2.metric("Emails sent", overview["total_sent"])
    open_rate = (
        overview["unique_opened_contacts"] / overview["total_contacts"] * 100
        if overview["total_contacts"] else 0
    )
    col3.metric("Unique open rate", f"{open_rate:.0f}%")

    if not config.docly_tracking_ready():
        st.caption(
            "⚠️ Open tracking is currently off/unreachable, so the open-rate "
            "number above will stay at 0% until `TRACKING_BASE_URL` is set "
            "to a public address (see the Docly tab)."
        )

    st.markdown("**Lead pipeline (CRM stages)**")
    stage_counts = overview["stage_counts"]
    stage_df = pd.DataFrame(
        {"Stage": list(stage_counts.keys()), "Contacts": list(stage_counts.values())}
    ).set_index("Stage")
    st.bar_chart(stage_df)

    won = stage_counts.get("Won", 0)
    if overview["total_sent"] > 0:
        st.caption(
            f"**{won} deal(s) won** out of {overview['total_contacts']} contacts "
            f"and {overview['total_sent']} emails sent so far. Update these "
            f"stages from the Docly tab as replies come in."
        )

    step_stats = overview["step_stats"]
    if step_stats:
        st.markdown("**Per-step performance (Day 0 / Day 2 / Day 7)**")
        step_names = {1: "Day 0", 2: "Day 2", 3: "Day 7"}
        rows = []
        for step, stats in sorted(step_stats.items()):
            sent = stats.get("sent", 0)
            opened = stats.get("opened", 0)
            rate = f"{(opened / sent * 100):.0f}%" if sent else "—"
            rows.append(
                {"Step": step_names.get(step, f"Step {step}"), "Sent": sent,
                 "Opened": opened, "Open rate": rate}
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Optional password gate — active only if a password is configured.
# Checked in this order: Streamlit Community Cloud "Secrets" (st.secrets)
# first, then the local .env value (config.APP_PASSWORD). Leaving both
# unset means the app runs open, exactly as before (local-only use).
# ---------------------------------------------------------------------------
def _configured_password() -> str:
    try:
        secret_pw = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        secret_pw = ""
    return secret_pw or config.APP_PASSWORD


def require_login():
    required_password = _configured_password()
    if not required_password:
        return  # no password configured — app stays open (local default)

    if st.session_state.get("authenticated"):
        return

    st.markdown(
        """
        <div class="fivegen-banner">
            <h1>📋 5thGenLeadGenerator</h1>
            <p>Private dashboard — sign in to continue</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.form("login_form"):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        if entered == required_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()


def main():
    require_login()
    selected = render_sidebar()

    page_renderers = {
        "📁 Campaigns": render_campaign_section,
        "📥 Import Leads": render_import_section,
        "📊 Leads & Status": render_leads_section,
        "🔍 Website Research": render_research_section,
        "🤖 AI Lead Analysis": render_analysis_section,
        "📧 AI Outreach": render_outreach_section,
        "📨 Docly": render_docly_section,
        "📈 Reporting": render_reporting_section,
    }
    page_renderers[selected]()


if __name__ == "__main__":
    main()

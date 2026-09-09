"""
docly_scheduler.py
====================
5thGenLeadGenerator — Docly background follow-up scheduler.

Runs as a single daemon thread for the life of the Streamlit process
(`streamlit run app.py`). Every DOCLY_POLL_SECONDS it:
    1. Checks the "Enable Sending" toggle + .env SMTP_ENABLED — if either
       is off, it does nothing this tick (just waits for the next one).
    2. Finds sequences whose next_send_at has arrived.
    3. Sends the right template for the step that's due
       (Day 0 -> template_step_1, Day 2 -> template_step_2, Day 7 -> template_step_3).
    4. Advances the sequence to the next step/date, or marks it COMPLETED
       after the Day 7 follow-up.
    5. Respects config.DOCLY_DAILY_SEND_LIMIT (stops sending for the rest
       of the day once hit, resumes automatically the next day).

IMPORTANT: this only runs while the Streamlit process is running. If you
close the terminal / stop `streamlit run`, follow-ups pause until you
start it again — nothing is lost, sequences simply resume from wherever
they were.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

import config
import database
import smtp_sender
import tracking_server
from logger_setup import get_logger

logger = get_logger()

_scheduler_started = False
_scheduler_lock = threading.Lock()

# See bulkreach_scheduler.py's identical comment: this serializes actual
# send-check runs so a manual button click and the background thread's
# tick can never both send the same due contact at once.
_run_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _personalize(template: str, business_name: str) -> str:
    business_name = business_name or "there"
    return (template or "").replace("{business_name}", business_name)


def _first_line_as_subject(body: str, fallback: str) -> str:
    for line in (body or "").splitlines():
        line = line.strip()
        if line:
            return line[:120]
    return fallback


def run_one_check() -> dict:
    """
    Run a single due-check-and-send pass. Safe to call manually (e.g. from
    a "Check & Send Now" button) as well as from the background loop —
    serialized via _run_lock so it can never double-send a due contact.
    """
    with _run_lock:
        return _run_one_check_locked()


def _run_one_check_locked() -> dict:
    """Returns a small summary dict for display."""
    summary = {"checked": 0, "sent": 0, "failed": 0, "skipped_disabled": 0, "skipped_limit": 0}

    sending_on = config.SMTP_ENABLED and database.docly_sending_enabled()
    if not config.docly_smtp_configured():
        sending_on = False

    due = database.docly_get_due_sequences(limit=100)
    summary["checked"] = len(due)

    if not due:
        return summary

    if not sending_on:
        for seq in due:
            database.docly_log_send(
                seq["contact_id"], seq["current_step"] + 1, None, None,
                "SKIPPED_DISABLED",
                "Sending is off (.env SMTP_ENABLED or the Docly toggle is off)",
            )
        summary["skipped_disabled"] = len(due)
        return summary

    sent_today = database.docly_sent_today_count()

    for seq in due:
        if sent_today >= config.DOCLY_DAILY_SEND_LIMIT:
            database.docly_log_send(
                seq["contact_id"], seq["current_step"] + 1, None, None,
                "SKIPPED_LIMIT",
                f"Daily send limit ({config.DOCLY_DAILY_SEND_LIMIT}) reached",
            )
            summary["skipped_limit"] += 1
            continue

        next_step = seq["current_step"] + 1  # 1, 2, or 3
        template = database.docly_get_template(next_step, "")
        if not template.strip():
            # No template written for this step yet — skip, try again next tick.
            continue

        body = _personalize(template, seq["business_name"])
        subject_fallback = f"Following up — {seq['business_name'] or ''}".strip()
        subject = _first_line_as_subject(body, subject_fallback) if next_step == 1 else subject_fallback

        success, error = smtp_sender.send_email(
            seq["email"], subject, body,
            pixel_url=tracking_server.pixel_url(seq["contact_id"], next_step),
        )
        database.docly_log_send(
            seq["contact_id"], next_step, subject, body,
            "SENT" if success else "FAILED", error,
        )

        if success:
            sent_today += 1
            summary["sent"] += 1
            if next_step >= 3:
                database.docly_advance_sequence(
                    seq["contact_id"], next_step, seq["next_send_at"], status="COMPLETED"
                )
            else:
                days_ahead = (
                    config.DOCLY_FOLLOWUP_DAY_2 if next_step == 1
                    else config.DOCLY_FOLLOWUP_DAY_7
                )
                # Both offsets are measured from Day 0 (contact creation),
                # matching "Day 0 / Day 2 / Day 7" exactly rather than
                # compounding from whenever this send happened to run.
                contact_created = seq.get("created_at") or _now_iso()
                try:
                    base_dt = datetime.fromisoformat(contact_created)
                except ValueError:
                    base_dt = datetime.now(timezone.utc)
                next_dt = base_dt + timedelta(days=days_ahead)
                database.docly_advance_sequence(
                    seq["contact_id"], next_step, next_dt.isoformat(timespec="seconds"),
                    status="ACTIVE",
                )
        else:
            summary["failed"] += 1
            # Leave next_send_at as-is so a transient SMTP error (e.g. a
            # dropped connection) gets retried on the next poll instead of
            # silently skipping this contact's step forever.

    return summary


def _loop():
    logger.info(
        f"Docly scheduler thread started (poll every {config.DOCLY_POLL_SECONDS}s)."
    )
    while True:
        try:
            result = run_one_check()
            if result["checked"]:
                logger.info(f"Docly scheduler tick: {result}")
        except Exception as exc:
            logger.error(f"Docly scheduler tick failed: {exc}")
        time.sleep(max(config.DOCLY_POLL_SECONDS, 15))


def ensure_started():
    """
    Start the background thread exactly once per process. Streamlit
    reruns this module's importing script on every interaction, so this
    guard (module-level flag + lock) prevents spawning duplicate threads.
    Call this from app.py wrapped in @st.cache_resource for a second
    layer of "only once" safety.
    """
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        thread = threading.Thread(target=_loop, name="docly-scheduler", daemon=True)
        thread.start()
        _scheduler_started = True

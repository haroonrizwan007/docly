"""
bulkreach_scheduler.py
========================
5thGenLeadGenerator — BulkReach background scheduler.

Same daemon-thread pattern as docly_scheduler.py, but with one important
difference: newly imported contacts don't start their sequence right
away. They sit QUEUED, and this scheduler releases up to
bulkreach_get_daily_limit() of them per day (oldest-imported first),
sending each one's Day 0 message the moment it's released. Once
released, a contact follows the exact same Day 0 -> Day 2 -> Day 7 flow
as Docly.

Every tick:
    1. Check the "Enable Sending" toggle + .env SMTP_ENABLED.
    2. Send any already-ACTIVE sequence whose Day 2 / Day 7 follow-up is
       due (existing contacts take priority over releasing new ones).
    3. With whatever's left of today's daily limit, release that many
       QUEUED contacts to ACTIVE and send their Day 0 message
       immediately, in the same pass.
    4. Whatever's still QUEUED stays QUEUED — it's picked up automatically
       on a later day once today's quota resets.

IMPORTANT: like Docly, this only runs while the Streamlit process is
running. Closing it pauses everything; nothing is lost, it resumes
exactly where it left off.
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _personalize(html: str, business_name: str) -> str:
    business_name = business_name or "there"
    return (html or "").replace("{business_name}", business_name)


def _default_subject(step: int, business_name: str) -> str:
    if step == 1:
        return f"A quick idea for {business_name}".strip() if business_name else "A quick idea for your business"
    return f"Following up — {business_name}".strip() if business_name else "Following up"


def _send_step(seq: dict, step: int, summary: dict):
    template_html = database.bulkreach_get_template(step, "")
    if not template_html.strip():
        return  # nothing written for this step yet — leave it due, retry next tick

    subject_override = database.bulkreach_get_subject(step, "").strip()
    business_name = seq.get("business_name") or ""
    subject = _personalize(subject_override, business_name) or _default_subject(step, business_name)
    html_body = _personalize(template_html, business_name)

    success, error = smtp_sender.send_html_email(
        seq["email"], subject, html_body,
        pixel_url=tracking_server.pixel_url(seq["contact_id"], step, system="bulkreach"),
    )
    database.bulkreach_log_send(
        seq["contact_id"], step, subject, html_body,
        "SENT" if success else "FAILED", error,
    )

    if success:
        summary["sent"] += 1
        if step >= 3:
            database.bulkreach_advance_sequence(
                seq["contact_id"], step, seq["next_send_at"], status="COMPLETED"
            )
        else:
            # Day 0 -> Day 2 offset is measured from right now (the
            # moment Day 0 actually went out). Day 2 -> Day 7 offset is
            # measured from the stored Day 0 send time (last_sent_at),
            # not from whenever Day 2 happened to fire — keeps "Day 7"
            # meaning Day 7 from first contact, not from the last email.
            if step == 1:
                base_dt = datetime.now(timezone.utc)
                days_ahead = config.DOCLY_FOLLOWUP_DAY_2
            else:
                try:
                    base_dt = datetime.fromisoformat(seq.get("last_sent_at") or "")
                except ValueError:
                    base_dt = datetime.now(timezone.utc)
                days_ahead = config.DOCLY_FOLLOWUP_DAY_7 - config.DOCLY_FOLLOWUP_DAY_2
            next_dt = base_dt + timedelta(days=max(days_ahead, 0))
            database.bulkreach_advance_sequence(
                seq["contact_id"], step, next_dt.isoformat(timespec="seconds"),
                status="ACTIVE",
            )
    else:
        summary["failed"] += 1
        # Leave next_send_at as-is so a transient SMTP error gets retried
        # next tick instead of silently skipping this contact forever.


def run_one_check() -> dict:
    """
    Run a single due-check-and-send pass. Safe to call manually (e.g. from
    a "Check & Send Now" button) as well as from the background loop.
    """
    summary = {
        "checked": 0, "sent": 0, "failed": 0,
        "skipped_disabled": 0, "skipped_limit": 0, "released": 0,
    }

    sending_on = config.SMTP_ENABLED and database.bulkreach_sending_enabled()
    if not config.docly_smtp_configured():
        sending_on = False

    if not sending_on:
        due = database.bulkreach_get_due_sequences(limit=200)
        summary["checked"] = len(due)
        for seq in due:
            database.bulkreach_log_send(
                seq["contact_id"], seq["current_step"] + 1, None, None,
                "SKIPPED_DISABLED",
                "Sending is off (.env SMTP_ENABLED or the BulkReach toggle is off)",
            )
        summary["skipped_disabled"] = len(due)
        return summary

    daily_limit = database.bulkreach_get_daily_limit()
    sent_today = database.bulkreach_sent_today_count()
    quota = daily_limit - sent_today

    if quota <= 0:
        due = database.bulkreach_get_due_sequences(limit=200)
        summary["checked"] = len(due)
        for seq in due:
            database.bulkreach_log_send(
                seq["contact_id"], seq["current_step"] + 1, None, None,
                "SKIPPED_LIMIT",
                f"Daily send limit ({daily_limit}) reached",
            )
        summary["skipped_limit"] = len(due)
        return summary

    # 1) Existing follow-ups (Day 2 / Day 7) already due — these take
    #    priority over releasing brand-new contacts.
    due = database.bulkreach_get_due_sequences(limit=quota)
    summary["checked"] += len(due)
    for seq in due:
        _send_step(seq, seq["current_step"] + 1, summary)

    sent_this_pass = summary["sent"]
    remaining = quota - sent_this_pass

    # 2) Fill the rest of today's quota by releasing QUEUED contacts
    #    (oldest-imported first) and sending their Day 0 immediately.
    if remaining > 0:
        released = database.bulkreach_release_queue_batch(remaining)
        summary["released"] = len(released)
        summary["checked"] += len(released)
        for seq in released:
            _send_step(seq, 1, summary)

    return summary


def _loop():
    logger.info(
        f"BulkReach scheduler thread started (poll every {config.DOCLY_POLL_SECONDS}s)."
    )
    while True:
        try:
            result = run_one_check()
            if result["checked"]:
                logger.info(f"BulkReach scheduler tick: {result}")
        except Exception as exc:
            logger.error(f"BulkReach scheduler tick failed: {exc}")
        time.sleep(max(config.DOCLY_POLL_SECONDS, 15))


def ensure_started():
    """Start the background thread exactly once per process (same guard
    pattern as docly_scheduler.ensure_started())."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        thread = threading.Thread(target=_loop, name="bulkreach-scheduler", daemon=True)
        thread.start()
        _scheduler_started = True

"""
roofing_scheduler.py
======================
5thGenLeadGenerator — Roofing background scheduler + manual send flow.

Same queued-daily-limit-and-follow-up model as bulkreach_scheduler.py
(own tables, Day 0 -> Day 2 -> Day 7 automatic follow-up, daily send
cap), with one addition: send_now() lets the UI trigger today's Day-0
batch RIGHT NOW, synchronously, reporting progress after every single
email via a callback — this is what powers the "Send Mail" button's
live progress bar. The background thread still runs independently for
Day 2 / Day 7 follow-ups (and to auto-continue releasing queued
contacts on days the user doesn't click the button), using the exact
same _run_lock as send_now() so the two can never double-send the same
contact.
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

# Serializes actual send-check runs (background tick vs. the manual
# "Send Mail" button vs. each other) so the same due/queued contact can
# never be sent to twice at once.
_run_lock = threading.Lock()


def _personalize(html: str, business_name: str) -> str:
    business_name = business_name or "there"
    return (html or "").replace("{business_name}", business_name)


def _default_subject(step: int, business_name: str) -> str:
    if step == 1:
        return f"A quick idea for {business_name}".strip() if business_name else "A quick idea for your business"
    return f"Following up — {business_name}".strip() if business_name else "Following up"


def _send_step(seq: dict, step: int, summary: dict):
    template_html = database.roofing_get_template(step, "")
    if not template_html.strip():
        return  # nothing written for this step yet — leave it due, retry next tick

    subject_override = database.roofing_get_subject(step, "").strip()
    business_name = seq.get("business_name") or ""
    subject = _personalize(subject_override, business_name) or _default_subject(step, business_name)
    html_body = _personalize(template_html, business_name)

    success, error = smtp_sender.send_html_email(
        seq["email"], subject, html_body,
        pixel_url=tracking_server.pixel_url(seq["contact_id"], step, system="roofing"),
    )
    database.roofing_log_send(
        seq["contact_id"], step, subject, html_body,
        "SENT" if success else "FAILED", error,
    )

    if success:
        summary["sent"] += 1
        if step >= 3:
            database.roofing_advance_sequence(
                seq["contact_id"], step, seq["next_send_at"], status="COMPLETED"
            )
        else:
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
            database.roofing_advance_sequence(
                seq["contact_id"], step, next_dt.isoformat(timespec="seconds"),
                status="ACTIVE",
            )
    else:
        summary["failed"] += 1


def _sending_ready() -> bool:
    sending_on = config.SMTP_ENABLED and database.roofing_sending_enabled()
    if not config.docly_smtp_configured():
        sending_on = False
    return sending_on


def run_one_check() -> dict:
    """
    Background-thread version: processes due follow-ups, then fills any
    remaining daily quota by auto-releasing queued contacts — same as
    BulkReach. This is what keeps Day2/Day7 flowing and keeps the queue
    draining on days the user doesn't manually click "Send Mail".
    """
    with _run_lock:
        return _run_one_check_locked()


def _run_one_check_locked() -> dict:
    summary = {
        "checked": 0, "sent": 0, "failed": 0,
        "skipped_disabled": 0, "skipped_limit": 0, "released": 0,
    }

    if not _sending_ready():
        due = database.roofing_get_due_sequences(limit=200)
        summary["checked"] = len(due)
        for seq in due:
            database.roofing_log_send(
                seq["contact_id"], seq["current_step"] + 1, None, None,
                "SKIPPED_DISABLED",
                "Sending is off (.env SMTP_ENABLED or the Roofing toggle is off)",
            )
        summary["skipped_disabled"] = len(due)
        return summary

    daily_limit = database.roofing_get_daily_limit()
    sent_today = database.roofing_sent_today_count()
    quota = daily_limit - sent_today

    if quota <= 0:
        due = database.roofing_get_due_sequences(limit=200)
        summary["checked"] = len(due)
        for seq in due:
            database.roofing_log_send(
                seq["contact_id"], seq["current_step"] + 1, None, None,
                "SKIPPED_LIMIT",
                f"Daily send limit ({daily_limit}) reached",
            )
        summary["skipped_limit"] = len(due)
        return summary

    due = database.roofing_get_due_sequences(limit=quota)
    summary["checked"] += len(due)
    for seq in due:
        _send_step(seq, seq["current_step"] + 1, summary)

    sent_this_pass = summary["sent"]
    remaining = quota - sent_this_pass

    if remaining > 0:
        candidates = database.roofing_get_queued_candidates(limit=remaining)
        if candidates:
            database.roofing_activate_sequences([s["id"] for s in candidates])
            summary["released"] = len(candidates)
            summary["checked"] += len(candidates)
            for seq in candidates:
                _send_step(seq, 1, summary)

    return summary


def send_now(progress_callback=None) -> dict:
    """
    Foreground/manual version for the "Send Mail" button: sends today's
    batch RIGHT NOW (due follow-ups first, then releasing queued
    contacts), calling progress_callback(done, total) after every single
    send so the UI can render a live progress bar. Shares _run_lock with
    the background thread so the two can never double-send.
    """
    with _run_lock:
        summary = {
            "checked": 0, "sent": 0, "failed": 0,
            "skipped_disabled": 0, "skipped_limit": 0, "released": 0,
        }

        if not _sending_ready():
            summary["skipped_disabled"] = -1  # signal: nothing attempted, sending is off
            return summary

        daily_limit = database.roofing_get_daily_limit()
        sent_today = database.roofing_sent_today_count()
        quota = daily_limit - sent_today
        if quota <= 0:
            summary["skipped_limit"] = -1  # signal: nothing attempted, limit already hit
            return summary

        due = database.roofing_get_due_sequences(limit=quota)
        remaining_after_due = quota - len(due)
        candidates = (
            database.roofing_get_queued_candidates(limit=max(remaining_after_due, 0))
            if remaining_after_due > 0 else []
        )

        batch = [(seq, seq["current_step"] + 1) for seq in due]
        if candidates:
            database.roofing_activate_sequences([s["id"] for s in candidates])
            batch += [(seq, 1) for seq in candidates]
            summary["released"] = len(candidates)

        total = len(batch)
        summary["checked"] = total
        for i, (seq, step) in enumerate(batch, start=1):
            _send_step(seq, step, summary)
            if progress_callback:
                progress_callback(i, total)

        return summary


def _loop():
    logger.info(
        f"Roofing scheduler thread started (poll every {config.DOCLY_POLL_SECONDS}s)."
    )
    while True:
        try:
            result = run_one_check()
            if result["checked"]:
                logger.info(f"Roofing scheduler tick: {result}")
        except Exception as exc:
            logger.error(f"Roofing scheduler tick failed: {exc}")
        time.sleep(max(config.DOCLY_POLL_SECONDS, 15))


def ensure_started():
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        thread = threading.Thread(target=_loop, name="roofing-scheduler", daemon=True)
        thread.start()
        _scheduler_started = True

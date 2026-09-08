"""
smtp_sender.py
===============
5thGenLeadGenerator — Docly business-mail sending.

Thin, defensive wrapper around smtplib. Every call is guarded so nothing
is ever sent unless BOTH:
    1. config.SMTP_ENABLED is true (set in .env), AND
    2. database.docly_sending_enabled() is true (the in-app toggle)
Callers (docly_scheduler.py) are expected to check these before calling
send_email(), but send_email() re-checks config.docly_smtp_configured()
regardless, so a half-filled .env can never silently "succeed".
"""

import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import config
from logger_setup import get_logger

logger = get_logger()


def _deliver(msg, to_email: str) -> tuple[bool, str | None]:
    """Shared SMTP delivery for both plain-text (Docly) and HTML (BulkReach) messages."""
    try:
        if config.SMTP_USE_TLS:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.sendmail(config.SMTP_FROM_EMAIL, [to_email], msg.as_string())
        else:
            with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as server:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.sendmail(config.SMTP_FROM_EMAIL, [to_email], msg.as_string())
        return True, None
    except Exception as exc:
        return False, str(exc)


def send_email(to_email: str, subject: str, body_text: str,
                pixel_url: str = None) -> tuple[bool, str | None]:
    """
    Send one email via the configured business mailbox.

    If pixel_url is given, sends as multipart/alternative (plain text +
    an HTML version with the tracking pixel embedded at the end) so mail
    clients that render HTML can register an open. If pixel_url is None,
    sends plain text only.

    Returns (success, error_message). error_message is None on success.
    """
    if not config.docly_smtp_configured():
        return False, "SMTP not configured (SMTP_HOST/USER/PASSWORD/FROM_EMAIL missing in .env)"

    if not to_email or "@" not in to_email:
        return False, f"Invalid recipient address: {to_email!r}"

    body_text = body_text or ""

    if pixel_url:
        msg = MIMEMultipart("alternative")
        html_body = (
            body_text.replace("\n", "<br>")
            + f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:none">'
        )
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        msg = MIMEText(body_text, "plain", "utf-8")

    msg["Subject"] = subject or "(no subject)"
    msg["From"] = formataddr((config.SMTP_FROM_NAME, config.SMTP_FROM_EMAIL))
    msg["To"] = to_email

    success, error = _deliver(msg, to_email)
    if success:
        logger.info(f"Docly: email sent to {to_email} (subject={subject!r})")
    else:
        logger.error(f"Docly: send failed to {to_email}: {error}")
    return success, error


def send_html_email(to_email: str, subject: str, html_body: str,
                     pixel_url: str = None) -> tuple[bool, str | None]:
    """
    Send one raw-HTML email (BulkReach). Unlike send_email(), html_body is
    treated as a real HTML document the user pasted in — it is NOT
    converted from plain text. A crude tag-stripped plain-text version is
    generated as the multipart fallback so non-HTML mail clients still get
    something readable. The tracking pixel is inserted just before
    </body> if present, otherwise appended to the end.
    """
    if not config.docly_smtp_configured():
        return False, "SMTP not configured (SMTP_HOST/USER/PASSWORD/FROM_EMAIL missing)"

    if not to_email or "@" not in to_email:
        return False, f"Invalid recipient address: {to_email!r}"

    html_body = html_body or ""
    plain_fallback = re.sub(r"<[^>]+>", " ", html_body)
    plain_fallback = re.sub(r"\s+", " ", plain_fallback).strip()

    if pixel_url:
        pixel_tag = f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:none">'
        lower = html_body.lower()
        if "</body>" in lower:
            idx = lower.rindex("</body>")
            html_body = html_body[:idx] + pixel_tag + html_body[idx:]
        else:
            html_body = html_body + pixel_tag

    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(plain_fallback or "(open in an HTML-capable mail client)", "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    msg["Subject"] = subject or "(no subject)"
    msg["From"] = formataddr((config.SMTP_FROM_NAME, config.SMTP_FROM_EMAIL))
    msg["To"] = to_email

    success, error = _deliver(msg, to_email)
    if success:
        logger.info(f"BulkReach: email sent to {to_email} (subject={subject!r})")
    else:
        logger.error(f"BulkReach: send failed to {to_email}: {error}")
    return success, error

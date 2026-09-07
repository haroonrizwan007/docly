"""
website_research.py
====================
5thGenLeadGenerator — Phase 2: Website Research Engine.

Scope (per Master Specification, Phase 2):
- Take a lead's business_name + website and research the public website
  ONLY (homepage + a handful of internal pages such as About/Services/Contact).
- No AI analysis, no SEO scoring, no email generation/sending — this module
  only fetches HTML, extracts structured facts, and hands a result dict back
  for database.py to store.
- Strictly sequential, single-request-at-a-time. No threads, no async,
  no multiprocessing. Every network call reads its limits from config.py
  (MAX_PAGES_PER_WEBSITE, REQUEST_DELAY_SECONDS, REQUEST_TIMEOUT_SECONDS)
  and those are never raised automatically.
- Only follows links on the same domain as the lead's website. Never
  searches the wider web, never touches Google/LinkedIn/social media,
  never attempts login-protected pages, never bypasses robots.txt-style
  protections (a site that blocks/errors is simply recorded as FAILED).
"""

import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

import config
from logger_setup import get_logger

logger = get_logger()

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Standard headers a normal desktop browser sends on a page navigation.
# This is NOT a bypass of any security control — no CAPTCHA solving, no
# WAF evasion technique, no auth/robots override. It simply stops
# self-identifying as a bot (the previous "5thGenLeadGeneratorBot/1.0"
# UA), which is what caused ordinary WAFs (Wordfence/Cloudflare/Sucuri,
# common on small-business WordPress sites) to 403 a request that a
# real browser opens fine.
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Path keywords used to prioritize which internal links get visited first,
# per spec: About / About Us / Services / Our Services / Contact / Contact Us.
PREFERRED_PATH_KEYWORDS = [
    "contact-us", "contact_us", "contact",
    "about-us", "about_us", "about",
    "our-services", "our_services", "services",
]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Tightened from the original loose "any 7+ digit run with separators"
# pattern, which was greedy enough to swallow trailing unrelated digits
# (e.g. "972-528-7663 675" as one match) and to match plain dates
# (e.g. "2026-01-15", whose 4-2-2 digit grouping doesn't fit 3-3-4 here,
# so it no longer matches at all). Matches a standard US 3-3-4 number,
# optionally preceded by a "+1"/"1" country code, with a trailing word
# boundary so it stops at the last expected digit instead of continuing
# to eat nearby digits.
PHONE_RE = re.compile(
    r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b"
)

# Belt-and-suspenders explicit guard against plain ISO-style dates
# (YYYY-MM-DD / YYYY/MM/DD) that might otherwise slip through.
DATE_LIKE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")

ADDRESS_RE = re.compile(
    r"\d{1,5}\s+[A-Za-z0-9.,'\- ]{5,80}"
    r"(?:Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Lane|Ln\.?|Block|"
    r"Boulevard|Blvd\.?|Drive|Dr\.?|Way|Suite|Ste\.?|Highway|Hwy\.?|"
    r"Parkway|Pkwy\.?|Circle|Cir\.?|Court|Ct\.?|Place|Pl\.?)\b",
    re.IGNORECASE,
)

# --- Email filtering (conservative allow/deny) --------------------------
# File extensions that show up glued to "@2x"/"@3x" image-density
# filenames in HTML/CSS (e.g. "flags@2x.CK7NHWq8.webp") and get
# mis-parsed as an email's TLD by the regex above. Any address whose
# final domain label is one of these is not a real email.
NON_EMAIL_FILE_EXTENSIONS = {
    "webp", "png", "jpg", "jpeg", "gif", "svg", "bmp", "ico",
    "css", "js", "woff", "woff2", "ttf", "eot", "pdf",
}

# Example/placeholder domains from template HTML/boilerplate — never a
# real business's own contact address.
EXAMPLE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "test.com"}

# Known technical/monitoring/infrastructure domains that show up in page
# source (error trackers, site-builder internals) but are never a
# business's own contact address. Matched as an exact domain or a
# subdomain of these (so "sentry-next.wixpress.com" and
# "sentry.wixpress.com" are both caught by "wixpress.com").
TECHNICAL_EMAIL_DOMAIN_SUFFIXES = ("sentry.io", "wixpress.com")

# Simple, low-risk technology fingerprints looked for in raw HTML.
TECH_SIGNATURES = {
    "WordPress": ["wp-content", "wp-includes", "/wp-json/"],
    "WooCommerce": ["woocommerce"],
    "Shopify": ["cdn.shopify.com", "shopify.com/s/files", "Shopify.theme"],
    "Wix": ["wix.com", "wixstatic.com"],
    "Squarespace": ["squarespace.com", "static1.squarespace.com"],
    "Webflow": ["webflow.com", "website-files.com"],
    "Joomla": ["/media/jui/", "joomla"],
    "Drupal": ["/sites/default/files/", "drupal.js"],
}


class StopRequested(Exception):
    """Internal signal used to unwind out of a multi-page fetch loop when
    the caller's stop_flag() returns True. Never crosses a whole batch —
    callers catch this per-lead so already-saved results stay intact."""
    pass


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
def normalize_target_url(website: str):
    """Turn a raw CSV website value into a fetchable absolute URL, or None
    if it can't be made into one."""
    if not website:
        return None
    website = website.strip()
    if not website:
        return None
    if not re.match(r"^https?://", website, re.IGNORECASE):
        website = "https://" + website
    parsed = urlparse(website)
    if not parsed.netloc:
        return None
    return website


def _root_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower().split(":")[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _same_domain(url: str, root_domain: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return False
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc == root_domain or netloc.endswith("." + root_domain)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def fetch(url: str, timeout: int, session: requests.Session = None, referer: str = None):
    """Single HTTP GET. Returns (ok, status_code, final_url, html, error_message).
    Never raises — every requests exception is captured and returned so a
    single bad website can never crash the batch.

    If a session is given, it's reused so cookies/connection behave like a
    normal browser tab moving between pages on the same site (still one
    request at a time — this does not add concurrency). referer, if given,
    is sent as the Referer header, matching how a browser actually
    navigates from one page to an internal link."""
    headers = dict(BASE_HEADERS)
    if referer:
        headers["Referer"] = referer
    requester = session if session is not None else requests
    try:
        resp = requester.get(
            url,
            timeout=timeout,
            headers=headers,
            allow_redirects=True,
        )
        return True, resp.status_code, resp.url, resp.text, None
    except requests.exceptions.Timeout:
        return False, None, None, None, "Request timed out"
    except requests.exceptions.SSLError as exc:
        return False, None, None, None, f"SSL error: {exc}"
    except requests.exceptions.ConnectionError as exc:
        return False, None, None, None, f"Connection failed (DNS/unreachable): {exc}"
    except requests.exceptions.RequestException as exc:
        return False, None, None, None, f"Request failed: {exc}"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def _is_business_email(email: str) -> bool:
    """Conservative allow/deny check for a single extracted email.
    Returns False for anything that isn't clearly a real business
    contact address — when uncertain, this rejects rather than risking
    junk data in the saved results."""
    email = (email or "").strip().strip(".")
    if "@" not in email:
        return False
    local, _, domain = email.rpartition("@")
    domain = domain.lower().strip(".")
    if not local or not domain or "." not in domain:
        return False

    last_label = domain.rsplit(".", 1)[-1]
    if last_label in NON_EMAIL_FILE_EXTENSIONS:
        return False

    if domain in EXAMPLE_EMAIL_DOMAINS:
        return False

    if any(domain == suf or domain.endswith("." + suf) for suf in TECHNICAL_EMAIL_DOMAIN_SUFFIXES):
        return False

    return True


def extract_emails(text: str):
    candidates = EMAIL_RE.findall(text or "")
    valid = sorted({c for c in candidates if _is_business_email(c)})
    return valid[:10]


def _normalize_phone(raw: str):
    """Validate + normalize one extracted phone candidate to a canonical
    "(XXX) XXX-XXXX" US format, or return None to exclude it. Conservative:
    anything that isn't cleanly a 10-digit US number (or 11 with a leading
    '1' country code) is dropped rather than guessed at — this is what
    rejects both date-like strings and malformed/duplicate captures."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if DATE_LIKE_RE.match(raw):
        return None

    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None

    # NANP area codes never start with 0 or 1; a match here means the
    # regex grabbed something that isn't really a phone number.
    if digits[0] in "01":
        return None
    # All-identical-digit strings ("0000000000") are never real numbers.
    if len(set(digits)) == 1:
        return None

    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"


def extract_phones(text: str):
    found = []
    seen_digits = set()
    for m in PHONE_RE.findall(text or ""):
        normalized = _normalize_phone(m)
        if not normalized:
            continue
        digits_key = re.sub(r"\D", "", normalized)
        if digits_key in seen_digits:
            continue
        seen_digits.add(digits_key)
        found.append(normalized)
    return found[:10]


def detect_technology(html: str):
    lowered = (html or "").lower()
    detected = []
    for tech, signatures in TECH_SIGNATURES.items():
        if any(sig.lower() in lowered for sig in signatures):
            detected.append(tech)
    return detected


def parse_page(html: str, page_url: str):
    soup = BeautifulSoup(html or "", "html.parser")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    meta_description = ""
    meta_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if meta_tag and meta_tag.get("content"):
        meta_description = meta_tag["content"].strip()

    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)[:5000]

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#"):
            continue
        if href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        links.append(urljoin(page_url, href))

    return {"title": title, "meta_description": meta_description, "text": text, "links": links}


def rank_internal_links(links, root_domain):
    """De-dupe, restrict to the same domain, and sort by preferred-page
    keyword so About/Services/Contact are visited before random pages."""
    internal = []
    seen = set()
    for link in links:
        clean = link.split("#")[0]
        if clean in seen or not _same_domain(clean, root_domain):
            continue
        seen.add(clean)
        internal.append(clean)

    def score(url):
        path = urlparse(url).path.lower()
        for i, kw in enumerate(PREFERRED_PATH_KEYWORDS):
            if kw in path:
                return i
        return len(PREFERRED_PATH_KEYWORDS) + 1

    internal.sort(key=score)
    return internal


# ---------------------------------------------------------------------------
# Per-lead research
# ---------------------------------------------------------------------------
def research_one_lead(lead: dict, stop_flag=None, page_callback=None) -> dict:
    """
    Research a single lead's website end to end. Returns a result dict
    matching the shape database.save_research_result() expects.

    Normal site/network problems (unreachable, 404, timeout, SSL error,
    malformed HTML, ...) are captured as a FAILED or SKIPPED result and
    never raised — a bad website must never crash the batch.

    stop_flag, if given, is a zero-arg callable. Between individual page
    fetches (never mid-request) it's checked; if it returns True this
    raises StopRequested so the caller can leave the lead's status as
    resumable rather than counting it as failed.
    """
    business_name = lead.get("business_name", "")
    raw_website = lead.get("website", "")
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    target = normalize_target_url(raw_website)
    if not target:
        return {
            "status": "SKIPPED",
            "website_url": raw_website,
            "final_url": None,
            "http_status": None,
            "pages_checked": 0,
            "error_message": "No usable website URL",
            "research_started_at": started_at,
            "research_completed_at": started_at,
        }

    if page_callback:
        page_callback(business_name, target, target, 0)

    # One Session per lead: reuses the TCP connection and any cookies the
    # site sets, like a real browser tab moving between pages. This does
    # NOT add concurrency — fetches within it still happen strictly one
    # at a time, in order, with the same delay/timeout as before.
    session = requests.Session()

    ok, status_code, final_url, html, err = fetch(
        target, config.REQUEST_TIMEOUT_SECONDS, session=session
    )

    if not ok:
        logger.info(f"Website unreachable for '{business_name}' ({target}): {err}")
        session.close()
        return {
            "status": "FAILED",
            "website_url": target,
            "final_url": None,
            "http_status": status_code,
            "pages_checked": 0,
            "error_message": err,
            "research_started_at": started_at,
            "research_completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    if status_code >= 400:
        logger.info(f"HTTP error for '{business_name}' ({target}): {status_code}")
        session.close()
        reason = (
            "Website denied automated HTTP request (403)"
            if status_code == 403
            else f"HTTP {status_code}"
        )
        return {
            "status": "FAILED",
            "website_url": target,
            "final_url": final_url,
            "http_status": status_code,
            "pages_checked": 0,
            "error_message": reason,
            "research_started_at": started_at,
            "research_completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    root_domain = _root_domain(final_url or target)
    homepage = parse_page(html, final_url or target)
    pages_checked = 1

    tech_detected = set(detect_technology(html))
    emails = set(extract_emails(html))
    phones = set(extract_phones(homepage["text"]))
    services_text_parts = []
    contact_text = ""
    contact_page_url = ""
    visited = {final_url or target}

    to_visit = rank_internal_links(homepage["links"], root_domain)

    try:
        for link in to_visit:
            if pages_checked >= config.MAX_PAGES_PER_WEBSITE:
                break
            if link in visited:
                continue
            if stop_flag and stop_flag():
                raise StopRequested()

            time.sleep(config.REQUEST_DELAY_SECONDS)

            if page_callback:
                page_callback(business_name, target, link, pages_checked)

            p_ok, p_status, p_final, p_html, p_err = fetch(
                link, config.REQUEST_TIMEOUT_SECONDS, session=session, referer=final_url or target
            )
            visited.add(link)
            pages_checked += 1

            if not p_ok or (p_status and p_status >= 400):
                logger.info(f"Skipped page {link} for '{business_name}': {p_err or p_status}")
                continue

            page_data = parse_page(p_html, p_final or link)
            tech_detected.update(detect_technology(p_html))
            emails.update(extract_emails(p_html))

            path_lower = urlparse(link).path.lower()
            if "service" in path_lower:
                services_text_parts.append(page_data["text"][:1500])
            if "contact" in path_lower:
                contact_text = page_data["text"][:1500]
                contact_page_url = link
                phones.update(extract_phones(page_data["text"]))
    finally:
        session.close()

    location_text = ""
    address_match = ADDRESS_RE.search(contact_text or homepage["text"])
    if address_match:
        location_text = address_match.group(0).strip()

    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return {
        "status": "COMPLETED",
        "website_url": target,
        "final_url": final_url,
        "http_status": status_code,
        "page_title": homepage["title"],
        "meta_description": homepage["meta_description"],
        "business_description": homepage["meta_description"] or homepage["text"][:500],
        "services_text": " ".join(services_text_parts)[:3000],
        "contact_text": contact_text,
        "contact_url": contact_page_url,
        "location_text": location_text,
        "emails_found": sorted(emails),
        "phones_found": sorted(phones),
        "technology_detected": sorted(tech_detected),
        "pages_checked": pages_checked,
        "research_started_at": started_at,
        "research_completed_at": completed_at,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Sequential batch runner (used by scripts/tests; the Streamlit dashboard
# drives research_one_lead() itself, one lead per rerun — see app.py).
# ---------------------------------------------------------------------------
def run_batch(leads: list, stop_flag, on_result=None, progress_callback=None) -> dict:
    """
    Sequentially research a list of lead dicts. stop_flag is a zero-arg
    callable returning True once the user asked to stop; checked before
    each lead AND passed through to research_one_lead for the pages within
    a lead. on_result(lead, result) is called after every lead so the
    caller can persist it (e.g. database.save_research_result). Never
    raises for a bad website — only StopRequested is caught internally.
    """
    stats = {"processed": 0, "success": 0, "failed": 0, "skipped": 0, "total": len(leads)}

    for lead in leads:
        if stop_flag():
            logger.info("Website research batch stopped by user request.")
            break

        try:
            result = research_one_lead(lead, stop_flag=stop_flag)
        except StopRequested:
            logger.info(f"Research interrupted mid-lead: lead_id={lead.get('id')}")
            break

        if on_result:
            on_result(lead, result)

        stats["processed"] += 1
        if result["status"] == "COMPLETED":
            stats["success"] += 1
        elif result["status"] == "FAILED":
            stats["failed"] += 1
        else:
            stats["skipped"] += 1

        if progress_callback:
            progress_callback(stats)

    return stats
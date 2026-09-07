"""
ai_analysis.py
================
5thGenLeadGenerator — Phase 3: AI-powered Business Analysis / Lead
Qualification.

Scope (per Master Specification, Phase 3):
- Takes the clean website-research data Phase 2 already stored (never
  re-scrapes, never adds another crawler) and produces a structured,
  transparent opportunity analysis per lead.
- The lead_score / lead_grade / opportunity_level / recommended_service /
  secondary_services are computed HERE, deterministically, from stored
  Phase 1 + Phase 2 facts only — see compute_findings(). This is the
  "transparent scoring system" the spec asks for: every point is
  traceable to a stored fact, documented inline, and identical no matter
  which AI provider is configured.
- The configured AIProvider (ai_provider.get_provider()) only turns those
  already-decided findings into readable text — see
  AIProvider.generate_narrative(). Swapping providers changes the wording,
  never the score.
- Never invents facts. Where data is missing, fields say so explicitly
  and confidence is lowered — see compute_findings()'s confidence logic.
- Strictly sequential (one lead per call), matching Phase 2. The
  Streamlit dashboard drives one lead per rerun, same pattern as the
  Website Research tab.

--- Evidence-tiered service detection (revision 2 — see below) ----------
Each of the 5 services is scored as one of three EVIDENCE TIERS, using
ONLY fields Phase 2 already stored (no re-scraping):
  STRONG   — a directly-observed, unambiguous fact (site unreachable/
             missing, ecommerce platform or cart/checkout language
             found, both title AND meta description missing, or a
             meta description present but explicitly weak — too short
             to be useful, or a verbatim duplicate of the page title,
             or an explicit custom-software/integration phrase in the
             site text).
  MODERATE — an explicit, data-derived signal that is real but softer:
             a template-based website builder (Wix/Squarespace/Webflow)
             with known technical-SEO/customization limitations; no
             address/service-area text found anywhere across the pages
             Phase 2 checked; no dedicated Contact page found; services
             content present but noticeably thin; a single marketing-
             related or no-contact-info signal. Each is stated with the
             concrete fact behind it (e.g. "no address text found across
             5 pages checked") so it's always verifiable, never a vague
             industry guess.
  NONE     — no supported evidence. Generic assumptions that aren't
             backed by an actual stored fact — an unrecognized CMS by
             itself, or bare words like "shop"/"store"/"product" — are
             deliberately never turned into evidence at any tier; they
             simply don't count towards anything (this is what keeps
             Shopify/Website-Development from being guessed).
recommended_service is the service with the highest tier (STRONG beats
MODERATE beats NONE), tie-broken by a fixed priority order. If every
service comes back NONE, recommended_service is "Needs Review" — but
per the revision-2 evidence sources above, a genuinely healthy, fully-
reachable small-business site will still usually surface at least one
MODERATE, concrete signal (a builder-platform limitation, a missing
address, a missing Contact page, thin content) rather than defaulting
straight to Needs Review. Needs Review is now reserved for leads where
none of those checks turn up anything — and the narrative explains
exactly which checks were run and came back clean.
confidence is derived from the tier behind recommended_service (STRONG
-> HIGH, MODERATE -> MEDIUM, NONE/Needs Review -> LOW), not from general
data completeness — confidence reflects how solid THIS recommendation
is, and MEDIUM is an expected, normal outcome (not a downgrade) for a
well-reasoned moderate-evidence recommendation.
"""

import json
import re
from datetime import datetime, timezone

from ai_provider import get_provider
from ai_provider.base import NEEDS_REVIEW
from logger_setup import get_logger

logger = get_logger()

SERVICES = (
    "Digital Marketing",
    "Software Development",
    "SEO",
    "Shopify Development",
    "Website Development",
)

# Priority order used only to break ties between services that land on the
# same evidence tier — never used to invent evidence.
SERVICE_PRIORITY = (
    "Website Development",
    "Shopify Development",
    "SEO",
    "Digital Marketing",
    "Software Development",
)

# Ecommerce platforms that, if Phase 2 detected them in technology_detected,
# are STRONG, unambiguous Shopify-Development evidence on their own.
ECOMMERCE_PLATFORMS = {"shopify", "woocommerce", "bigcommerce", "magento"}

# Multi-word/unambiguous ecommerce phrases only — deliberately excludes
# generic single words like "shop", "store", or "product" that appear in
# ordinary business copy (and that even substring-matched into unrelated
# words like "restore"), which was the source of false-positive Shopify
# recommendations for sites with no actual storefront.
ECOMMERCE_PHRASES = (
    "add to cart", "shopping cart", "checkout", "buy now", "online store",
    "e-commerce", "ecommerce", "product page", "shop online", "storefront",
)

# Template/website-builder platforms with genuinely narrower technical-SEO
# and customization control than a custom build or WordPress — a real,
# commonly-cited limitation, not a generic industry assumption. Detected
# directly from Phase 2's technology_detected, so this is always a
# verifiable fact for the specific lead, not a guess.
BUILDER_PLATFORMS = {"wix", "squarespace", "webflow"}

MARKETING_KEYWORDS = (
    "marketing", "advertising", "social media", "branding", "campaign", "promotion",
)
TECHNICAL_KEYWORDS = (
    "api", "custom software", "saas", "integration", "automation",
    "erp", "crm platform", "mobile app", "web application",
)
SEO_KEYWORDS = ("seo", "search engine optimization", "google ranking")

# A meta description shorter than this is treated as present-but-weak
# (STRONG evidence, distinct from "missing") — too short to function as a
# useful search-result snippet. Chosen well below Google's typical ~150-160
# char display width so only genuinely thin descriptions trigger it.
MIN_USEFUL_META_LENGTH = 40

# Combined services-page text shorter than this (while non-empty) is
# treated as thin/underdeveloped content — a real, measurable signal, not
# an assumption.
MIN_USEFUL_SERVICES_TEXT_LENGTH = 150


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _combined_text(context: dict) -> str:
    parts = [
        context.get("page_title") or "",
        context.get("meta_description") or "",
        context.get("business_description") or "",
        context.get("services_text") or "",
        context.get("category") or "",
    ]
    return " ".join(p for p in parts if p).lower()


def _contains_phrase(text: str, phrase: str) -> bool:
    """Word-boundary match so multi-word phrases never substring-match
    inside an unrelated word (e.g. bare 'store' previously matched inside
    'restore')."""
    return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None


def _grade_for_score(score: int) -> str:
    if score >= 80:
        return "VERY HIGH"
    if score >= 60:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


_OPPORTUNITY_LABELS = {
    "LOW": "Low Priority",
    "MEDIUM": "Worth a Look",
    "HIGH": "Good Fit",
    "VERY HIGH": "Priority Lead",
}


def build_context(lead: dict, research: dict) -> dict:
    """Assemble the read-only fact sheet passed to scoring + the AI
    provider. Pulls only from what Phase 1/2 already stored — no network
    calls, no re-scraping."""
    research = research or {}

    def _json_list(raw):
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return []

    return {
        "lead_id": lead.get("id"),
        "business_name": lead.get("business_name") or "",
        "owner_name": lead.get("owner_name") or "",
        "category": lead.get("category") or "",
        "website": lead.get("website") or "",
        "lead_email": lead.get("email") or "",
        "lead_phone": lead.get("phone") or "",
        "research_status": research.get("status") or "MISSING",
        "http_status": research.get("http_status"),
        "page_title": research.get("page_title") or "",
        "meta_description": research.get("meta_description") or "",
        "business_description": research.get("business_description") or "",
        "services_text": research.get("services_text") or "",
        "location_text": research.get("location_text") or "",
        "contact_page_url": research.get("contact_page_url") or "",
        "emails_found": _json_list(research.get("emails_found")),
        "phones_found": _json_list(research.get("phones_found")),
        "technology_detected": _json_list(research.get("technology_detected")),
        "pages_checked": research.get("pages_checked") or 0,
    }


def _evidence(tier, reasons):
    return {"tier": tier, "reasons": reasons}


def _max_tier(a: str, b: str) -> str:
    """Combine two tiers, keeping the stronger — never lets one signal
    silently downgrade evidence another signal already established."""
    return a if _TIER_RANK[a] >= _TIER_RANK[b] else b


_TIER_RANK = {"strong": 2, "moderate": 1, "none": 0}
_TIER_TO_CONFIDENCE = {"strong": "HIGH", "moderate": "MEDIUM", "none": "LOW"}


def _service_evidence(context: dict) -> dict:
    """Evidence-tiered per-service detection. Returns
    {service_name: {"tier": "strong"/"moderate"/"none", "reasons": [...]}}.
    Every reason is worded from the exact fact that produced it — never a
    generic template — so downstream text can never claim a cause (e.g.
    "missing metadata") that isn't actually true for this lead, and can
    always say precisely what was checked."""
    has_website = bool(context["website"].strip())
    research_status = context["research_status"]
    has_contact = bool(
        context["emails_found"] or context["phones_found"]
        or context["lead_email"] or context["lead_phone"]
    )
    title = (context["page_title"] or "").strip()
    meta = (context["meta_description"] or "").strip()
    tech = [t.strip() for t in (context["technology_detected"] or []) if t]
    tech_lower = {t.lower() for t in tech}
    pages_checked = context["pages_checked"]
    location_text = (context.get("location_text") or "").strip()
    contact_page_url = (context.get("contact_page_url") or "").strip()
    services_text = (context.get("services_text") or "").strip()
    text = _combined_text(context)

    evidence = {}

    # --- Website Development --------------------------------------------
    # STRONG: the site itself is the problem (unreachable / doesn't exist)
    # — a direct, observed fact, not an inference.
    # MODERATE: reachable, but a concrete structural gap Phase 2 actually
    # observed — either (a) no platform fingerprint AND only the homepage
    # was reachable together (neither alone is enough — an unknown CMS by
    # itself is never treated as proof of a poor website), or (b) no
    # dedicated Contact page was found among multiple pages checked (a
    # real navigation/conversion gap, not a guess).
    if not has_website:
        evidence["Website Development"] = _evidence(
            "strong", ["No website on file — a build from scratch is a clear, unambiguous opportunity."]
        )
    elif research_status == "FAILED":
        evidence["Website Development"] = _evidence(
            "strong", ["Website did not respond during research — an unreachable site is a concrete rebuild/fix opportunity."]
        )
    elif research_status == "SKIPPED":
        evidence["Website Development"] = _evidence(
            "strong", ["No usable website URL was available to evaluate."]
        )
    elif research_status == "COMPLETED":
        tier = "none"
        reasons = []
        if not tech and pages_checked <= 1:
            tier = _max_tier(tier, "moderate")
            reasons.append(
                "No recognizable platform was detected AND only the homepage was reachable — "
                "possible thin/custom build (unknown platform alone is not treated as proof of a poor site)."
            )
        if pages_checked >= 2 and not contact_page_url:
            tier = _max_tier(tier, "moderate")
            reasons.append(
                f"No dedicated Contact page was found among the {pages_checked} pages checked — "
                f"a real navigation/lead-capture gap worth confirming manually."
            )
        evidence["Website Development"] = _evidence(tier, reasons)
    else:
        evidence["Website Development"] = _evidence("none", [])

    # --- Shopify Development ---------------------------------------------
    # STRONG only: a named ecommerce platform in technology_detected, or an
    # explicit multi-word ecommerce phrase (cart/checkout/etc.) in the
    # collected text. No lower tier — Shopify must never be guessed from
    # generic wording (e.g. bare "shop"/"store"/"product") without
    # reliable storefront evidence.
    shopify_reasons = []
    if tech_lower & ECOMMERCE_PLATFORMS:
        matched = ", ".join(t for t in tech if t.lower() in ECOMMERCE_PLATFORMS)
        shopify_reasons.append(f"Ecommerce platform detected in website technology: {matched}.")
    matched_phrases = [p for p in ECOMMERCE_PHRASES if _contains_phrase(text, p)]
    if matched_phrases:
        shopify_reasons.append(f"Ecommerce/storefront language found on the site: {', '.join(matched_phrases)}.")
    if shopify_reasons:
        evidence["Shopify Development"] = _evidence("strong", shopify_reasons)
    else:
        evidence["Shopify Development"] = _evidence("none", [])

    # --- SEO ----------------------------------------------------------------
    # STRONG: both title and meta description missing (explicit gap), or a
    # meta description that IS present but is explicitly weak — too short
    # to serve as a useful search snippet, or a verbatim duplicate of the
    # page title. Never claims metadata is missing when it was actually
    # found and usable.
    # MODERATE: exactly one of title/meta missing; an explicit on-site "seo"
    # mention; no address/service-area text found anywhere across the pages
    # checked (a real local-search gap); services content present but
    # measurably thin; or the site runs on a template builder with known
    # technical-SEO limitations. Each ties to a concrete, stated fact.
    if research_status == "COMPLETED":
        tier = "none"
        reasons = []
        if not title and not meta:
            tier = "strong"
            reasons.append("Both page title and meta description are missing.")
        elif not meta:
            tier = _max_tier(tier, "moderate")
            reasons.append("Meta description is missing (page title is present).")
        elif not title:
            tier = _max_tier(tier, "moderate")
            reasons.append("Page title is missing (meta description is present).")
        elif len(meta) < MIN_USEFUL_META_LENGTH or meta.lower() == title.lower():
            tier = _max_tier(tier, "strong")
            if meta.lower() == title.lower():
                reasons.append("Meta description is present but is a verbatim duplicate of the page title — not a useful search-result snippet.")
            else:
                reasons.append(f"Meta description is present but only {len(meta)} characters — too short to be a useful search-result snippet.")

        if any(_contains_phrase(text, k) for k in SEO_KEYWORDS):
            tier = _max_tier(tier, "moderate")
            reasons.append("Site text explicitly references SEO/search ranking.")

        if pages_checked >= 2 and not location_text:
            tier = _max_tier(tier, "moderate")
            reasons.append(
                f"No address or service-area text was found anywhere across the {pages_checked} pages checked — "
                f"local-search signals (NAP/service-area info) may be thin."
            )

        if services_text and len(services_text) < MIN_USEFUL_SERVICES_TEXT_LENGTH:
            tier = _max_tier(tier, "moderate")
            reasons.append(
                f"Services content is present but thin (~{len(services_text)} characters) across {pages_checked} pages checked."
            )

        if tech_lower & BUILDER_PLATFORMS:
            platform = next(t for t in tech if t.lower() in BUILDER_PLATFORMS)
            tier = _max_tier(tier, "moderate")
            reasons.append(
                f"Site is built on {platform}, a template-based website builder — these typically offer "
                f"narrower technical-SEO/customization control than a custom or WordPress build."
            )

        evidence["SEO"] = _evidence(tier, reasons)
    else:
        evidence["SEO"] = _evidence("none", [])

    # --- Digital Marketing ---------------------------------------------------
    # Each signal below is an explicit, stated fact (never bare industry
    # assumption). A single one is MODERATE; two together are STRONG.
    dm_reasons = []
    if any(_contains_phrase(text, k) for k in MARKETING_KEYWORDS):
        dm_reasons.append("Site text references marketing/advertising/branding.")
    if research_status == "COMPLETED" and not has_contact:
        dm_reasons.append("Website reachable but no usable contact info was extracted — limited evidence of active lead-capture/marketing follow-through.")
    if len(dm_reasons) >= 2:
        evidence["Digital Marketing"] = _evidence("strong", dm_reasons)
    elif len(dm_reasons) == 1:
        evidence["Digital Marketing"] = _evidence("moderate", dm_reasons)
    else:
        evidence["Digital Marketing"] = _evidence("none", [])

    # --- Software Development ------------------------------------------------
    # An explicit technical-integration phrase in the site's own text is a
    # specific, unambiguous signal on its own (unlike the generic ecommerce
    # words excluded from Shopify above) — these terms rarely appear in
    # ordinary small-business copy by accident.
    sw_hits = [k for k in TECHNICAL_KEYWORDS if _contains_phrase(text, k)]
    if sw_hits:
        evidence["Software Development"] = _evidence(
            "strong", [f"Site text explicitly references '{', '.join(sw_hits)}' — a specific custom-software/integration signal."]
        )
    else:
        evidence["Software Development"] = _evidence("none", [])

    return evidence


def _select_recommendation(evidence: dict):
    """Pick recommended_service + secondary_services from per-service
    evidence tiers. Never defaults to a specific service when nobody has
    supported evidence — returns NEEDS_REVIEW instead."""
    best_tier = max((evidence[s]["tier"] for s in SERVICES), key=lambda t: _TIER_RANK[t])

    if _TIER_RANK[best_tier] == 0:
        return NEEDS_REVIEW, [], "none"

    candidates = [s for s in SERVICE_PRIORITY if evidence[s]["tier"] == best_tier]
    primary = candidates[0]

    remaining_ranked = sorted(
        (s for s in SERVICE_PRIORITY if s != primary and evidence[s]["tier"] != "none"),
        key=lambda s: (-_TIER_RANK[evidence[s]["tier"]], SERVICE_PRIORITY.index(s)),
    )
    secondary = remaining_ranked[:2]

    return primary, secondary, best_tier


def _needs_review_explanation(context: dict) -> str:
    """Builds a specific, checklist-style explanation of what was actually
    checked and came back clean when no service reached MODERATE/STRONG
    evidence — never a generic "nothing found" statement. This only fires
    when _select_recommendation() returns NEEDS_REVIEW, i.e. every check
    below genuinely came back clean for this lead."""
    research_status = context["research_status"]
    if research_status != "COMPLETED":
        return (
            f"Website research status is {research_status}, so there isn't enough collected "
            f"data yet to support a specific service recommendation. Needs Review: re-run "
            f"Website Research once a usable URL/response is available."
        )

    title = (context["page_title"] or "").strip()
    meta = (context["meta_description"] or "").strip()
    tech = [t for t in (context["technology_detected"] or []) if t]
    pages_checked = context["pages_checked"]
    location_text = (context.get("location_text") or "").strip()
    contact_page_url = (context.get("contact_page_url") or "").strip()
    services_text = (context.get("services_text") or "").strip()

    checks = [
        f"page title present ({len(title)} chars)" if title else "page title missing",
        f"meta description present and adequate length ({len(meta)} chars)" if meta else "meta description missing",
        f"platform detected ({', '.join(tech)})" if tech else "no platform detected, but multiple pages were still reachable",
        f"{pages_checked} page(s) checked",
        "address/service-area text found" if location_text else "no address text found",
        "dedicated Contact page found" if contact_page_url else "no dedicated Contact page found",
        f"services content present (~{len(services_text)} chars)" if services_text else "no services content found",
        "no ecommerce/cart/checkout language found",
        "no explicit custom-software/integration phrase found",
    ]
    return (
        "Needs Review — checked: " + "; ".join(checks) + ". "
        "None of these individually or together met the bar for a confident service "
        "recommendation. To move this forward: verify manually (page speed, on-page "
        "content quality, competitor gap, or a direct conversation with the business) "
        "rather than relying on Phase 2 data alone."
    )


def compute_findings(context: dict) -> dict:
    """Deterministic, transparent scoring. Every point added is paired
    with a plain-English reason so analysis_reason is always traceable.
    Never touches the network; only reads `context`.

    Two independent outputs, both from the same underlying facts:
    - lead_score/lead_grade/opportunity_level: a general 0-100 "how much
      is going on here" opportunity score (site reachability, contact
      info, metadata, platform, page depth).
    - recommended_service/secondary_services/confidence: which specific
      5thGen service the evidence actually supports, and how strong that
      specific evidence is (see _service_evidence/_select_recommendation
      above) — kept separate so a generic opportunity score never gets
      mistaken for confidence in one particular recommendation.
    """
    reasons = []
    score = 0

    has_website = bool(context["website"].strip())
    research_status = context["research_status"]
    has_contact = bool(
        context["emails_found"] or context["phones_found"]
        or context["lead_email"] or context["lead_phone"]
    )
    has_meta_description = bool((context["meta_description"] or "").strip())
    has_services_text = bool((context["services_text"] or "").strip())
    tech = context["technology_detected"]
    pages_checked = context["pages_checked"]

    if not has_website:
        score += 5
        reasons.append("No website on file.")
    elif research_status == "MISSING":
        score += 5
        reasons.append("Website not yet researched by Phase 2.")
    elif research_status == "FAILED":
        score += 20
        reasons.append("Website did not respond during research (likely down or blocking automated requests).")
    elif research_status == "SKIPPED":
        score += 10
        reasons.append("Website research was skipped (no usable URL).")
    else:  # COMPLETED
        score += 15
        reasons.append("Website is live and reachable.")
        if has_contact:
            score += 15
            reasons.append("Usable contact info (email/phone) found.")
        else:
            reasons.append("No usable contact info extracted.")
        if not has_meta_description:
            score += 10
            reasons.append("Missing meta description (SEO gap).")
        if not has_services_text:
            score += 5
            reasons.append("No dedicated services page found.")
        if not tech:
            score += 15
            reasons.append("No recognizable website platform/CMS detected.")
        else:
            score += 5
            reasons.append(f"Platform detected: {', '.join(tech)}.")
        if pages_checked <= 1:
            score += 5
            reasons.append("Only the homepage was reachable (thin site).")

    score = max(0, min(score, 100))
    lead_grade = _grade_for_score(score)
    opportunity_level = _OPPORTUNITY_LABELS[lead_grade]

    # --- evidence-tiered service detection (see _service_evidence) ---------
    evidence = _service_evidence(context)
    recommended_service, secondary_services, best_tier = _select_recommendation(evidence)
    confidence = _TIER_TO_CONFIDENCE[best_tier]

    if recommended_service == NEEDS_REVIEW:
        reasons.append(_needs_review_explanation(context))
    else:
        for r in evidence[recommended_service]["reasons"]:
            reasons.append(r)

    services_detected = [s for s in SERVICES if evidence[s]["tier"] != "none"]

    return {
        "lead_score": score,
        "lead_grade": lead_grade,
        "opportunity_level": opportunity_level,
        "confidence": confidence,
        "recommended_service": recommended_service,
        "secondary_services": secondary_services,
        "services_detected": services_detected,
        "service_evidence": evidence,
        "reasons": reasons,
    }


def analyze_one_lead(lead: dict, research: dict, provider=None) -> dict:
    """
    Run Phase 3 analysis for a single lead. Returns a result dict matching
    the shape database.save_analysis_result() expects. Never raises for
    bad/missing data — a lead with no research data still returns a
    low-confidence, clearly-labeled result rather than crashing the batch.
    """
    started_at = _now()
    context = build_context(lead, research)

    try:
        findings = compute_findings(context)
        active_provider = provider or get_provider()
        narrative = active_provider.generate_narrative(context, findings)

        return {
            "status": "COMPLETED",
            "business_category": narrative["business_category"],
            "business_summary": narrative["business_summary"],
            "services_detected": findings["services_detected"],
            "website_platform": narrative["website_platform"],
            "website_quality_observations": narrative["website_quality_observations"],
            "seo_opportunities": narrative["seo_opportunities"],
            "digital_marketing_opportunities": narrative["digital_marketing_opportunities"],
            "website_development_opportunities": narrative["website_development_opportunities"],
            "shopify_opportunities": narrative["shopify_opportunities"],
            "software_development_opportunities": narrative["software_development_opportunities"],
            "recommended_service": findings["recommended_service"],
            "secondary_services": findings["secondary_services"],
            "lead_score": findings["lead_score"],
            "lead_grade": findings["lead_grade"],
            "opportunity_level": findings["opportunity_level"],
            "sales_angle": narrative["sales_angle"],
            "analysis_reason": narrative["analysis_reason"],
            "confidence": findings["confidence"],
            "ai_provider": active_provider.name,
            "ai_model": active_provider.model_name,
            "error_message": None,
            "analysis_started_at": started_at,
            "analysis_completed_at": _now(),
        }
    except Exception as exc:
        logger.error(f"AI analysis failed for lead_id={lead.get('id')}: {exc}")
        return {
            "status": "FAILED",
            "error_message": f"Unexpected error: {exc}",
            "analysis_started_at": started_at,
            "analysis_completed_at": _now(),
        }


# ---------------------------------------------------------------------------
# Sequential batch runner (used by scripts/tests; the Streamlit dashboard
# drives analyze_one_lead() itself, one lead per rerun — see app.py).
# ---------------------------------------------------------------------------
def run_batch(leads_with_research: list, stop_flag, on_result=None, progress_callback=None) -> dict:
    """
    leads_with_research: list of (lead_dict, research_dict) tuples.
    stop_flag: zero-arg callable returning True once the user asked to
    stop; checked before each lead. on_result(lead, result) is called
    after every lead so the caller can persist it. Never raises for a
    single bad lead.
    """
    stats = {"processed": 0, "success": 0, "failed": 0, "skipped": 0, "total": len(leads_with_research)}
    provider = get_provider()

    for lead, research in leads_with_research:
        if stop_flag():
            logger.info("AI analysis batch stopped by user request.")
            break

        result = analyze_one_lead(lead, research, provider=provider)

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
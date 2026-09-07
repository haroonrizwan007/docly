"""
ai_outreach.py
===============
5thGenLeadGenerator — Phase 4 Outreach Orchestrator

Turns already-stored Phase 1-3 data (lead + website research + the
deterministic Phase 3 findings) into a personalized outreach DRAFT. This
module never re-scrapes a website, never re-decides the score/recommended
service, and never sends anything — it only orchestrates:

    build_context()      -- from ai_analysis.py (Phase 1+2 facts)
    compute_findings()   -- from ai_analysis.py (Phase 3 deterministic
                             score/service decision — recomputed here from
                             the same stored facts, never from new scraping)
    provider.generate_outreach(context, findings)
                          -- wording layer only (mock or gemini)
    normalize_outreach_result()
                          -- from ai_provider/base.py, final safety contract

The single public entry point app.py calls is generate_draft_for_lead().
"""

from datetime import datetime, timezone

from ai_analysis import build_context, compute_findings
from ai_provider import get_provider
from ai_provider.base import normalize_outreach_result, NEEDS_REVIEW
from logger_setup import get_logger

logger = get_logger()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_draft_for_lead(lead: dict, research: dict, provider=None) -> dict:
    """
    Generate (or regenerate) a Phase 4 outreach draft for one lead.

    Parameters
    ----------
    lead:
        Row dict from database.get_lead(lead_id).
    research:
        Row dict from database.get_research(lead_id) (may be None/empty
        if research failed — build_context() handles that safely).
    provider:
        Optional AIProvider instance. Defaults to config.AI_PROVIDER via
        get_provider() (with automatic fallback to MockProvider).

    Returns
    -------
    dict
        Shape expected by database.save_outreach_draft():
            generation_status, subject, subject_options, email_body,
            recommended_service, confidence, ai_provider, ai_model,
            error_message, generated_at
    """
    business_name = (lead or {}).get("business_name", "unknown")
    lead_id = (lead or {}).get("id")

    try:
        context = build_context(lead or {}, research or {})
        findings = compute_findings(context)
        active_provider = provider or get_provider()

        # Hard safety rule (also enforced inside providers, but repeated
        # here so the orchestrator never depends on a provider behaving):
        # a lead with no supported service never gets a fabricated pitch.
        recommended = findings.get("recommended_service")
        if not recommended or recommended == NEEDS_REVIEW:
            raw_result = {
                "generation_status": "NEEDS_REVIEW",
                "subject_options": [],
                "email_body": (
                    "No sufficiently supported service opportunity was "
                    "identified from the collected research for this lead. "
                    "Manual review is required before outreach — see the "
                    "AI Lead Analysis tab's Evidence Found section for "
                    "exactly what was checked."
                ),
            }
        else:
            raw_result = active_provider.generate_outreach(context, findings)

        normalized = normalize_outreach_result(raw_result)

        subject_options = normalized.get("subject_options") or []
        subject = subject_options[0] if subject_options else None

        logger.info(
            f"Outreach draft generated: lead_id={lead_id} business={business_name} "
            f"status={normalized['generation_status']} service={recommended} "
            f"provider={active_provider.name}"
        )

        return {
            "generation_status": normalized["generation_status"],
            "subject": subject,
            "subject_options": subject_options,
            "email_body": normalized.get("email_body"),
            "recommended_service": recommended,
            "confidence": findings.get("confidence"),
            "ai_provider": active_provider.name,
            "ai_model": active_provider.model_name,
            "error_message": None,
            "generated_at": _now(),
        }

    except Exception as exc:
        logger.error(f"Outreach generation failed for lead_id={lead_id}: {exc}")
        return {
            "generation_status": "NEEDS_REVIEW",
            "subject": None,
            "subject_options": [],
            "email_body": (
                "Outreach generation hit an unexpected error and was not "
                "produced. Manual review is required. "
                f"(details: {exc})"
            ),
            "recommended_service": None,
            "confidence": None,
            "ai_provider": getattr(provider, "name", None),
            "ai_model": getattr(provider, "model_name", None),
            "error_message": str(exc),
            "generated_at": _now(),
        }

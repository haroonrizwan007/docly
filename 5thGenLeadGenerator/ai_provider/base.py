"""
ai_provider/base.py
===================

5thGenLeadGenerator — AI Provider Contract

Purpose
-------
Defines the common interface used by every AI provider:

    - MockProvider
    - GeminiProvider
    - future providers

The rest of the application must NOT depend on a specific provider.

Architecture
------------
Phase 1
    Lead import + database

Phase 2
    Website research

Phase 3
    Deterministic lead analysis
        - lead_score
        - lead_grade
        - opportunity_level
        - confidence
        - recommended_service
        - secondary_services
        - service_evidence

Phase 4
    Evidence-based outreach drafting

AI providers
    Only convert already-known facts/findings into natural language.

IMPORTANT
---------
Providers must NOT become the source of truth for:

    - lead_score
    - lead_grade
    - opportunity_level
    - confidence
    - recommended_service
    - secondary_services
    - service_evidence

Those decisions belong to ai_analysis.py.

The provider is a wording/generation layer only.
"""


from abc import ABC, abstractmethod
from typing import Any, Dict, List


# ============================================================================
# CONSTANTS
# ============================================================================

#: Sentinel value used when Phase 3 cannot identify a sufficiently supported
#: service opportunity.
NEEDS_REVIEW = "Needs Review"


#: Allowed outreach workflow states.
OUTREACH_DRAFT = "DRAFT"
OUTREACH_APPROVED = "APPROVED"
OUTREACH_REJECTED = "REJECTED"
OUTREACH_NEEDS_REVIEW = "NEEDS_REVIEW"


#: Provider-generated narrative fields.
#
# Every provider should return these fields from generate_narrative().
NARRATIVE_FIELDS = (
    "business_category",
    "business_summary",
    "website_platform",
    "website_quality_observations",
    "seo_opportunities",
    "digital_marketing_opportunities",
    "website_development_opportunities",
    "shopify_opportunities",
    "software_development_opportunities",
    "sales_angle",
    "analysis_reason",
)


#: Provider-generated outreach fields.
OUTREACH_FIELDS = (
    "generation_status",
    "subject_options",
    "email_body",
)


#: Valid statuses that an AI provider may return for outreach generation.
VALID_OUTREACH_STATUSES = {
    OUTREACH_DRAFT,
    OUTREACH_NEEDS_REVIEW,
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def safe_text(value: Any, default: str = "Unknown — not enough data") -> str:
    """
    Convert an arbitrary value into safe display text.

    Provider output must never contain None for narrative fields because
    downstream UI/database code should not have to guess what missing data
    means.

    Parameters
    ----------
    value:
        Any provider-returned value.

    default:
        Text returned when value is None or empty.

    Returns
    -------
    str
    """

    if value is None:
        return default

    text = str(value).strip()

    return text if text else default


def normalize_subject_options(
    value: Any,
    limit: int = 3,
) -> List[str]:
    """
    Normalize provider-generated subject lines.

    Rules
    -----
    - Must be a list/tuple-like collection.
    - Empty values are removed.
    - Duplicate subjects are removed.
    - Maximum number of subjects is `limit`.
    - Returned values are always strings.

    This function does NOT guarantee exactly three subjects.

    Why?
    ----
    NEEDS_REVIEW is allowed to have zero subjects.
    The concrete provider can enforce its own stricter requirements
    for DRAFT responses.
    """

    if not isinstance(value, (list, tuple)):
        return []

    normalized = []
    seen = set()

    for item in value:
        text = safe_text(item, default="").strip()

        if not text:
            continue

        # Case-insensitive duplicate protection.
        dedupe_key = text.casefold()

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        normalized.append(text)

        if len(normalized) >= limit:
            break

    return normalized


def normalize_outreach_result(result: Any) -> Dict[str, Any]:
    """
    Normalize and safety-check a provider outreach response.

    This function provides a final contract boundary between an AI provider
    and the rest of the application.

    Important:
        It does NOT approve or send emails.

    It only guarantees that the returned object follows the application's
    basic outreach contract.

    Rules
    -----
    1. Invalid provider output becomes NEEDS_REVIEW.
    2. Unknown workflow statuses become NEEDS_REVIEW.
    3. NEEDS_REVIEW may have zero subjects.
    4. DRAFT requires:
           - exactly 3 subject options
           - non-empty email body
           - reasonable minimum body length
    5. The provider cannot create APPROVED or REJECTED status.
    """

    # ------------------------------------------------------------------
    # Invalid top-level response
    # ------------------------------------------------------------------

    if not isinstance(result, dict):
        return needs_review_outreach(
            "The AI provider returned an invalid outreach response. "
            "Manual review is required."
        )

    # ------------------------------------------------------------------
    # Generation status
    # ------------------------------------------------------------------

    raw_status = result.get("generation_status")

    if raw_status is None:
        status = OUTREACH_NEEDS_REVIEW
    else:
        status = str(raw_status).strip().upper()

    if status not in VALID_OUTREACH_STATUSES:
        status = OUTREACH_NEEDS_REVIEW

    # ------------------------------------------------------------------
    # Subject lines
    # ------------------------------------------------------------------

    subjects = normalize_subject_options(
        result.get("subject_options")
    )

    # ------------------------------------------------------------------
    # Email body
    # ------------------------------------------------------------------

    email_body = safe_text(
        result.get("email_body"),
        default="",
    ).strip()

    # ------------------------------------------------------------------
    # NEEDS_REVIEW
    # ------------------------------------------------------------------

    if status == OUTREACH_NEEDS_REVIEW:
        return {
            "generation_status": OUTREACH_NEEDS_REVIEW,
            "subject_options": [],
            "email_body": (
                email_body
                or
                "No sufficiently supported service opportunity was "
                "identified from the collected research. Manual review "
                "is required before outreach."
            ),
        }

    # ------------------------------------------------------------------
    # DRAFT validation
    # ------------------------------------------------------------------

    # A valid DRAFT must contain exactly three subject options.
    if len(subjects) != 3:
        return needs_review_outreach(
            "The AI provider did not return three valid subject options. "
            "Manual review is required."
        )

    # Empty body is never allowed.
    if not email_body:
        return needs_review_outreach(
            "The AI provider did not return a valid email body. "
            "Manual review is required."
        )

    # Very short output is unlikely to be a meaningful B2B email.
    if len(email_body.split()) < 40:
        return needs_review_outreach(
            "The generated email was too short to safely use. "
            "Manual review is required."
        )

    return {
        "generation_status": OUTREACH_DRAFT,
        "subject_options": subjects,
        "email_body": email_body,
    }


def needs_review_outreach(reason: str) -> Dict[str, Any]:
    """
    Create a safe NEEDS_REVIEW outreach result.

    This helper is intentionally simple and deterministic.

    It prevents providers from accidentally returning malformed drafts
    that the UI might treat as usable outreach.
    """

    return {
        "generation_status": OUTREACH_NEEDS_REVIEW,
        "subject_options": [],
        "email_body": safe_text(
            reason,
            default=(
                "Manual review is required before outreach."
            ),
        ),
    }


def normalize_narrative_result(result: Any) -> Dict[str, str]:
    """
    Normalize a provider-generated Phase 3 narrative response.

    Every required narrative field is guaranteed to exist and contain
    non-empty text.

    Missing fields are explicitly marked as unknown instead of silently
    becoming None.

    This protects the Streamlit UI and database layer from malformed
    provider responses.
    """

    if not isinstance(result, dict):
        result = {}

    normalized = {}

    for field in NARRATIVE_FIELDS:
        normalized[field] = safe_text(
            result.get(field),
            default="Unknown — not enough data",
        )

    return normalized


# ============================================================================
# ABSTRACT AI PROVIDER
# ============================================================================

class AIProvider(ABC):
    """
    Common interface for all AI providers.

    Every provider must implement:

        is_available()
        generate_narrative()
        generate_outreach()

    The application should interact with providers only through this
    interface.
    """

    # ------------------------------------------------------------------
    # Provider metadata
    # ------------------------------------------------------------------

    #: Short machine-readable provider identifier.
    #
    # Examples:
    #     "mock"
    #     "free"
    #     "real"
    name = "base"

    #: Human-readable model identifier.
    #
    # Stored with generated analysis/outreach records.
    model_name = "unknown"

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """
        Return True when this provider is configured and usable.

        Examples
        --------
        Mock:
            Always True.

        Gemini:
            True only when GEMINI_API_KEY is configured.

        IMPORTANT:
            This method must never raise an exception.

        Returns
        -------
        bool
        """

        raise NotImplementedError

    # ------------------------------------------------------------------
    # Phase 3 — Narrative
    # ------------------------------------------------------------------

    @abstractmethod
    def generate_narrative(
        self,
        context: dict,
        findings: dict,
    ) -> dict:
        """
        Generate human-readable narrative from existing research/findings.

        Parameters
        ----------
        context:
            Stored Phase 1 + Phase 2 information for one lead.

            Examples include:

                business_name
                category
                website
                email
                phone
                city
                country
                page_title
                meta_description
                business_description
                services_text
                technology_detected
                emails_found
                phones_found
                research_status
                etc.

        findings:
            Deterministic Phase 3 findings already calculated by
            ai_analysis.py.

            Examples include:

                lead_score
                lead_grade
                opportunity_level
                confidence
                recommended_service
                secondary_services
                service_evidence

        Returns
        -------
        dict

        Required fields:

            business_category
            business_summary
            website_platform
            website_quality_observations
            seo_opportunities
            digital_marketing_opportunities
            website_development_opportunities
            shopify_opportunities
            software_development_opportunities
            sales_angle
            analysis_reason

        Architectural rule
        ------------------
        This method is a WRITING layer.

        It must NOT independently decide:

            lead_score
            lead_grade
            opportunity_level
            confidence
            recommended_service
            secondary_services

        Those values are authoritative from Phase 3.

        The provider may explain those findings, but must not replace them.

        Evidence rule
        -------------
        The provider must never invent facts.

        If the supplied data does not support a conclusion, the provider
        should explicitly say that there is not enough information.
        """

        raise NotImplementedError

    # ------------------------------------------------------------------
    # Phase 4 — Outreach
    # ------------------------------------------------------------------

    @abstractmethod
    def generate_outreach(
        self,
        context: dict,
        findings: dict,
    ) -> dict:
        """
        Generate a personalized outreach draft from stored data only.

        Parameters
        ----------
        context:
            Existing Phase 1 + Phase 2 research.

        findings:
            Existing Phase 3 deterministic findings.

        Returns
        -------
        dict

        Required structure:

            {
                "generation_status": "DRAFT",
                "subject_options": [
                    "subject 1",
                    "subject 2",
                    "subject 3"
                ],
                "email_body": "..."
            }

        OR:

            {
                "generation_status": "NEEDS_REVIEW",
                "subject_options": [],
                "email_body": "Manual review explanation"
            }

        HARD SAFETY RULE
        ----------------
        If:

            findings["recommended_service"] == NEEDS_REVIEW

        then the provider MUST NOT create a sales email.

        It must return:

            generation_status = "NEEDS_REVIEW"

        with no fabricated:

            - service
            - pain point
            - opportunity
            - sales angle
            - business problem

        Evidence rule
        -------------
        Every opportunity mentioned in the generated email must be
        traceable to supplied Phase 3 findings/service evidence.

        No new website research is permitted.

        No re-scraping is permitted.

        No browsing is permitted.

        No email sending is permitted.

        No scheduling is permitted.

        No follow-up automation is permitted.

        Provider output is a DRAFT only.
        """

        raise NotImplementedError

    # ------------------------------------------------------------------
    # Optional provider metadata
    # ------------------------------------------------------------------

    def describe(self) -> Dict[str, str]:
        """
        Return basic provider metadata.

        This is intentionally NOT abstract so older/custom providers do
        not break simply because they don't implement a metadata method.

        Returns
        -------
        dict
        """

        return {
            "name": safe_text(
                getattr(self, "name", None),
                default="unknown",
            ),
            "model": safe_text(
                getattr(self, "model_name", None),
                default="unknown",
            ),
        }


# ============================================================================
# PUBLIC EXPORTS
# ============================================================================

__all__ = [
    "AIProvider",
    "NEEDS_REVIEW",
    "OUTREACH_DRAFT",
    "OUTREACH_APPROVED",
    "OUTREACH_REJECTED",
    "OUTREACH_NEEDS_REVIEW",
    "NARRATIVE_FIELDS",
    "OUTREACH_FIELDS",
    "VALID_OUTREACH_STATUSES",
    "safe_text",
    "normalize_subject_options",
    "normalize_outreach_result",
    "needs_review_outreach",
    "normalize_narrative_result",
]
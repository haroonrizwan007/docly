"""
ai_provider/gemini_provider.py
==============================

GeminiProvider — Google Gemini generateContent API provider.

Purpose:
- Calls the native Gemini REST endpoint directly with `requests`:

      https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}

- No SDK, no local model, no GPU — one sequential HTTPS request per lead.
- Retries HTTP 429 (rate limit) and 503 (overloaded) with exponential
  backoff, up to config.GEMINI_MAX_RETRIES retries.
- Requests schema-constrained JSON (responseMimeType + responseSchema).
- Outreach is generated as multi-angle JSON — three angles in one call:
      direct_audit, case_study, short_inquiry
  The direct_audit body becomes the primary draft; each angle's subject
  line feeds subject_options.
- Keeps Phase 3 findings authoritative — the model only does wording.
- Never sends emails.

Configuration (see config.py / .env.example):
    GEMINI_API_KEY        required
    GEMINI_MODEL          default: gemini-3.6-flash
    GEMINI_TEMPERATURE    default: 0.7
    GEMINI_MAX_RETRIES    default: 3
"""

import json
import time

import requests

import config
from .base import AIProvider


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


# ---------------------------------------------------------------------------
# Required response fields
# ---------------------------------------------------------------------------

REQUIRED_NARRATIVE_FIELDS = (
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

REQUIRED_OUTREACH_FIELDS = (
    "generation_status",
    "subject_options",
    "email_body",
)

#: Outreach angle keys requested from the model in a single JSON response.
OUTREACH_ANGLES = (
    "direct_audit",
    "case_study",
    "short_inquiry",
)


# ---------------------------------------------------------------------------
# Retryable HTTP statuses — exponential backoff applies to these only.
# ---------------------------------------------------------------------------

RETRYABLE_STATUSES = frozenset({429, 503})

_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_CAP_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Fixed sign-off — appended by CODE, never written by the model.
# This guarantees every email ends with exactly this block, every time,
# with no risk of the model dropping it, shortening it, or adding/removing
# fields on its own.
# ---------------------------------------------------------------------------

FIXED_SIGNATURE = (
    "Best regards,\n"
    "Usman Ali Sarwar\n"
    "Founder & CEO\n"
    "5thGenTechnologies\n"
    "5thgentechnologies.com"
)

# Same reasoning as FIXED_SIGNATURE -- appended by CODE so the exact
# call-to-action wording/offer is guaranteed every time, never left to
# the model to phrase (and possibly drift) on its own.
FIXED_CTA = (
    "Would you be open to me sending over a quick 2-minute breakdown?"
)


# ---------------------------------------------------------------------------
# Shared AI rules
# ---------------------------------------------------------------------------

_SHARED_RULES = """
You are an experienced human B2B outreach writer working for
5thGen Technologies, a professional web development, SEO, digital
marketing, Shopify and software development company.

Your writing must sound like a real person personally researching
a business before contacting them.

IMPORTANT:

1. Never mention AI.
2. Never mention language models.
3. Never mention prompts.
4. Never mention internal scoring.
5. Never mention confidence levels.
6. Never mention "Phase 3", "Phase 4", "analysis", "lead score",
   "opportunity score", "evidence tier", or internal systems.
7. Never fabricate facts.
8. Never invent a problem that is not supported by the supplied research.
9. Never claim that something is missing unless the supplied research
   explicitly says it is missing.
10. Never invent the prospect's location.
11. Never invent the prospect's services.
12. Never invent competitors.
13. Never invent traffic numbers.
14. Never invent rankings.
15. Never promise guaranteed SEO results.
16. Never use fake urgency in the EMAIL BODY (subject lines have
    their own separate, more specific instructions given below in
    this task -- follow those for subject lines).
17. Never use aggressive sales language.
18. Never use clickbait in the EMAIL BODY (subject lines have their
    own separate, more specific instructions given below in this
    task -- follow those for subject lines).
19. Never use excessive exclamation marks.
20. Never write like a mass marketing campaign.
21. Avoid generic phrases such as:
    "I hope this email finds you well"
    "I wanted to reach out"
    "I came across your amazing business"
    "We are a leading company"
    "We can take your business to the next level"
22. Do not over-praise the prospect with fabricated superlatives,
    invented awards, invented review counts, or invented press
    mentions. A short, genuine compliment is fine ONLY when grounded
    in the supplied research (e.g. their stated services, how their
    site presents itself) -- never invent a fact to compliment.
23. Do not sound desperate.
24. Do not use complicated corporate language.
25. Keep sentences short and natural.
26. Prefer conversational American business English.
27. Write as if a real agency owner or consultant personally
    reviewed the prospect's website.
28. Mention one specific observation from the supplied research.
29. Explain why that observation may matter in practical business terms.
30. Suggest one simple, relevant improvement.
31. Keep the email useful even if the prospect never replies.
32. Use a soft call-to-action rather than a hard sales push.
33. Never write any closing/signature/sign-off yourself (no "Best,",
    no name, no company name, no links, no contact details at the
    end) -- the application appends the real sign-off automatically
    after your text. End your email right after the call-to-action
    question/sentence, with no closing line at all.
34. Do not include phone numbers, prices, or guarantees unless they
    are explicitly supplied in the context.
35. The company name "5thGen Technologies" may be mentioned naturally,
    but do not force it into every sentence.
36. Personalization must come ONLY from supplied data.

MOST IMPORTANT:

The supplied Phase 3 recommendation is authoritative.

You are NOT allowed to change:
- recommended_service
- confidence
- lead_score
- lead_grade

Your job is only to turn the existing findings into natural language.
"""


# ---------------------------------------------------------------------------
# JSON schemas (generationConfig.responseSchema)
# ---------------------------------------------------------------------------

_NARRATIVE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        field: {"type": "STRING"} for field in REQUIRED_NARRATIVE_FIELDS
    },
    "required": list(REQUIRED_NARRATIVE_FIELDS),
}

_OUTREACH_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        angle: {
            "type": "OBJECT",
            "properties": {
                "subject": {"type": "STRING"},
                "email_body": {"type": "STRING"},
            },
            "required": ["subject", "email_body"],
        }
        for angle in OUTREACH_ANGLES
    },
    "required": list(OUTREACH_ANGLES),
}


# ---------------------------------------------------------------------------
# HTTP helper — exponential backoff on HTTP 429/503
# ---------------------------------------------------------------------------

def _backoff_seconds(attempt: int, response: requests.Response | None = None) -> float:
    """
    Exponential backoff delay for retry `attempt` (0-based): 2s, 4s, 8s...

    A Retry-After response header (seconds) takes precedence when present,
    since 429s from the Gemini API frequently carry one.
    """

    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _BACKOFF_CAP_SECONDS)
            except ValueError:
                pass

    return min(
        _BACKOFF_BASE_SECONDS * (2 ** attempt),
        _BACKOFF_CAP_SECONDS,
    )


def _post_with_backoff(
    url: str,
    payload: dict,
    timeout: int,
) -> dict:
    """
    POST to the Gemini generateContent endpoint with exponential backoff.

    Retried (up to config.GEMINI_MAX_RETRIES times):
        - HTTP 429 / 503
        - connection errors and timeouts

    Never retried:
        - other 4xx/5xx responses
        - malformed JSON
    """

    max_retries = config.GEMINI_MAX_RETRIES

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=timeout,
            )
        except (requests.ConnectionError, requests.Timeout):
            if attempt >= max_retries:
                raise RuntimeError(
                    "Gemini provider request failed after "
                    f"{max_retries} retries (connection/timeout)."
                ) from None
            time.sleep(_backoff_seconds(attempt))
            continue

        if response.status_code in RETRYABLE_STATUSES:
            if attempt >= max_retries:
                raise RuntimeError(
                    f"Gemini provider returned HTTP {response.status_code} "
                    f"after {max_retries} retries."
                ) from None
            time.sleep(_backoff_seconds(attempt, response))
            continue

        if response.status_code != 200:
            raise RuntimeError(
                f"Gemini provider returned HTTP {response.status_code}."
            ) from None

        try:
            return response.json()
        except ValueError:
            raise RuntimeError(
                "Gemini provider returned a non-JSON HTTP response."
            ) from None

    raise RuntimeError("Gemini provider request failed.")


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _extract_json_content(data: dict) -> dict:
    """
    Extract the JSON object from a Gemini generateContent response.

    Expected:

    {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "{...JSON...}"}]
                }
            }
        ]
    }

    The text may be split across multiple parts, so all text parts
    are joined before parsing.
    """

    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        raise ValueError(
            "Gemini provider response did not match the expected "
            "generateContent shape (no candidates/content/parts)."
        )

    if not isinstance(parts, list):
        raise ValueError(
            "Gemini provider response parts were not a list."
        )

    content = "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict)
    )

    if not content.strip():
        raise ValueError(
            "Gemini provider returned empty text content."
        )

    # Remove accidental markdown fences.
    content = content.strip()

    if content.startswith("```"):
        content = content.replace("```json", "", 1)
        content = content.replace("```JSON", "", 1)
        content = content.replace("```", "", 1)
        content = content.strip()

    try:
        parsed = json.loads(content)

    except (TypeError, ValueError):
        # The model sometimes wraps valid JSON in stray prose (a
        # leading acknowledgement, a trailing note) despite being told
        # not to. Before giving up, try extracting just the outermost
        # {...} span and parsing that instead of the raw content.
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(content[start:end + 1])
            except (TypeError, ValueError):
                # Include the raw model output (truncated) directly in
                # the error message so it lands in the app's existing
                # log line (ai_outreach.py logs str(exc)) without
                # needing any new logging plumbing here.
                raise ValueError(
                    "Gemini provider did not return valid JSON content. "
                    f"Raw response (truncated to 1500 chars): {content[:1500]!r}"
                )
        else:
            raise ValueError(
                "Gemini provider did not return valid JSON content. "
                f"Raw response (truncated to 1500 chars): {content[:1500]!r}"
            )

    if not isinstance(parsed, dict):
        raise ValueError(
            "Gemini provider JSON content was not a JSON object."
        )

    return parsed


def _strip_model_signoff(email_body: str) -> str:
    """
    Defense in depth: the system prompt explicitly tells the model never
    to write its own closing/signature, but if it does anyway, drop it --
    so the FIXED_SIGNATURE/FIXED_CTA appended afterward are never doubled
    up with a model-written closing.

    Two passes:
    1. Look near the end of the text for a standalone closing salutation
       line ("Best regards," / "Best," / "Regards," / "Sincerely," ...).
       If found, cut everything from that line onward -- this correctly
       handles the model inventing its OWN name in between (e.g. "Best
       regards,\nJohn Smith\n5thGen Technologies"), which a simple
       trailing-marker scan would miss because "John Smith" doesn't
       match any known marker and would otherwise stop the scan early.
    2. Trailing-marker scan (as before) for any leftover marker lines
       with no explicit salutation line above them (e.g. a body that
       ends directly with "5thGen Technologies\nhttps://...").
    """

    SALUTATIONS = (
        "best regards", "best", "regards", "sincerely",
        "warm regards", "kind regards", "thanks",
        "thank you", "cheers",
    )

    SIGNOFF_MARKERS = (
        "best,", "best regards", "regards,", "sincerely,",
        "5thgen technologies", "5thgentechnologies",
        "https://5thgentechnologies.com",
        "info@5thgentechnologies.com", "whatsapp:",
        "would you be open to me sending", "2-minute breakdown",
        "quick 2-minute breakdown",
    )

    lines = email_body.splitlines()

    # Pass 1: cut at an explicit closing-salutation line, searched only
    # in the trailing window so we never accidentally cut mid-body text
    # that happens to contain the word "thanks" etc.
    search_from = max(len(lines) - 8, 0)
    for i in range(len(lines) - 1, search_from - 1, -1):
        candidate = lines[i].strip().lower().rstrip(",")
        if candidate in SALUTATIONS:
            lines = lines[:i]
            break

    # Pass 2: trailing-marker scan for any remaining marker lines.
    while lines:
        candidate = lines[-1].strip().lower()
        if not candidate or any(marker in candidate for marker in SIGNOFF_MARKERS):
            lines.pop()
            continue
        break

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Gemini Provider
# ---------------------------------------------------------------------------

class GeminiProvider(AIProvider):

    name = "gemini"

    def __init__(self):
        self.model_name = config.GEMINI_MODEL or "unconfigured"

    # -----------------------------------------------------------------------
    # Availability
    # -----------------------------------------------------------------------

    def is_available(self) -> bool:
        return bool(
            config.GEMINI_API_KEY
            and config.GEMINI_MODEL
        )

    # -----------------------------------------------------------------------
    # Shared request plumbing
    # -----------------------------------------------------------------------

    @staticmethod
    def _endpoint() -> str:
        return (
            f"{GEMINI_API_BASE}/{config.GEMINI_MODEL}"
            f":generateContent?key={config.GEMINI_API_KEY}"
        )

    def _generate_json(
        self,
        system_instruction: str,
        user_prompt: str,
        schema: dict,
        max_output_tokens: int,
    ) -> dict:
        """
        One schema-constrained generateContent call, returning the
        parsed JSON object.
        """

        payload = {
            "system_instruction": {
                "parts": [{"text": system_instruction}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}],
                },
            ],
            "generationConfig": {
                # Constrain the model's decoding to syntactically valid,
                # schema-shaped JSON at the API level -- far more reliable
                # than prompt wording alone at preventing prose mixed
                # into the JSON.
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "temperature": config.GEMINI_TEMPERATURE,
                "maxOutputTokens": max_output_tokens,
            },
        }

        data = _post_with_backoff(
            self._endpoint(),
            payload,
            config.REQUEST_TIMEOUT_SECONDS,
        )

        return _extract_json_content(data)

    # -----------------------------------------------------------------------
    # Narrative generation
    # -----------------------------------------------------------------------

    def generate_narrative(
        self,
        context: dict,
        findings: dict,
    ) -> dict:

        if not self.is_available():
            raise RuntimeError(
                "GeminiProvider is not configured. Set GEMINI_API_KEY "
                "in .env, or use AI_PROVIDER=mock."
            )

        prompt = self._build_narrative_prompt(
            context,
            findings,
        )

        system_instruction = (
            _SHARED_RULES
            + """

For this task, return ONLY one valid JSON object.

The JSON object must contain exactly these string fields:

"""
            + ", ".join(REQUIRED_NARRATIVE_FIELDS)
        )

        parsed = self._generate_json(
            system_instruction,
            prompt,
            schema=_NARRATIVE_SCHEMA,
            max_output_tokens=4096,
        )

        return {
            field: str(
                parsed.get(
                    field,
                    "Unknown — model did not return this field",
                )
            )
            for field in REQUIRED_NARRATIVE_FIELDS
        }

    # -----------------------------------------------------------------------
    # Narrative prompt
    # -----------------------------------------------------------------------

    @staticmethod
    def _build_narrative_prompt(
        context: dict,
        findings: dict,
    ) -> str:

        return f"""
Analyze the supplied business research.

Do not perform new research.

Do not browse the internet.

Do not invent information.

The research below was already collected by the application.

BUSINESS / WEBSITE RESEARCH:
{json.dumps(context, indent=2, default=str)}

AUTHORITATIVE PHASE 3 FINDINGS:
{json.dumps(findings, indent=2, default=str)}

Use the findings exactly as provided.

Your job is to improve the explanation and wording only.
"""


    # -----------------------------------------------------------------------
    # Outreach generation
    # -----------------------------------------------------------------------

    def generate_outreach(
        self,
        context: dict,
        findings: dict,
    ) -> dict:

        if not self.is_available():
            raise RuntimeError(
                "GeminiProvider is not configured. Set GEMINI_API_KEY "
                "in .env, or use AI_PROVIDER=mock."
            )

        recommended = findings.get(
            "recommended_service"
        )

        analysis_reason = (findings.get("analysis_reason") or "").strip()
        sales_angle = (findings.get("sales_angle") or "").strip()
        has_real_finding = bool(analysis_reason) or bool(sales_angle)

        # ---------------------------------------------------------------
        # Hard safety rule -- only skip AI generation entirely when
        # there is truly NOTHING to work with (no recommended_service
        # AND no reasoning text at all). If Phase 3 landed on "Needs
        # Review" but still wrote down a real, evidence-based finding
        # (just at LOW confidence), that finding is genuine -- write a
        # dynamic, appropriately tentative email from it instead of
        # blocking every low-confidence lead from outreach.
        # ---------------------------------------------------------------

        if recommended is None and not has_real_finding:

            return {
                "generation_status": "NEEDS_REVIEW",
                "subject_options": [],
                "email_body": (
                    "No sufficiently supported service opportunity "
                    "was identified from the collected research. "
                    "Manual review is required before outreach."
                ),
            }

        if recommended == "Needs Review" and not has_real_finding:

            return {
                "generation_status": "NEEDS_REVIEW",
                "subject_options": [],
                "email_body": (
                    "No sufficiently supported service opportunity "
                    "was identified from the collected research. "
                    "Manual review is required before outreach."
                ),
            }

        # If we're proceeding on a "Needs Review" + genuine-finding
        # case, build a version of findings for the AI call with a
        # safe, general-purpose service substituted in (these
        # low-confidence findings are almost always about site
        # content/structure). This is only used to phrase the email --
        # it never overwrites Phase 3's own stored recommended_service.
        effective_findings = findings
        if recommended == "Needs Review" and has_real_finding:
            effective_findings = dict(findings)
            effective_findings["recommended_service"] = "Website Development"
            effective_findings["_low_confidence_soft_pitch"] = True

        prompt = self._build_outreach_prompt(
            context,
            effective_findings,
        )

        system_instruction = (
            _SHARED_RULES
            + """

You are now writing real cold emails. They must feel like a real
person personally looked at this business's site -- not a template.

You must respond with ONLY a single JSON object. No text before it,
no text after it, no markdown, no explanation of what you wrote --
just the JSON object itself, starting with { and ending with }.

Required JSON shape -- the same core message written as THREE
different outreach angles (exact keys, all strings):

{"direct_audit": {"subject": "...", "email_body": "..."}, "case_study": {"subject": "...", "email_body": "..."}, "short_inquiry": {"subject": "...", "email_body": "..."}}

The three angles:

- direct_audit: a straightforward, professional email (~110-150
  words) that names the ONE real issue found and its practical
  impact, following the 5-part structure below exactly.
- case_study: the same core message (~110-150 words) framed around
  a short "we recently helped a similar business with the same
  thing" angle. Keep it generic and honest -- NEVER invent a client
  name, numbers, results, or a recognizable story; "a similar local
  services business" is the right level of specificity. Otherwise
  follows the same 5-part structure.
- short_inquiry: a very short (50-80 words), casual inquiry-style
  version -- greeting, the ONE issue phrased as a simple observation,
  and one genuine question. No compliment paragraph needed here.

The mandatory 5-part structure for direct_audit and case_study
email bodies (plain text, no JSON inside them):

Line 1: "Hi {greeting_name}," -- use the greeting name given to you
in the user message below. Never write the literal text "[Name]".

Paragraph 2: Open with "I came across {business_name} and was
impressed by..." (or a close natural variant), followed by ONE
genuine, specific compliment grounded ONLY in facts present in the
supplied research below (their stated services, their service area,
how their site describes itself, real awards/recognitions only if
literally present in the data). Never invent an award, review count,
press mention, statistic, or service-area detail not in the supplied
data.

Paragraph 3: "While reviewing your website, I noticed..." followed
by the ONE single real issue actually supported by this lead's
evidence for the recommended service below, in plain words (no
jargon). Do not invent a second issue.

Paragraph 4: ONE short paragraph on the practical, plausible impact
of that issue for a business like theirs (e.g. "this could mean
missing inquiries from customers who are already interested"). Stay
grounded and modest -- no guaranteed-results claims, no invented
numbers, no "losing thousands of dollars" type exaggeration.

Paragraph 5: ONE short, soft, slightly vague teaser sentence about
"a couple of opportunities" or "a few small changes" that could help
-- without listing new specific issues beyond the one already named
in paragraph 3. This sets up the call-to-action, which the
application adds automatically after your text.

Stop there. Do not add a call-to-action, a closing, a signature, a
name, a company name, a link, an email, or a phone/WhatsApp number
-- the application adds those automatically after your text. Do not
add any note about word count or any other meta-commentary.

Each angle's "subject" is its subject line: designed to make the
recipient feel they NEED to open this email -- use a loss-aversion
hook (what they may be missing out on) or a curiosity hook, tied
ONLY to the real issue found for THIS business. HARD RULE: every
subject line must include the business's actual name (the
business_name given in the supplied research below) -- never a
generic phrase with no name in it. Examples of the angle (not
literal text to copy): "[Business Name], are you missing local
leads?", "One thing costing [Business Name] customers", "Quick
question about [Business Name]'s site". The hook must stay truthful
and specific to this lead's real evidence -- never invent a threat,
a deadline, a countdown, or a fact not in the supplied data. No ALL
CAPS, no excessive punctuation (no "!!!", no multiple "?"), no spammy
words ("FREE", "ACT NOW", "URGENT"), and no generic marketing phrases
("Unlock", "Hidden", "Boost Your Growth") -- the hook must be
specific to this business, not a template phrase that could apply
to anyone. It should still read like it came from a real person, not
a marketing blast. (This is the one intentional exception to the
"no fake urgency / no clickbait" rules above -- it applies ONLY to
subject lines, and only when the hook is truthfully grounded in
this lead's real evidence. The email bodies themselves must still
follow every rule above with no exception.)
"""
        )

        parsed = self._generate_json(
            system_instruction,
            prompt,
            schema=_OUTREACH_SCHEMA,
            max_output_tokens=8192,
        )

        # ---------------------------------------------------------------
        # Validate the multi-angle response
        # ---------------------------------------------------------------

        angles = {}
        for angle in OUTREACH_ANGLES:
            value = parsed.get(angle)
            if not isinstance(value, dict):
                continue
            subject = str(value.get("subject") or "").strip()
            body = str(value.get("email_body") or "").strip()
            if subject or body:
                angles[angle] = {
                    "subject": subject,
                    "email_body": body,
                }

        if not angles:
            raise ValueError(
                "Gemini provider response contained none of the "
                "required outreach angles "
                f"({', '.join(OUTREACH_ANGLES)})."
            )

        # Each angle's subject line becomes one subject option, in
        # fixed angle order (direct_audit first) so the primary
        # subject always matches the primary body below.
        subjects = [
            angles[angle]["subject"]
            for angle in OUTREACH_ANGLES
            if angle in angles and angles[angle]["subject"]
        ][:3]

        # The direct_audit angle is the primary draft body. Fall back
        # to the other angles (in order) only if it came back empty.
        email_body = ""
        for angle in OUTREACH_ANGLES:
            if angles.get(angle, {}).get("email_body"):
                email_body = angles[angle]["email_body"]
                break

        generation_status = "DRAFT"

        # ---------------------------------------------------------------
        # Basic quality guard -- runs on the model's own text only,
        # BEFORE the fixed CTA/signature are appended below, so
        # word-count and emptiness checks reflect what the model
        # actually wrote. The CTA and signature are appended separately
        # by code, not by the model, so they don't count toward this
        # check.
        # ---------------------------------------------------------------

        if not email_body or len(email_body.split()) < 40:

            generation_status = "NEEDS_REVIEW"

        if len(subjects) == 0:

            generation_status = "NEEDS_REVIEW"

        # ---------------------------------------------------------------
        # Defense in depth: even though the prompt tells the model never
        # to write its own CTA/closing/signature, strip one off if it
        # slips through anyway, so the fixed CTA/signature below are
        # never doubled up with model-written ones.
        # ---------------------------------------------------------------

        email_body = _strip_model_signoff(email_body)

        # ---------------------------------------------------------------
        # Append the real CTA and signature by CODE, not by the model.
        # This guarantees every DRAFT email always offers the same
        # 2-minute-breakdown offer and ends with exactly the same
        # signature block -- never missing, never reworded, never
        # altered by the model.
        # ---------------------------------------------------------------

        if generation_status == "DRAFT" and email_body:
            email_body = f"{email_body}\n\n{FIXED_CTA}\n\n{FIXED_SIGNATURE}"

        return {
            "generation_status": generation_status,
            "subject_options": subjects,
            "email_body": email_body,
        }

    # -----------------------------------------------------------------------
    # Outreach prompt
    # -----------------------------------------------------------------------

    @staticmethod
    def _build_outreach_prompt(
        context: dict,
        findings: dict,
    ) -> str:

        recommended = findings.get(
            "recommended_service"
        )

        evidence = findings.get(
            "service_evidence"
        )

        sales_angle = findings.get(
            "sales_angle"
        )

        analysis_reason = findings.get(
            "analysis_reason"
        )

        greeting_name = (
            context.get("owner_name")
            or context.get("business_name")
            or "there"
        )

        business_name = context.get("business_name") or greeting_name

        confidence = (findings.get("confidence") or "").strip().upper()
        low_confidence_note = ""
        if confidence == "LOW" or findings.get("_low_confidence_soft_pitch"):
            low_confidence_note = """

--------------------------------------------------
NOTE ON CONFIDENCE
--------------------------------------------------

This finding is LOW confidence. Phrase the issue more tentatively
than usual -- "might be worth a look" / "could be worth checking"
rather than an assertive claim. Never overstate certainty beyond
what the evidence actually supports.
"""

        return f"""
You are writing personalized B2B outreach emails.

The sender is:

5thGen Technologies

The prospect's information comes from stored website research.

Do NOT browse or re-fetch the website.

--------------------------------------------------
GREETING NAME FOR THESE EMAILS
--------------------------------------------------

Use exactly this in your greeting ("Hi {greeting_name},"):

{greeting_name}

Do not write the literal text "[Name]" -- use the value above.

--------------------------------------------------
PROSPECT RESEARCH
--------------------------------------------------

{json.dumps(context, indent=2, default=str)}

--------------------------------------------------
AUTHORITATIVE RECOMMENDATION
--------------------------------------------------

Recommended service:
{recommended}

Service evidence:
{json.dumps(evidence, indent=2, default=str)}

Existing sales angle:
{sales_angle}

Existing analysis reason:
{analysis_reason}
{low_confidence_note}
--------------------------------------------------
WRITING TASK
--------------------------------------------------

Turn the supplied information into short, human-sounding emails --
one per angle (direct_audit, case_study, short_inquiry), as
described in the system instruction.

The emails should feel like they were written after someone quickly
reviewed the business website.

Do not simply copy the evidence paragraph word for word.

Rewrite it naturally, in your own words.

For example, instead of:

"No address or service-area text was found..."

A human writer might say something closer to:

"there's no address or service-area info on the site, which can
hurt local search visibility"

But ONLY use that style if it is supported by the supplied evidence.

Do not exaggerate the problem.

Do not say the prospect is "losing thousands of customers."

Do not say their SEO is "bad."

Do not claim Google penalizes them.

Do not claim competitors are outranking them unless supplied.

Do not promise rankings.

Do not make the emails sound like an audit report.

The prospect should understand the observation without
seeing internal technical language.

Keep the tone:

- calm
- friendly
- direct
- professional
- useful
- confident but not pushy

Avoid:

"Hope you're doing well."

"I wanted to reach out."

"I came across your amazing company."

"Take your business to the next level."

"Unlock your full potential."

"10x your growth."

"Guaranteed results."

"Limited time."

"Act now."

"Just checking in."

"Following up on my previous email."

No emojis.

No exclamation marks unless genuinely necessary.

Do not write a call-to-action, closing, signature, name, company
name, link, email, or phone/WhatsApp number in any angle -- the
application appends the real call-to-action and signature
automatically after your text.
"""

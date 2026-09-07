"""
ai_provider/mock_provider.py
=============================
MockProvider — the "Local/Free provider" that serves as the built-in
fallback (config.AI_PROVIDER=mock).

This is NOT a language model. It never downloads anything, never makes a
network call, never uses a GPU/CPU-heavy inference step, and needs no API
key — it turns the deterministic findings computed in ai_analysis.py into
readable sentences. Every field it produces is traceable straight back to
a stored Phase 1/2 fact, so there's nothing to hallucinate.

Revision 3 (over-conservative "Needs Review" fix): the per-service text
below now reflects ai_analysis.py's 3-tier model (STRONG/MODERATE/NONE —
see that module's docstring) and packs each recommendation into 6 clearly
labeled parts inside the existing sales_angle/analysis_reason fields (no
DB schema change needed):
  analysis_reason -> "Evidence Found" (the raw, stated facts behind the
                      recommendation, or — for Needs Review — exactly
                      which checks were run and came back clean)
  sales_angle     -> "Why This Is An Opportunity" + "What We Should
                      Pitch" + "What's Missing / To Verify", each on
                      its own labeled line
recommended_service and confidence are already their own top-level DB
fields/UI metrics, so items 1-2 of the requested 6-part structure need no
change here.

Where a real AI provider connects: see ai_provider/gemini_provider.py —
it takes the same `context`/`findings` inputs and returns the same field
shape, activated with AI_PROVIDER=gemini in .env.
"""

from .base import AIProvider, NEEDS_REVIEW


def _fmt_list(items):
    items = [i for i in (items or []) if i]
    if not items:
        return "none detected"
    return ", ".join(items)


_NO_EVIDENCE_TEXT = {
    "SEO": "No supported SEO gap found — title, meta description, on-site content depth, "
           "and platform were all checked and came back clean.",
    "Digital Marketing": "No supported digital-marketing gap detected from available data.",
    "Website Development": "Existing website is reachable, has a Contact page, and either has a "
                            "detected platform or more than one page checked — no supported rebuild signal.",
    "Shopify Development": "No ecommerce/storefront evidence (platform or cart/checkout language) found — Shopify is not indicated.",
    "Software Development": "No explicit custom-software/integration phrase found in the collected text.",
}

# (substring to look for in a reason, service) -> (why this matters, what to pitch).
# Checked against the actual evidence reasons so the "why"/"pitch" text stays tied
# to the specific fact found for this lead, not a generic service-level guess.
# Falls back to _GENERIC_WHY_PITCH when no specific match is found.
_SPECIFIC_WHY_PITCH = [
    ("both page title and meta description are missing", "SEO", (
        "Search engines have nothing written to show in a result snippet, which directly hurts click-through even if the site ranks.",
        "Offer a fast on-page metadata pass (titles + descriptions site-wide) as a low-cost, visible first engagement.",
    )),
    ("too short to be a useful search-result snippet", "SEO", (
        "A thin or duplicate meta description gets truncated or ignored by search engines, wasting a free chance to earn the click.",
        "Offer to rewrite metadata across the site's key pages — a quick, demonstrable win.",
    )),
    ("verbatim duplicate of the page title", "SEO", (
        "A duplicated title/description looks unpolished in search results and gives no extra reason to click.",
        "Offer to rewrite metadata across the site's key pages — a quick, demonstrable win.",
    )),
    ("no address or service-area text was found", "SEO", (
        "Local service businesses rely on address/service-area signals for local-pack and 'near me' search visibility — without them, they're likely losing nearby search traffic to competitors who have it.",
        "Offer a local-SEO pass: on-site NAP (name/address/phone) consistency, service-area pages, and a Google Business Profile check.",
    )),
    ("services content is present but thin", "SEO", (
        "Thin service-page content gives search engines and visitors little to work with, limiting both rankings and conversion.",
        "Offer expanded, keyword-relevant service-page content as a content/SEO engagement.",
    )),
    ("template-based website builder", "SEO", (
        "Builder platforms like this typically limit control over technical SEO (custom schema, advanced meta control, some plugin/script integrations) compared to a custom or WordPress build.",
        "Offer an SEO audit specific to the platform's constraints, or scope a migration if growth needs outgrow the builder.",
    )),
    ("site text explicitly references seo", "SEO", (
        "The business is already talking about search/ranking on its own site — that's a direct, low-friction opening.",
        "Reference their own language back to them and offer a free ranking/visibility audit.",
    )),
    ("no dedicated contact page was found", "Website Development", (
        "Visitors who can't quickly find a way to get in touch are more likely to leave without converting — a real, structural lead-loss point.",
        "Offer to add/streamline a dedicated Contact page (or contact form) as a quick, visible fix.",
    )),
    ("no recognizable platform was detected and only the homepage", "Website Development", (
        "A single-page, unidentified-platform site is usually hard to maintain, extend, or optimize going forward.",
        "Offer a discovery call to assess whether a modern rebuild (WordPress or similar) would better support their growth.",
    )),
    ("did not respond during research", "Website Development", (
        "An unreachable site means the business is currently invisible to anyone trying to find it online right now.",
        "Lead with urgency: their site appears to be down — offer an immediate fix/rebuild conversation.",
    )),
    ("no website on file", "Website Development", (
        "Without a website, the business is relying entirely on word-of-mouth/offline channels for discovery.",
        "Offer a starter website build scoped to their core services.",
    )),
    ("no usable website url was available", "Website Development", (
        "Without a working, reachable site on file, the business has no verified online presence to build on.",
        "Offer a discovery call to confirm their current web presence and scope a build or fix.",
    )),
    ("ecommerce platform detected", "Shopify Development", (
        "They already run on ecommerce infrastructure — there's a concrete, existing storefront to optimize or extend.",
        "Offer a storefront audit (conversion, checkout flow, product-page SEO) on the platform they already use.",
    )),
    ("ecommerce/storefront language found", "Shopify Development", (
        "The site's own language points to selling online — a live or planned storefront is a concrete Shopify entry point.",
        "Offer a Shopify build/migration scoped around the products or services already referenced on the site.",
    )),
    ("no usable contact info was extracted", "Digital Marketing", (
        "A reachable site with no easy way to capture a lead is likely leaving inquiries on the table.",
        "Offer a lead-capture/marketing audit focused on turning visits into contactable leads (forms, click-to-call, tracking).",
    )),
    ("references marketing/advertising/branding", "Digital Marketing", (
        "The business already talks about marketing/branding on its own site — a natural, low-friction opening for a marketing conversation.",
        "Reference their own language back to them and offer a marketing/brand audit.",
    )),
    ("custom-software/integration signal", "Software Development", (
        "Explicit mention of custom tooling/integration needs suggests off-the-shelf solutions may not be covering everything they need.",
        "Offer a scoping call to understand the specific integration/automation need referenced on their site.",
    )),
]

_GENERIC_WHY_PITCH = {
    "SEO": (
        "The evidence above is a concrete, checkable gap in how the site presents itself to search engines and searchers.",
        "Offer a focused, low-cost SEO/content pass targeting the specific gap found.",
    ),
    "Digital Marketing": (
        "The evidence above suggests the business isn't fully capturing or converting the traffic its site could bring in.",
        "Offer a marketing/lead-capture audit.",
    ),
    "Website Development": (
        "The evidence above is a concrete, structural gap in the site itself, not just its content.",
        "Offer a discovery call to scope a fix or rebuild around the specific gap found.",
    ),
    "Shopify Development": (
        "The evidence above points to an existing or intended online storefront.",
        "Offer a storefront build/optimization scoped around what's already there.",
    ),
    "Software Development": (
        "The evidence above suggests a specific technical need beyond a standard website.",
        "Offer a scoping call around the specific integration/automation need found.",
    ),
}


def _why_and_pitch(service, reasons):
    for needle, svc, pair in _SPECIFIC_WHY_PITCH:
        if svc == service and any(needle in r.lower() for r in reasons):
            return pair
    return _GENERIC_WHY_PITCH.get(service, ("Worth a closer look based on the evidence above.", "Start with a discovery call."))


def _verification_note(recommended, tier):
    if recommended == NEEDS_REVIEW:
        return (
            "See Evidence Found above for exactly what was checked — none of it, alone or combined, "
            "met the bar for a specific recommendation. Verify manually (page speed, content quality, "
            "a competitor comparison, or a direct conversation) or re-run Website Research if more "
            "pages become reachable."
        )
    if tier == "strong":
        return (
            "This is a directly observed fact from Phase 2's stored research data — still worth a quick "
            "manual confirmation (e.g. a live site visit) before using it in outreach."
        )
    return (
        "This is a moderate-confidence signal inferred from the collected data, not a certainty. "
        "Confirm manually — a live site visit, a quick call, or a look at 2-3 local competitors — "
        "before presenting it as a firm finding."
    )


class MockProvider(AIProvider):
    name = "mock"
    model_name = "5thGen-RuleBasedAnalyzer-v1"

    def is_available(self) -> bool:
        # Pure Python, no external dependency — always available.
        return True

    def generate_narrative(self, context: dict, findings: dict) -> dict:
        business_name = context.get("business_name") or "This business"
        category = (context.get("category") or "").strip()
        research_status = context.get("research_status") or "MISSING"
        tech = context.get("technology_detected") or []
        page_title = context.get("page_title") or ""
        meta_description = context.get("meta_description") or ""
        description = context.get("business_description") or ""
        pages_checked = context.get("pages_checked") or 0

        # --- business_category -------------------------------------------------
        if category:
            business_category = category
        elif page_title:
            business_category = f"Unknown — inferred only from page title \"{page_title[:80]}\""
        else:
            business_category = "Unknown — not stated in the CSV and not detected from the website"

        # --- website_platform ---------------------------------------------------
        if research_status != "COMPLETED":
            website_platform = f"Unknown — website research status was {research_status}"
        elif tech:
            website_platform = _fmt_list(tech)
        else:
            website_platform = "No recognizable platform/CMS signature found (may be custom-built or a very light site)"

        # --- business_summary ----------------------------------------------------
        snippet = (meta_description or description or "").strip()
        if research_status == "COMPLETED" and snippet:
            business_summary = f"{business_name} — {snippet[:280]}"
        elif research_status == "COMPLETED":
            business_summary = (
                f"{business_name}'s website ({pages_checked} page(s) checked) had no "
                f"meta description or extractable summary text."
            )
        elif research_status == "FAILED":
            business_summary = (
                f"{business_name}'s website could not be reached during Phase 2 research, "
                f"so no on-site business summary is available."
            )
        elif research_status == "SKIPPED":
            business_summary = (
                f"No usable website URL was on file for {business_name}, so no on-site "
                f"business summary is available."
            )
        else:
            business_summary = f"{business_name} has not yet been researched by Phase 2."

        # --- website_quality_observations ----------------------------------------
        if research_status == "COMPLETED":
            obs = []
            obs.append(f"{pages_checked} page(s) checked" if pages_checked else "only the homepage was reachable")
            obs.append("meta description present" if meta_description.strip() else "missing meta description")
            obs.append(f"platform detected: {_fmt_list(tech)}" if tech else "no CMS/platform fingerprint detected")
            website_quality_observations = "; ".join(obs) + "."
        elif research_status == "FAILED":
            website_quality_observations = "Website did not respond during research — likely down, misconfigured, or blocking automated requests."
        elif research_status == "SKIPPED":
            website_quality_observations = "No valid website URL to evaluate."
        else:
            website_quality_observations = "Not yet researched."

        # --- per-service opportunity text ----------------------------------------
        # Built directly from each service's own evidence reasons — never a
        # fixed template — so the text can never claim a cause that isn't
        # actually true for this lead (see module docstring).
        evidence = findings.get("service_evidence", {})
        recommended = findings.get("recommended_service")

        def service_text(service):
            info = evidence.get(service, {"tier": "none", "reasons": []})
            if info["tier"] == "none" or not info["reasons"]:
                return _NO_EVIDENCE_TEXT.get(service, "No supported evidence found from available data.")
            prefix = "Primary opportunity: " if service == recommended else "Secondary opportunity: "
            tier_label = "(strong evidence) " if info["tier"] == "strong" else "(moderate evidence) "
            return prefix + tier_label + " ".join(info["reasons"])

        seo_opportunities = service_text("SEO")
        digital_marketing_opportunities = service_text("Digital Marketing")
        website_development_opportunities = service_text("Website Development")
        shopify_opportunities = service_text("Shopify Development")
        software_development_opportunities = service_text("Software Development")

        # --- analysis_reason == "Evidence Found" ----------------------------------
        lead_score = findings.get("lead_score")
        lead_grade = findings.get("lead_grade")
        confidence = findings.get("confidence")
        tier = findings.get("service_evidence", {}).get(recommended, {}).get("tier", "none")

        if recommended == NEEDS_REVIEW or not recommended:
            evidence_found_text = " ".join(findings.get("reasons") or [])
        else:
            recommended_reasons = evidence.get(recommended, {}).get("reasons", [])
            evidence_found_text = " ".join(recommended_reasons) if recommended_reasons else "No specific evidence recorded."

        analysis_reason = (
            f"**Evidence Found:** {evidence_found_text} "
            f"(General opportunity score: {lead_score}/100, {lead_grade}.)"
        ).strip()

        # --- sales_angle == Why / Pitch / What's Missing ---------------------------
        if recommended == NEEDS_REVIEW or not recommended:
            why_text = "None of the checks performed on this lead's Phase 2 data turned up a specific, defensible service gap — see Evidence Found above for exactly what was checked."
            pitch_text = "No specific pitch yet — this lead needs manual review before outreach, not a guessed angle."
        else:
            recommended_reasons = evidence.get(recommended, {}).get("reasons", [])
            why_text, pitch_text = _why_and_pitch(recommended, recommended_reasons)

        verify_text = _verification_note(recommended, tier)

        sales_angle = (
            f"**Why This Is An Opportunity:** {why_text}\n\n"
            f"**What We Should Pitch:** {pitch_text}\n\n"
            f"**What's Missing / To Verify:** {verify_text}"
        )

        return {
            "business_category": business_category,
            "business_summary": business_summary,
            "website_platform": website_platform,
            "website_quality_observations": website_quality_observations,
            "seo_opportunities": seo_opportunities,
            "digital_marketing_opportunities": digital_marketing_opportunities,
            "website_development_opportunities": website_development_opportunities,
            "shopify_opportunities": shopify_opportunities,
            "software_development_opportunities": software_development_opportunities,
            "sales_angle": sales_angle,
            "analysis_reason": analysis_reason,
        }

    # -----------------------------------------------------------------------
    # Phase 4 — personalized outreach email generation
    # -----------------------------------------------------------------------
    # Deliberately reuses the SAME evidence-reason -> (why, pitch) mapping
    # (_why_and_pitch/_SPECIFIC_WHY_PITCH) that generate_narrative() above
    # uses for "Why This Is An Opportunity" / "What We Should Pitch" — the
    # email can never say something different from what the AI Lead
    # Analysis tab already shows for the same lead, because it's built
    # from the identical reason strings, not a separate guess.
    def generate_outreach(self, context: dict, findings: dict) -> dict:
        recommended = findings.get("recommended_service")

        if recommended == NEEDS_REVIEW or not recommended:
            return {
                "generation_status": "NEEDS_REVIEW",
                "subject_options": [],
                "email_body": (
                    "No sufficiently supported service opportunity was identified from the "
                    "collected research for this lead. Manual review is required before outreach — "
                    "see the AI Lead Analysis tab's Evidence Found section for exactly what was "
                    "checked and came back clean."
                ),
            }

        evidence = findings.get("service_evidence", {})
        reasons = evidence.get(recommended, {}).get("reasons", [])
        why_text, pitch_text = _why_and_pitch(recommended, reasons)

        business_name = context.get("business_name") or "there"
        category = (context.get("category") or "").strip()
        location_text = (context.get("location_text") or "").strip()
        primary_reason = reasons[0] if reasons else why_text

        # --- personalized opening (never "Dear Business Owner...") --------
        if category:
            opening = f"Hi {business_name} team — came across your site while looking at {category.lower()}s"
        else:
            opening = f"Hi {business_name} team — came across your site"
        if location_text:
            opening += f" in the area"
        opening += "."

        # --- specific, evidence-based observation --------------------------
        observation = _lowercase_first(primary_reason.rstrip("."))

        body = (
            f"{opening}\n\n"
            f"One thing stood out: {observation}. {why_text}\n\n"
            f"{pitch_text} If that sounds useful, would a quick 10-15 minute call this "
            f"week work? Happy to share specifics first if that's easier.\n\n"
            f"Either way, best of luck with the business."
        )

        subject_options = _subject_options(business_name, recommended, primary_reason)

        return {
            "generation_status": "DRAFT",
            "subject_options": subject_options,
            "email_body": body,
        }


def _lowercase_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


_SUBJECT_TOPIC_WORDS = {
    "SEO": "website visibility",
    "Digital Marketing": "getting more leads from your site",
    "Website Development": "your website",
    "Shopify Development": "your online store",
    "Software Development": "a tooling idea",
}


def _subject_options(business_name: str, recommended: str, primary_reason: str) -> list:
    topic = _SUBJECT_TOPIC_WORDS.get(recommended, "your website")
    options = [
        f"Quick idea for {business_name}",
        f"One {topic} note for {business_name}",
        "Quick website question",
    ]
    # de-duplicate while preserving order, in case business_name ever makes
    # two templates collide
    seen = set()
    unique = []
    for opt in options:
        if opt not in seen:
            seen.add(opt)
            unique.append(opt)
    return unique[:3]

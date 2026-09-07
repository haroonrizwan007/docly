"""
ai_provider package
===================

Central provider registry for 5thGenLeadGenerator.

Supported providers:
    - mock
    - gemini

Design goals:
    - One public entry point: get_provider()
    - Provider selection controlled by config.AI_PROVIDER
    - Automatic fallback to MockProvider when a provider is
      unavailable or incorrectly configured
    - No provider-specific logic required in app.py
    - Safe for Phase 3 analysis and Phase 4/5 outreach
    - Gemini provider keeps laptop lightweight by performing HTTP
      requests only
    - No local model, GPU, Ollama installation, or heavy AI
      dependency is required on the laptop

Usage:

    from ai_provider import get_provider

    provider = get_provider()

    result = provider.generate_narrative(...)
    result = provider.generate_outreach(...)
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Type


# ---------------------------------------------------------------------------
# Provider imports
# ---------------------------------------------------------------------------

from .base import AIProvider
from .mock_provider import MockProvider
from .gemini_provider import GeminiProvider


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

_logger = logging.getLogger("5thGenLeadGenerator")


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
#
# Keep this dictionary as the single provider registry.
#
# Adding a future provider should normally require only:
#
#   1. Create the provider class.
#   2. Import it above.
#   3. Add it here.
#
# app.py should NOT need to change.
# ---------------------------------------------------------------------------

_PROVIDERS: Dict[str, Type[AIProvider]] = {
    "mock": MockProvider,
    "gemini": GeminiProvider,
}


# ---------------------------------------------------------------------------
# Provider normalization
# ---------------------------------------------------------------------------

def _normalize_provider_name(provider_name: Optional[str]) -> str:
    """
    Normalize a provider name.

    Rules:
        - None / blank -> mock
        - lowercase
        - trim whitespace
        - unknown values -> mock

    This function intentionally does not instantiate a provider.
    """

    name = (provider_name or "").strip().lower()

    if not name:
        return "mock"

    if name not in _PROVIDERS:
        _logger.warning(
            "Unknown AI provider '%s' requested. Falling back to mock.",
            name,
        )
        return "mock"

    return name


# ---------------------------------------------------------------------------
# Provider availability helper
# ---------------------------------------------------------------------------

def _provider_is_available(provider: AIProvider) -> bool:
    """
    Safely check provider availability.

    A provider's is_available() method should normally return a boolean,
    but this wrapper prevents a broken availability check from crashing
    the whole Streamlit dashboard.

    If availability checking itself fails, the provider is considered
    unavailable and the caller can fall back to MockProvider.
    """

    try:
        return bool(provider.is_available())

    except Exception as exc:
        _logger.exception(
            "AI provider '%s' availability check failed: %s",
            getattr(provider, "name", "unknown"),
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------

def _build_provider(provider_name: str) -> AIProvider:
    """
    Instantiate a registered provider.

    This function handles provider constructor failures and falls back
    to MockProvider instead of allowing the dashboard to crash.
    """

    provider_cls = _PROVIDERS.get(
        provider_name,
        MockProvider,
    )

    try:
        provider = provider_cls()

    except Exception as exc:
        _logger.exception(
            "Failed to initialize AI provider '%s': %s",
            provider_name,
            exc,
        )

        if provider_name != "mock":
            _logger.warning(
                "Falling back to MockProvider because provider "
                "'%s' could not be initialized.",
                provider_name,
            )

        return MockProvider()

    return provider


# ---------------------------------------------------------------------------
# Public provider factory
# ---------------------------------------------------------------------------

def get_provider(provider_name: Optional[str] = None) -> AIProvider:
    """
    Return the active AI provider.

    Selection priority:

        1. Explicit provider_name argument
        2. config.AI_PROVIDER
        3. mock

    If the selected provider is unavailable or fails initialization,
    MockProvider is returned.

    This guarantees that the rest of the application can safely do:

        provider = get_provider()

    without needing to know whether the configured provider is ready.

    Examples:

        provider = get_provider()

        provider = get_provider("mock")

        provider = get_provider("gemini")
    """

    # Import config locally to avoid circular imports during package load.
    import config

    requested_name = (
        provider_name
        if provider_name is not None
        else getattr(config, "AI_PROVIDER", "mock")
    )

    normalized_name = _normalize_provider_name(
        requested_name
    )

    provider = _build_provider(
        normalized_name
    )

    # Mock is always the final safety provider.
    if normalized_name == "mock":
        return provider

    # Check whether the requested provider is actually configured.
    if not _provider_is_available(provider):

        _logger.warning(
            "AI provider '%s' is unavailable or not configured. "
            "Falling back to MockProvider.",
            normalized_name,
        )

        return MockProvider()

    return provider


# ---------------------------------------------------------------------------
# Provider information
# ---------------------------------------------------------------------------

def get_provider_info(provider_name: Optional[str] = None) -> dict:
    """
    Return safe provider information for UI/logging.

    This function intentionally does NOT expose API keys.

    Example return:

        {
            "requested_provider": "gemini",
            "resolved_provider": "gemini",
            "available": True,
            "name": "gemini",
            "model": "gemini-3.6-flash"
        }

    If unavailable:

        {
            "requested_provider": "gemini",
            "resolved_provider": "mock",
            "available": False,
            "name": "mock",
            "model": "mock"
        }
    """

    import config

    requested = (
        provider_name
        if provider_name is not None
        else getattr(config, "AI_PROVIDER", "mock")
    )

    resolved_name = _normalize_provider_name(requested)

    provider = _build_provider(resolved_name)

    available = _provider_is_available(provider)

    if not available and resolved_name != "mock":
        fallback = MockProvider()

        return {
            "requested_provider": resolved_name,
            "resolved_provider": "mock",
            "available": False,
            "name": getattr(fallback, "name", "mock"),
            "model": getattr(
                fallback,
                "model_name",
                "mock",
            ),
        }

    return {
        "requested_provider": resolved_name,
        "resolved_provider": resolved_name,
        "available": available,
        "name": getattr(
            provider,
            "name",
            resolved_name,
        ),
        "model": getattr(
            provider,
            "model_name",
            "unknown",
        ),
    }


# ---------------------------------------------------------------------------
# Provider availability summary
# ---------------------------------------------------------------------------

def list_provider_status() -> list[dict]:
    """
    Return availability information for every registered provider.

    This is useful for diagnostics or a future provider-status panel.

    API keys and credentials are never returned.
    """

    results = []

    for name, provider_cls in _PROVIDERS.items():

        try:
            provider = provider_cls()
            available = _provider_is_available(provider)

            results.append(
                {
                    "name": name,
                    "available": available,
                    "model": getattr(
                        provider,
                        "model_name",
                        "unknown",
                    ),
                }
            )

        except Exception as exc:

            _logger.exception(
                "Failed checking provider '%s': %s",
                name,
                exc,
            )

            results.append(
                {
                    "name": name,
                    "available": False,
                    "model": "unavailable",
                }
            )

    return results


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "AIProvider",
    "MockProvider",
    "GeminiProvider",
    "get_provider",
    "get_provider_info",
    "list_provider_status",
]

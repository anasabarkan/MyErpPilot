"""
Deterministic language detection for assistant responses.
"""

from __future__ import annotations

import re
from typing import Literal

SupportedLanguage = Literal["en", "fr", "ar"]

LANGUAGE_NAMES: dict[SupportedLanguage, str] = {
    "en": "English",
    "fr": "French",
    "ar": "Arabic",
}

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_FRENCH_ACCENT_RE = re.compile(r"[àâçéèêëîïôùûüÿœ]", re.IGNORECASE)
_FRENCH_TERMS = {
    "affiche",
    "annee",
    "année",
    "bonjour",
    "chiffre",
    "clients",
    "commandes",
    "coucou",
    "donne",
    "factures",
    "meilleur",
    "meilleure",
    "meilleurs",
    "mes",
    "produits",
    "quels",
    "quelles",
    "recapitulatif",
    "récapitulatif",
    "resume",
    "résumé",
    "sont",
    "ventes",
}


def detect_supported_language(text: str) -> SupportedLanguage:
    """
    Return the supported assistant language inferred from user text.

    English is intentionally the fallback for short or ambiguous input.
    """

    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return "en"

    if _ARABIC_RE.search(normalized):
        return "ar"

    if _FRENCH_ACCENT_RE.search(normalized):
        return "fr"

    terms = set(re.findall(r"[a-zA-ZÀ-ÿ]+", normalized))
    french_hits = terms.intersection(_FRENCH_TERMS)
    if len(french_hits) >= 2:
        return "fr"

    return "en"


def language_name(language: SupportedLanguage) -> str:
    return LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["en"])

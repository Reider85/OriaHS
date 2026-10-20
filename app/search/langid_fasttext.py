"""FastText language identification for multilingual search.

ARCHITECT §9.3–9.4, PR-12: Многоязычность enhanced.

This module provides language identification:
- FastText-based language detection
- Mixed query token-level handling
- Cyrillic transliteration
- Unicode NFC normalization

NOT IMPLEMENTED: This is a stub for PR-00. Real implementation in PR-12.
"""

from app.search.langid_fasttext import detect_language, normalize_query

__all__ = ["detect_language", "normalize_query"]

"""Text normalization utilities.

Unicode normalization and text processing utilities.
"""

import unicodedata


def normalize_nfc(text: str) -> str:
    """Apply Unicode NFC normalization."""
    return unicodedata.normalize("NFC", text)

"""FastText language identification for multilingual search.

ARCHITECT §9.3–9.4, PR-12: Многоязычность enhanced.

This module provides language identification:
- FastText-based language detection
- Mixed query token-level handling
- Cyrillic transliteration
- Unicode NFC normalization
"""

import asyncio
from pathlib import Path
from typing import Any

import aiofiles
import langdetect
from structlog import get_logger

from app.config import settings
from app.utils.normalization import normalize_nfc

logger = get_logger(__name__)

# Unicode script ranges for token classification
LATIN_RANGES = [
    (0x0041, 0x005A),  # A-Z
    (0x0061, 0x007A),  # a-z
    (0x00C0, 0x00D6),  # À-Ö
    (0x00D8, 0x00F6),  # Ø-ö
    (0x00F8, 0x00FF),  # ø-ÿ
    (0x0100, 0x017F),  # Latin Extended-A
    (0x0180, 0x024F),  # Latin Extended-B
]
CYRILLIC_RANGES = [(0x0400, 0x04FF)]  # Cyrillic
DIGIT_RANGES = [(0x0030, 0x0039)]  # 0-9


def normalize_query(text: str) -> str:
    """Apply NFC normalization and lowercase for search."""
    return normalize_nfc(text).lower()


def detect_token_languages(query: str) -> dict[str, list[str]]:
    """Group tokens by Unicode script: latin, cyrillic, neutral.

    Returns:
        {"latin": [...], "cyrillic": [...], "neutral": [...]}
    """
    tokens = query.split()
    result: dict[str, list[str]] = {"latin": [], "cyrillic": [], "neutral": []}

    for token in tokens:
        has_latin = False
        has_cyrillic = False

        # Check each character in token
        for char in token:
            code = ord(char)

            # Check if character is in any range
            for start, end in LATIN_RANGES:
                if start <= code <= end:
                    has_latin = True
                    break
            if has_latin:
                continue

            for start, end in CYRILLIC_RANGES:
                if start <= code <= end:
                    has_cyrillic = True
                    break
            if has_cyrillic:
                continue

            for start, end in DIGIT_RANGES:
                if start <= code <= end:
                    # Digits go to neutral but also indicate neutral token
                    result["neutral"].append(token)
                    break
            else:
                # Non-alphanumeric character
                result["neutral"].append(token)
                break
        else:
            # No special characters found
            if has_latin and has_cyrillic:
                # Mixed script token
                result["neutral"].append(token)
            elif has_latin:
                result["latin"].append(token)
            elif has_cyrillic:
                result["cyrillic"].append(token)
            else:
                result["neutral"].append(token)

    return result


def translit_variants(text: str) -> list[str]:
    """Generate transliterated variants using cyrtranslit.

    Returns:
        List of transliterated variants (original + transliterations).
    """
    try:
        import cyrtranslit
    except ImportError:
        logger.warning("cyrtranslit not available, transliteration disabled")
        return [text]

    variants = [text]

    # Check if text contains Cyrillic characters
    has_cyrillic = any(
        any(start <= ord(char) <= end for start, end in CYRILLIC_RANGES)
        for char in text
    )

    # Check if text contains Latin characters
    has_latin = any(
        any(start <= ord(char) <= end for start, end in LATIN_RANGES)
        for char in text
    )

    if has_cyrillic:
        try:
            latin_variant = cyrtranslit.to_latin(text)
            if latin_variant != text:
                variants.append(latin_variant)
        except Exception:
            logger.warning("Failed to transliterate Cyrillic to Latin", exc_info=True)

    if has_latin:
        try:
            cyrillic_variant = cyrtranslit.to_cyrillic(text)
            if cyrillic_variant != text:
                variants.append(cyrillic_variant)
        except Exception:
            logger.warning("Failed to transliterate Latin to Cyrillic", exc_info=True)

    return variants


class FastTextLangId:
    """FastText-based language identification with fallback to langdetect."""

    def __init__(self) -> None:
        self._model = None
        self._model_path = None
        self._load_lock = asyncio.Lock()

    def _get_model_path(self) -> Path:
        """Get the path to the FastText model file."""
        if self._model_path:
            return Path(self._model_path)

        # Default cache directory
        cache_dir = Path.home() / ".cache" / "oriahs"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "lid.176.ftz"

    async def _ensure_model(self) -> Any:
        """Load the FastText model if not already loaded."""
        if self._model is not None:
            return self._model

        async with self._load_lock:
            if self._model is not None:
                return self._model

            try:
                import fasttext
            except ImportError:
                logger.warning("fasttext not available, using langdetect fallback")
                raise RuntimeError("fasttext not available") from None

            model_path = self._get_model_path()

            if not model_path.exists():
                if settings.langid.auto_download:
                    await self._download_model(model_path)
                else:
                    raise FileNotFoundError(f"Model not found: {model_path}")

            try:
                # Load model in thread to avoid blocking event loop
                self._model = await asyncio.to_thread(fasttext.load_model, str(model_path))
                logger.info("FastText language model loaded", extra={"model_path": str(model_path)})
            except Exception as e:
                logger.error("Failed to load FastText model", extra={"error": str(e)})
                raise

            return self._model

    async def _download_model(self, model_path: Path) -> None:
        """Download the FastText language model."""
        import aiohttp

        url = "https://dl.fbaipublicfiles.com/fasttext/models/identifiers/lid.176.ftz"

        logger.info("Downloading FastText language model", extra={"url": url})

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    response.raise_for_status()

                    # Create parent directory if needed
                    model_path.parent.mkdir(parents=True, exist_ok=True)

                    # Download file
                    async with aiofiles.open(model_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

                    logger.info("FastText language model downloaded", extra={"model_path": str(model_path)})

        except Exception as e:
            logger.error("Failed to download FastText model", extra={"error": str(e)})
            raise

    async def detect(self, text: str, tenant_default: str | None = None) -> tuple[str, float]:
        """Detect language of text using FastText, with fallback.

        Args:
            text: Text to analyze
            tenant_default: Fallback language if confidence is too low

        Returns:
            Tuple of (language_code, confidence)
        """
        try:
            model = await self._ensure_model()

            # Run inference in thread to avoid blocking
            result = await asyncio.to_thread(model.predict, text, k=1)
            labels, probs = result

            # Extract language code (remove '__label__' prefix)
            lang = labels[0].replace("__label__", "")
            confidence = float(probs[0])

            # Apply confidence threshold
            if confidence < settings.langid.fasttext_confidence_threshold:
                fallback_lang = tenant_default or settings.langid.fallback_language
                logger.debug(
                    "Low confidence language detection",
                    extra={
                        "detected_lang": lang,
                        "confidence": confidence,
                        "threshold": settings.langid.fasttext_confidence_threshold,
                        "fallback_lang": fallback_lang,
                    }
                )
                return fallback_lang, confidence

            return lang, confidence

        except Exception:
            # Fallback to langdetect
            return await self._fallback_detect(text, tenant_default)

    async def _fallback_detect(self, text: str, tenant_default: str | None = None) -> tuple[str, float]:
        """Fallback to langdetect if FastText is unavailable."""
        try:
            # langdetect is synchronous, run in thread
            lang = await asyncio.to_thread(langdetect.detect, text)
            confidence = 0.5  # Default confidence for langdetect
            fallback_lang = tenant_default or settings.langid.fallback_language

            if lang != fallback_lang:
                logger.info(
                    "Using langdetect fallback",
                    extra={"detected_lang": lang, "fallback_lang": fallback_lang}
                )

            return lang, confidence

        except Exception as e:
            # Final fallback
            fallback_lang = tenant_default or settings.langid.fallback_language
            logger.warning(
                "Language detection failed, using fallback",
                extra={"error": str(e), "fallback_lang": fallback_lang}
            )
            return fallback_lang, 0.0


# Global singleton instance
_langid_instance = None


def get_langid() -> FastTextLangId:
    """Get the global FastTextLangId instance."""
    global _langid_instance
    if _langid_instance is None:
        _langid_instance = FastTextLangId()
    return _langid_instance


# Convenience functions for direct import
async def detect_language(text: str, tenant_default: str | None = None) -> tuple[str, float]:
    """Detect language of text."""
    return await get_langid().detect(text, tenant_default)





__all__ = [
    "normalize_nfc",
    "normalize_query",
    "detect_token_languages",
    "translit_variants",
    "FastTextLangId",
    "get_langid",
    "detect_language",
]

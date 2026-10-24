"""Tests for FastText language identification (PR-12).

Tests language detection, token classification, transliteration, and fallback
behaviors without requiring external models or network calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.search.langid_fasttext import (
    FastTextLangId,
    detect_language,
    detect_token_languages,
    get_langid,
    normalize_nfc,
    normalize_query,
    translit_variants,
)


class TestNormalization:
    """Test NFC normalization and query normalization."""

    def test_normalize_nfc(self):
        """Test NFC normalization of Unicode text."""
        # Decomposed é (e + combining acute)
        decomposed = "e\u0301"
        # Composed é (single character)
        composed = "é"

        assert normalize_nfc(decomposed) == composed
        assert normalize_nfc(composed) == composed
        assert normalize_nfc("hello") == "hello"

    def test_normalize_query(self):
        """Test query normalization (NFC + lowercase)."""
        result = normalize_query("Élève 123")
        assert result == "élève 123"

        # Test NFC normalization
        decomposed = "e\u0301lève"
        assert normalize_query(decomposed) == "élève"


class TestTokenLanguages:
    """Test token language classification by script."""

    def test_detect_token_languages_mixed(self):
        """Test detection of mixed language tokens."""
        query = "купить iPhone 15 pro в Москве"
        result = detect_token_languages(query)

        assert "latin" in result
        assert "cyrillic" in result
        assert "neutral" in result

        # Check that tokens are correctly classified
        assert "iPhone" in result["latin"]
        assert "pro" in result["latin"]
        assert "15" in result["neutral"]
        assert "купить" in result["cyrillic"]
        assert "Москве" in result["cyrillic"]

    def test_detect_token_languages_latin_only(self):
        """Test detection of Latin-only tokens."""
        query = "buy iPhone 15 pro"
        result = detect_token_languages(query)

        assert len(result["latin"]) > 0
        assert len(result["cyrillic"]) == 0
        assert "15" in result["neutral"]

    def test_detect_token_languages_cyrillic_only(self):
        """Test detection of Cyrillic-only tokens."""
        query = "купить Айфон в Москве"
        result = detect_token_languages(query)

        assert len(result["cyrillic"]) > 0
        assert len(result["latin"]) == 0
        assert "15" not in result  # No digits in this query

    def test_detect_token_languages_with_symbols(self):
        """Test detection of tokens with symbols."""
        query = "купить iPhone 15pro! @home"
        result = detect_token_languages(query)

        assert "iPhone" in result["latin"]
        assert "купить" in result["cyrillic"]
        assert "15pro!" in result["neutral"]  # Mixed alphanumeric with symbol
        assert "@home" in result["neutral"]  # Symbol with word
        assert "@home" in result["neutral"]  # Symbol with word remains as one token


class TestTransliteration:
    """Test transliteration functionality."""

    @patch("builtins.__import__")
    def test_translit_variants_cyrillic_to_latin(self, mock_import):
        """Test Cyrillic to Latin transliteration."""
        def mock_cyrtranslit(name, *args, **kwargs):
            if name == "cyrtranslit":
                mock_module = MagicMock()
                mock_module.to_latin.side_effect = lambda x: x.replace("айфон", "ayфон")
                return mock_module
            return real_import(name, *args, **kwargs)
        
        real_import = mock_import.side_effect
        mock_import.side_effect = mock_cyrtranslit

        text = "купить айфон"
        variants = translit_variants(text)

        assert text in variants
        assert "купить айфон" in variants  # Original
        assert "купить ayфон" in variants   # Transliterated

    @patch("builtins.__import__")
    def test_translit_variants_latin_to_cyrillic(self, mock_import):
        """Test Latin to Cyrillic transliteration."""
        def mock_cyrtranslit(name, *args, **kwargs):
            if name == "cyrtranslit":
                mock_module = MagicMock()
                mock_module.to_cyrillic.side_effect = lambda x: x.replace("i", "і")
                return mock_module
            return real_import(name, *args, **kwargs)
        
        real_import = mock_import.side_effect
        mock_import.side_effect = mock_cyrtranslit

        text = "buy iphone"
        variants = translit_variants(text)

        assert text in variants
        assert "buy iphone" in variants    # Original
        assert "buy іphone" in variants     # Transliterated

    @patch("builtins.__import__")
    def test_translit_variants_no_cyrtranslit(self, mock_import):
        """Test transliteration without cyrtranslit."""
        def mock_import_with_import_error(name, *args, **kwargs):
            if name == "cyrtranslit":
                raise ImportError("No module named 'cyrtranslit'")
            return real_import(name, *args, **kwargs)
        
        real_import = mock_import.side_effect
        mock_import.side_effect = mock_import_with_import_error
        
        text = "купить айфон"
        variants = translit_variants(text)

        assert variants == [text]  # Only original when cyrtranslit unavailable


class TestFastTextLangId:
    """Test FastText language detection with fallbacks."""

    @pytest.mark.asyncio
    async def test_detect_with_fasttext_model(self):
        """Test language detection with mocked FastText model."""
        langid = FastTextLangId()

        # Mock FastText model
        mock_model = MagicMock()
        mock_model.predict.return_value = (["__label__ru"], [0.85])

        async def mock_ensure_model():
            return mock_model

        with patch.object(langid, '_ensure_model', side_effect=mock_ensure_model):
            lang, confidence = await langid.detect("купить айфон")

            assert lang == "ru"
            assert confidence == 0.85

    @pytest.mark.asyncio
    async def test_detect_with_low_confidence_fallback(self):
        """Test fallback to tenant default language with low confidence."""
        langid = FastTextLangId()

        # Mock FastText model with low confidence
        mock_model = MagicMock()
        mock_model.predict.return_value = (["__label__en"], [0.5])

        async def mock_ensure_model():
            return mock_model

        with patch.object(langid, '_ensure_model', side_effect=mock_ensure_model):
            lang, confidence = await langid.detect("mixed query", tenant_default="fr")

            # Should fall back to tenant default
            assert lang == "fr"
            assert confidence == 0.5

    @pytest.mark.asyncio
    async def test_detect_with_fallback_to_langdetect(self):
        """Test fallback to langdetect when FastText unavailable."""
        langid = FastTextLangId()

        # Mock FastText unavailable
        with patch.object(langid, '_ensure_model', side_effect=RuntimeError("No model")):
            with patch('langdetect.detect', return_value="en") as mock_detect:
                lang, confidence = await langid.detect("hello world")

                assert lang == "en"
                assert confidence == 0.5  # Default langdetect confidence
                mock_detect.assert_called_once_with("hello world")

    @pytest.mark.asyncio
    async def test_detect_with_fallback_failure(self):
        """Test final fallback when all detection methods fail."""
        langid = FastTextLangId()

        # Mock FastText unavailable
        with patch.object(langid, '_ensure_model', side_effect=RuntimeError("No model")):
            with patch('langdetect.detect', side_effect=Exception("Detection failed")):
                lang, confidence = await langid.detect("hello world", tenant_default="es")

                # Should fall back to tenant default
                assert lang == "es"
                assert confidence == 0.0

    def test_get_langid_singleton(self):
        """Test that get_langid returns the same instance."""
        langid1 = get_langid()
        langid2 = get_langid()

        assert langid1 is langid2


class TestLanguageDetectionIntegration:
    """Integration tests for the complete language detection pipeline."""

    @pytest.mark.asyncio
    async def test_detect_language_convenience_function(self):
        """Test the convenience detect_language function."""
        with patch('app.search.langid_fasttext.get_langid') as mock_get_langid:
            mock_langid = AsyncMock()
            mock_langid.detect.return_value = ("ru", 0.85)
            mock_get_langid.return_value = mock_langid

            lang, confidence = await detect_language("купить айфон")

            assert lang == "ru"
            assert confidence == 0.85
            mock_langid.detect.assert_called_once_with("купить айфон", None)

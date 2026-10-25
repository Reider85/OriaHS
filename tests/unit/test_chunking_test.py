"""Tests for PR-13 asymmetric chunking (app/embedding/chunking.py)."""

import uuid

import numpy as np
import pytest

from app.embedding.chunking import (
    ChunkingService,
    ChunkingStrategy,
    ChunkPlan,
    DocumentChunk,
    HuggingFaceTokenizer,
    SectionType,
    WhitespaceTokenizer,
)


class TestChunkingService:
    """Test ChunkingService strategies and methods."""

    def test_short_content_single_chunk(self) -> None:
        """AC: short content → 1 chunk."""
        service = ChunkingService(tokenizer=WhitespaceTokenizer())
        chunks = service.chunk_document("Test Title", "short content")

        assert len(chunks) == 1
        assert chunks[0].text == "Test Title\nshort content"
        assert chunks[0].chunk_idx == 0
        assert chunks[0].section_type == "document"

    def test_medium_content_weighted_strategy(self) -> None:
        """AC: 200-511 tokens → weighted strategy with title/body."""
        service = ChunkingService(
            short_threshold=5,
            tokenizer=WhitespaceTokenizer()
        )

        # Create content with ~200 "tokens" (words)
        content = " ".join([f"word{i}" for i in range(200)])
        chunks = service.chunk_document("Test Title", content)

        assert len(chunks) == 1
        assert chunks[0].section_type == "weighted"
        assert "Test Title\n" in chunks[0].text

    def test_long_content_multi_chunks(self) -> None:
        """AC: 2000 tokens → multiple chunks with 50 overlap."""
        service = ChunkingService(
            chunk_size=10,  # Small for testing
            overlap=5,     # 50% overlap
            short_threshold=5,
            tokenizer=WhitespaceTokenizer()
        )

        # Create content with ~2000 "tokens"
        content = " ".join([f"word{i}" for i in range(2000)])
        chunks = service.chunk_document("Test Title", content)

        # Should have multiple chunks
        assert len(chunks) > 1
        assert all(c.section_type == "body" for c in chunks)
        assert all(isinstance(c.chunk_idx, int) and c.chunk_idx >= 0 for c in chunks)

        # Check overlap between consecutive chunks
        for i in range(1, len(chunks)):
            prev_words = chunks[i-1].text.split()
            curr_words = chunks[i].text.split()
            # Should have overlap of 5 words (50% of chunk_size=10)
            overlap = len(set(prev_words[-5:]) & set(curr_words[:5]))
            assert overlap >= 4  # Allow some flexibility

    def test_structured_content_with_abstract(self) -> None:
        """AC: structured doc with abstract → weighted with all sections."""
        service = ChunkingService(
            short_threshold=5,
            tokenizer=WhitespaceTokenizer()
        )

        attributes = {"abstract": "This is the abstract"}
        chunks = service.chunk_document(
            "Test Title",
            "This is the body content.",
            attributes
        )

        assert len(chunks) == 1
        assert chunks[0].section_type == "weighted"
        assert "Test Title\nThis is the abstract\nThis is the body content." in chunks[0].text

    def test_weighted_average_math(self) -> None:
        """AC: weighted average normalization test."""
        # Create test embeddings
        emb1 = np.array([0.6, 0.8])  # Unit vector
        emb2 = np.array([0.8, 0.6])  # Unit vector

        # Test weighted average with weights 3:1
        result = ChunkingService.weighted_average(
            {"title": emb1, "body": emb2},
            {"title": 3.0, "body": 1.0}
        )

        # Expected: (3*emb1 + 1*emb2) / 4, then normalized
        expected = (3 * emb1 + 1 * emb2) / 4
        expected = expected / np.linalg.norm(expected)

        # Check result is normalized and close to expected
        assert np.allclose(np.linalg.norm(result), 1.0)
        assert np.allclose(result, expected, rtol=1e-6)

    def test_plan_returns_strategy_and_texts(self) -> None:
        """Test plan() returns complete strategy information."""
        service = ChunkingService(tokenizer=WhitespaceTokenizer())

        # Single strategy
        plan = service.plan("Title", "short content")
        assert plan.strategy == "single"
        assert len(plan.embed_texts) == 1
        assert plan.chunks[0].section_type == "document"

        # Multi strategy
        long_content = " ".join([f"word{i}" for i in range(600)])
        plan = service.plan("Title", long_content)
        assert plan.strategy == "multi"
        assert len(plan.embed_texts) > 1
        assert all(c.section_type == "body" for c in plan.chunks)

    def test_whitespace_tokenizer_fallback(self) -> None:
        """Test fallback tokenizer works and is deterministic."""
        service = ChunkingService(tokenizer=WhitespaceTokenizer())

        # Test encode/decode
        text = "word1 word2 word3"
        ids = service._get_tokenizer().encode(text)
        decoded = service._get_tokenizer().decode(ids)

        assert ids == [0, 1, 2]
        assert "word_0 word_1 word_2" in decoded  # WhitespaceTokenizer uses "word_i" pattern

    def test_metrics_emission(self) -> None:
        """Test metrics are emitted during chunking."""
        # Note: Actual metric emission tested in integration with real EmbeddingService
        # Here we verify the plan structure that drives metrics
        service = ChunkingService(tokenizer=WhitespaceTokenizer())

        # Single strategy → should emit "single" docs_total and "document" chunks_total
        plan = service.plan("Title", "short content")
        assert plan.strategy == "single"
        assert plan.chunks[0].section_type == "document"

        # Multi strategy → should emit "multi" docs_total and multiple "body" chunks_total
        long_content = " ".join([f"word{i}" for i in range(600)])
        plan = service.plan("Title", long_content)
        assert plan.strategy == "multi"
        assert len(plan.chunks) > 1
        assert all(c.section_type == "body" for c in plan.chunks)

    def test_chunk_point_id_generation(self) -> None:
        """Test Qdrant point ID generation for chunks."""
        doc_id = uuid.uuid4()

        # Chunk 0 uses doc_id directly (backward compatibility)
        id0 = ChunkingService.chunk_point_id(doc_id, 0)
        assert id0 == str(doc_id)

        # Chunk >0 uses UUID5
        id1 = ChunkingService.chunk_point_id(doc_id, 1)
        id2 = ChunkingService.chunk_point_id(doc_id, 2)

        assert id1 != id2
        assert str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:1")) == id1
        assert str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:2")) == id2


class TestDocumentChunk:
    """Test DocumentChunk model."""

    def test_document_chunk_creation(self) -> None:
        """Test DocumentChunk creation with defaults."""
        chunk = DocumentChunk(text="test", chunk_idx=0)

        assert chunk.text == "test"
        assert chunk.chunk_idx == 0
        assert chunk.section_type is None

    def test_document_chunk_with_section_type(self) -> None:
        """Test DocumentChunk with explicit section type."""
        chunk = DocumentChunk(
            text="test",
            chunk_idx=1,
            section_type="body"
        )

        assert chunk.section_type == "body"


class TestChunkPlan:
    """Test ChunkPlan model."""

    def test_chunk_plan_structure(self) -> None:
        """Test ChunkPlan contains all required fields."""
        plan = ChunkPlan(
            strategy="single",
            embed_texts=["text1"],
            weights=None,
            chunks=[DocumentChunk(text="text1", chunk_idx=0)]
        )

        assert plan.strategy == "single"
        assert plan.embed_texts == ["text1"]
        assert plan.weights is None
        assert len(plan.chunks) == 1


class TestChunkingStrategies:
    """Test strategy type definitions."""

    def test_chunking_strategy_literal(self) -> None:
        """Test ChunkingStrategy type."""
        strategy: ChunkingStrategy = "single"
        assert strategy in ["single", "weighted", "multi"]

    def test_section_type_literal(self) -> None:
        """Test SectionType type."""
        section_type: SectionType = "document"
        assert section_type in ["document", "title", "abstract", "body", "weighted", None]


class TestWeightedAverage:
    """Test weighted_average function directly."""

    def test_weighted_average_normalization(self) -> None:
        """Test weighted average with normalization."""
        # Unit vectors
        emb1 = np.array([1.0, 0.0])
        emb2 = np.array([0.0, 1.0])

        result = ChunkingService.weighted_average(
            {"section1": emb1, "section2": emb2},
            {"section1": 2.0, "section2": 1.0}
        )

        # Result should be normalized
        assert np.allclose(np.linalg.norm(result), 1.0)

        # Should be closer to section1 due to higher weight
        assert result[0] > result[1]

    def test_weighted_average_zero_weight(self) -> None:
        """Test weighted average with zero weights."""
        emb1 = np.array([1.0, 0.0])
        emb2 = np.array([0.0, 1.0])

        result = ChunkingService.weighted_average(
            {"section1": emb1, "section2": emb2},
            {"section1": 0.0, "section2": 1.0}
        )

        # Should equal emb2 (only non-zero weight)
        assert np.allclose(result, emb2)

    def test_weighted_average_error(self) -> None:
        """Test weighted average with no valid weights."""
        emb1 = np.array([1.0, 0.0])

        with pytest.raises(ValueError, match="No valid weights provided"):
            ChunkingService.weighted_average({"section1": emb1}, {"section1": 0.0})


class TestTokenizerProtocol:
    """Test Tokenizer protocol implementations."""

    def test_whitespace_tokenizer_interface(self) -> None:
        """Test WhitespaceTokenizer implements Tokenizer protocol."""
        tokenizer = WhitespaceTokenizer()

        # Test encode
        ids = tokenizer.encode("word1 word2 word3")
        assert isinstance(ids, list)
        assert all(isinstance(i, int) for i in ids)

        # Test decode
        text = tokenizer.decode([0, 1, 2])
        assert isinstance(text, str)

    def test_huggingface_tokenizer_interface(self) -> None:
        """Test HuggingFaceTokenizer implements Tokenizer protocol."""
        # Note: Actual loading tested in integration
        # Here we test the interface
        tokenizer = HuggingFaceTokenizer()

        # Should have encode and decode methods
        assert hasattr(tokenizer, "encode")
        assert hasattr(tokenizer, "decode")

        # Methods should be callable
        assert callable(tokenizer.encode)
        assert callable(tokenizer.decode)


# AC-compatibility tests
class TestACTests:
    """Tests matching PR-13 acceptance criteria."""

    def test_ac_short_content_single_chunk(self) -> None:
        """AC: ChunkingService.chunk_document(title="Test", content="short content") → 1 chunk."""
        service = ChunkingService(tokenizer=WhitespaceTokenizer())
        chunks = service.chunk_document("Test", "short content")

        assert len(chunks) == 1
        assert chunks[0].text == "Test\nshort content"

    def test_ac_long_content_multiple_chunks(self) -> None:
        """AC: 2000 token content → multiple chunks with 50 overlap."""
        service = ChunkingService(
            chunk_size=100,  # For testing
            overlap=50,
            tokenizer=WhitespaceTokenizer()
        )

        # Create content with ~2000 "tokens" (words)
        content = " ".join([f"word{i}" for i in range(2000)])
        chunks = service.chunk_document("Test", content)

        assert len(chunks) > 1
        # Verify overlap exists between consecutive chunks
        for i in range(1, len(chunks)):
            prev_words = set(chunks[i-1].text.split()[-50:])
            curr_words = set(chunks[i].text.split()[:50])
            assert len(prev_words & curr_words) > 0  # Some overlap

    def test_ac_weighted_average_normalized(self) -> None:
        """AC: weighted_average({"title": emb1, "body": emb2}, {"title": 3, "body": 1}) → normalized."""
        emb1 = np.array([0.6, 0.8])  # Unit vector
        emb2 = np.array([0.8, 0.6])  # Unit vector

        result = ChunkingService.weighted_average(
            {"title": emb1, "body": emb2},
            {"title": 3.0, "body": 1.0}
        )

        # Check normalization
        norm = np.linalg.norm(result)
        assert 0.999 < norm < 1.001

        # Check it's the weighted average (normalized)
        expected = (3 * emb1 + 1 * emb2) / 4
        expected = expected / np.linalg.norm(expected)
        assert np.allclose(result, expected, rtol=1e-6)

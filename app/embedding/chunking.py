"""Asymmetric chunking for document embedding.

ARCHITECT §5.6, PR-13: Асимметричное индексирование.

This module provides asymmetric chunking:
- 512 token chunks with 50% overlap
- Weighted section aggregation (title × 3, abstract × 2, body × 1)
- Max score aggregation by document
- Enhanced embedding quality
"""

import logging
import uuid
from typing import Any, Literal, Protocol

import numpy as np
from pydantic import BaseModel

logger = logging.getLogger(__name__)

NAMESPACE_URL = uuid.NAMESPACE_URL

# Chunk strategy and section types
ChunkingStrategy = Literal["single", "weighted", "multi"]
SectionType = Literal["document", "title", "abstract", "body", "weighted", None]


class Tokenizer(Protocol):
    """Protocol for text tokenization and detokenization."""

    def encode(self, text: str) -> list[int]:
        """Encode text to token IDs."""
        ...

    def decode(self, ids: list[int]) -> str:
        """Decode token IDs back to text."""
        ...


class HuggingFaceTokenizer:
    """Lazy-loaded HuggingFace tokenizer for bge-m3."""

    _instance = None

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self.model_name = model_name
        self._tokenizer = None

    def _load(self):
        """Load tokenizer on first use."""
        if self._tokenizer is None:
            try:
                from transformers import AutoTokenizer
                self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, local_files_only=False)
                logger.info("Loaded HuggingFace tokenizer for chunking", {"model": self.model_name})
            except Exception as e:
                logger.warning("Failed to load HuggingFace tokenizer, falling back to whitespace", {"error": str(e)})
                raise

    def encode(self, text: str) -> list[int]:
        """Encode text to token IDs."""
        self._load()
        return self._tokenizer.encode(text, add_special_tokens=False)

    def decode(self, ids: list[int]) -> str:
        """Decode token IDs back to text."""
        self._load()
        return self._tokenizer.decode(ids, skip_special_tokens=True)


class WhitespaceTokenizer:
    """Whitespace-based tokenizer for fallback and tests."""

    def encode(self, text: str) -> list[int]:
        """Encode text to token IDs (word indices)."""
        return list(range(len(text.split())))

    def decode(self, ids: list[int]) -> str:
        """Decode token IDs back to text (join words)."""
        words = [f"word_{i}" for i in ids]
        return " ".join(words)


class DocumentChunk(BaseModel):
    """A chunk of document content for embedding."""

    text: str
    chunk_idx: int
    section_type: SectionType = None


class ChunkPlan(BaseModel):
    """Plan for embedding and indexing a document."""

    strategy: ChunkingStrategy
    embed_texts: list[str]  # Texts to embed (batch)
    weights: list[float] | None  # Per-text weights for weighted strategy
    chunks: list[DocumentChunk]  # Resulting Qdrant points


class ChunkingService:
    """Service for asymmetric document chunking strategies."""

    def __init__(
        self,
        chunk_size: int = 512,
        overlap: int = 50,
        short_threshold: int = 200,
        abstract_key: str = "abstract",
        tokenizer: Tokenizer | None = None,
    ):
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.short_threshold = short_threshold
        self.abstract_key = abstract_key
        self._tokenizer = tokenizer
        self._default_tokenizer = None

    def _get_tokenizer(self) -> Tokenizer:
        """Get or create default tokenizer."""
        if self._tokenizer is not None:
            return self._tokenizer

        if self._default_tokenizer is None:
            try:
                self._default_tokenizer = HuggingFaceTokenizer()
            except Exception:
                logger.warning("Using whitespace tokenizer for chunking (fallback)")
                self._default_tokenizer = WhitespaceTokenizer()

        return self._default_tokenizer

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text using configured tokenizer."""
        return len(self._get_tokenizer().encode(text))

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into overlapping chunks of chunk_size tokens."""
        tokenizer = self._get_tokenizer()
        ids = tokenizer.encode(text)
        chunks = []

        start = 0
        step = self.chunk_size - self.overlap

        while start < len(ids):
            end = min(start + self.chunk_size, len(ids))
            chunk_ids = ids[start:end]
            chunk_text = tokenizer.decode(chunk_ids)
            chunks.append(chunk_text)

            # Move to next chunk position
            start = start + step

            if start >= len(ids):
                break

        return chunks

    def plan(self, title: str, content: str, attributes: dict[str, Any] | None = None) -> ChunkPlan:
        """Plan chunking strategy and embeddings for a document.

        Args:
            title: Document title
            content: Document content
            attributes: Document attributes (may contain abstract)

        Returns:
            ChunkPlan with strategy, texts to embed, weights, and chunks
        """
        attributes = attributes or {}
        abstract = attributes.get(self.abstract_key)

        # Count tokens in content only (per spec)
        content_tokens = self._count_tokens(content)

        # Determine strategy
        if content_tokens >= self.chunk_size:
            # Multi-chunk: split content into overlapping chunks
            chunk_texts = self._chunk_text(content)
            weights = None  # No aggregation needed

            chunks = [
                DocumentChunk(
                    text=chunk_text,
                    chunk_idx=i,
                    section_type="body"
                )
                for i, chunk_text in enumerate(chunk_texts)
            ]

            return ChunkPlan(
                strategy="multi",
                embed_texts=chunk_texts,
                weights=weights,
                chunks=chunks
            )

        elif abstract is not None:
            # Weighted: title + abstract + body
            embed_texts = [title, abstract, content]
            weights = [3.0, 2.0, 1.0]  # title ×3, abstract ×2, body ×1

            chunks = [
                DocumentChunk(
                    text=title + "\n" + abstract + "\n" + content,
                    chunk_idx=0,
                    section_type="weighted"
                )
            ]

            return ChunkPlan(
                strategy="weighted",
                embed_texts=embed_texts,
                weights=weights,
                chunks=chunks
            )

        elif content_tokens >= self.short_threshold:
            # Weighted: title + body only
            embed_texts = [title, content]
            weights = [3.0, 1.0]  # title ×3, body ×1

            chunks = [
                DocumentChunk(
                    text=title + "\n" + content,
                    chunk_idx=0,
                    section_type="weighted"
                )
            ]

            return ChunkPlan(
                strategy="weighted",
                embed_texts=embed_texts,
                weights=weights,
                chunks=chunks
            )

        else:
            # Single: title + content as one chunk
            embed_texts = [title + "\n" + content]
            weights = None

            chunks = [
                DocumentChunk(
                    text=title + "\n" + content,
                    chunk_idx=0,
                    section_type="document"
                )
            ]

            return ChunkPlan(
                strategy="single",
                embed_texts=embed_texts,
                weights=weights,
                chunks=chunks
            )

    def chunk_document(self, title: str, content: str, attributes: dict[str, Any] | None = None) -> list[DocumentChunk]:
        """Chunk a document for embedding (AC-compatible interface).

        Returns:
            List of DocumentChunk objects for Qdrant points
        """
        plan = self.plan(title, content, attributes)
        return plan.chunks

    @classmethod
    def weighted_average(cls, section_embeddings: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
        """Compute weighted average of section embeddings, then normalize.

        Args:
            section_embeddings: Mapping of section name to embedding vector
            weights: Mapping of section name to weight

        Returns:
            Normalized weighted average embedding
        """
        # Compute weighted sum
        weighted_sum = np.zeros_like(list(section_embeddings.values())[0])
        total_weight = 0.0

        for section, embedding in section_embeddings.items():
            if section in weights:
                weight = weights[section]
                weighted_sum += weight * embedding
                total_weight += weight

        if total_weight == 0:
            raise ValueError("No valid weights provided")

        # Normalize by total weight
        weighted_avg = weighted_sum / total_weight

        # L2 normalize (consistent with embedding service output)
        norm = np.linalg.norm(weighted_avg)
        if norm > 0:
            weighted_avg = weighted_avg / norm

        return weighted_avg

    @staticmethod
    def chunk_point_id(doc_id: uuid.UUID, chunk_idx: int) -> str:
        """Generate Qdrant point ID for a chunk.

        Args:
            doc_id: Document UUID
            chunk_idx: Chunk index (0 for single chunk)

        Returns:
            Qdrant point ID (string UUID)
        """
        if chunk_idx == 0:
            return str(doc_id)  # Backward compatibility for chunk 0
        else:
            return str(uuid.uuid5(NAMESPACE_URL, f"{doc_id}:{chunk_idx}"))


__all__ = [
    "ChunkingService",
    "DocumentChunk",
    "ChunkPlan",
    "ChunkingStrategy",
    "SectionType",
    "Tokenizer",
    "HuggingFaceTokenizer",
    "WhitespaceTokenizer",
]

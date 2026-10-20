"""Asymmetric chunking for document embedding.

ARCHITECT §5.6, PR-13: Асимметричное индексирование.

This module provides asymmetric chunking:
- 512 token chunks with 50% overlap
- Weighted section aggregation (title × 3, abstract × 2, body × 1)
- Max score aggregation by document
- Enhanced embedding quality

NOT IMPLEMENTED: This is a stub for PR-00. Real implementation in PR-13.
"""

from app.embedding.chunking import aggregate_chunks, chunk_document

__all__ = ["chunk_document", "aggregate_chunks"]

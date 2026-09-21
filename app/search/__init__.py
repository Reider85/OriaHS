"""Search subsystem.

P-04 lays the Qdrant client + payload schema, P-09 the lexical channel.
P-10/P-11 add vector search and RRF fusion.
"""

from app.search.filters import SearchFilters
from app.search.lexical import LexicalHit, lexical_search

__all__ = ["SearchFilters", "LexicalHit", "lexical_search"]

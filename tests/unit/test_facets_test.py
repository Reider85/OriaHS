"""C-06: Facets computation unit tests (ARCHITECT §8.4).

Pure-function tests for facet aggregation on top-N search results.
"""

from uuid import uuid4

import pytest

from app.api.schemas import FacetBucket
from app.search.facets import compute_facets


class TestFacetBucket:
    """FacetBucket model validation."""

    def test_facet_bucket_creation(self):
        bucket = FacetBucket(value="news", count=5)
        assert bucket.value == "news"
        assert bucket.count == 5


class TestComputeFacets:
    """compute_facets function tests."""

    @pytest.mark.asyncio
    async def test_empty_doc_ids_returns_empty(self):
        """Empty doc_ids list should return empty dict."""
        from unittest.mock import AsyncMock

        session = AsyncMock()
        result = await compute_facets([], session, ["tags"], 20)
        assert result == {}

    @pytest.mark.asyncio
    async def test_none_facet_fields_returns_empty(self):
        """None facet_fields should return empty dict."""
        from unittest.mock import AsyncMock

        session = AsyncMock()
        result = await compute_facets([uuid4()], session, None, 20)
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_facet_fields_returns_empty(self):
        """Empty facet_fields list should return empty dict."""
        from unittest.mock import AsyncMock

        session = AsyncMock()
        result = await compute_facets([uuid4()], session, [], 20)
        assert result == {}

    @pytest.mark.asyncio
    async def test_tags_facet_computation(self):
        """Test tag facet aggregation."""
        from unittest.mock import AsyncMock, MagicMock

        # Mock session with tag results
        session = AsyncMock()
        session.execute = AsyncMock()

        # Mock query result: 3 tags with counts
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            type("Row", (), {"tag": "python", "count": 5})(),
            type("Row", (), {"tag": "javascript", "count": 3})(),
            type("Row", (), {"tag": "database", "count": 1})(),
        ]
        session.execute.return_value = mock_result

        doc_ids = [uuid4(), uuid4(), uuid4()]
        result = await compute_facets(doc_ids, session, ["tags"], 20)

        assert "tags" in result
        assert len(result["tags"]) == 3

        # Check ordering by count (descending)
        assert result["tags"][0].value == "python"
        assert result["tags"][0].count == 5
        assert result["tags"][1].value == "javascript"
        assert result["tags"][1].count == 3
        assert result["tags"][2].value == "database"
        assert result["tags"][2].count == 1

    @pytest.mark.asyncio
    async def test_category_facet_computation(self):
        """Test category facet aggregation."""
        from unittest.mock import AsyncMock, MagicMock

        # Mock session with category results
        session = AsyncMock()
        session.execute = AsyncMock()

        # Mock query result: 3 categories with counts
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            type("Row", (), {"category": "tech", "count": 8})(),
            type("Row", (), {"category": "business", "count": 4})(),
            type("Row", (), {"category": "science", "count": 2})(),
        ]
        session.execute.return_value = mock_result

        doc_ids = [uuid4(), uuid4(), uuid4()]
        result = await compute_facets(doc_ids, session, ["attributes.category"], 20)

        assert "attributes.category" in result
        assert len(result["attributes.category"]) == 3

        # Check ordering by count (descending)
        assert result["attributes.category"][0].value == "tech"
        assert result["attributes.category"][0].count == 8
        assert result["attributes.category"][1].value == "business"
        assert result["attributes.category"][1].count == 4
        assert result["attributes.category"][2].value == "science"
        assert result["attributes.category"][2].count == 2

    @pytest.mark.asyncio
    async def test_multiple_facet_fields(self):
        """Test computation of multiple facet fields."""
        from unittest.mock import AsyncMock, MagicMock

        # Mock session
        session = AsyncMock()
        session.execute = AsyncMock()

        # Mock tag results
        tag_result = MagicMock()
        tag_result.fetchall.return_value = [
            type("Row", (), {"tag": "python", "count": 5})(),
        ]

        # Mock category results
        category_result = MagicMock()
        category_result.fetchall.return_value = [
            type("Row", (), {"category": "tech", "count": 3})(),
        ]

        # Configure execute to return different results based on query
        def mock_execute(stmt):
            if "unnest" in str(stmt):
                return tag_result
            elif "->>" in str(stmt):
                return category_result
            return MagicMock()

        session.execute.side_effect = mock_execute

        doc_ids = [uuid4()]
        result = await compute_facets(doc_ids, session, ["tags", "attributes.category"], 20)

        assert "tags" in result
        assert "attributes.category" in result
        assert len(result["tags"]) == 1
        assert len(result["attributes.category"]) == 1
        assert result["tags"][0].value == "python"
        assert result["tags"][0].count == 5
        assert result["attributes.category"][0].value == "tech"
        assert result["attributes.category"][0].count == 3

    @pytest.mark.asyncio
    async def test_top_n_limiting(self):
        """Test top_n parameter limits results."""
        from unittest.mock import AsyncMock, MagicMock

        # Mock session with 7 tags but limit to 3
        session = AsyncMock()
        session.execute = AsyncMock()

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            type("Row", (), {"tag": f"tag{i}", "count": 10 - i})()
            for i in range(7)  # 7 tags
        ]
        session.execute.return_value = mock_result

        doc_ids = [uuid4(), uuid4()]
        result = await compute_facets(doc_ids, session, ["tags"], 3)  # limit to 3

        assert len(result["tags"]) == 3
        # Should return top 3 by count
        assert result["tags"][0].value == "tag0"
        assert result["tags"][0].count == 10
        assert result["tags"][1].value == "tag1"
        assert result["tags"][1].count == 9
        assert result["tags"][2].value == "tag2"
        assert result["tags"][2].count == 8

    @pytest.mark.asyncio
    async def test_unsupported_field_ignored(self):
        """Test unsupported facet fields are ignored."""
        from unittest.mock import AsyncMock, MagicMock

        session = AsyncMock()
        session.execute = AsyncMock()

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            type("Row", (), {"tag": "python", "count": 2})(),
        ]
        session.execute.return_value = mock_result

        doc_ids = [uuid4()]
        result = await compute_facets(doc_ids, session, ["tags", "unsupported.field"], 20)

        # Should only return supported fields
        assert "tags" in result
        assert "unsupported.field" not in result

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        """Test that exceptions during facet computation are caught and logged."""
        from unittest.mock import AsyncMock

        # Mock session that raises exception
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=Exception("Database error"))

        doc_ids = [uuid4()]
        result = await compute_facets(doc_ids, session, ["tags"], 20)

        # Should return empty dict instead of raising exception
        assert result == {}

    @pytest.mark.asyncio
    async def test_no_results_for_field(self):
        """Test fields with no results return empty list for that field."""
        from unittest.mock import AsyncMock, MagicMock

        # Mock session returning no results for tags
        session = AsyncMock()
        session.execute = AsyncMock()

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []  # No tags found
        session.execute.return_value = mock_result

        doc_ids = [uuid4()]
        result = await compute_facets(doc_ids, session, ["tags"], 20)

        assert "tags" in result
        assert len(result["tags"]) == 0


class TestEdgeCases:
    """Edge case tests for facet computation."""

    @pytest.mark.asyncio
    async def test_single_document_with_multiple_tags(self):
        """Test single document with multiple tags."""
        from unittest.mock import AsyncMock, MagicMock

        session = AsyncMock()
        session.execute = AsyncMock()

        # Mock result: single document with 3 tags
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            type("Row", (), {"tag": "python", "count": 1})(),
            type("Row", (), {"tag": "web", "count": 1})(),
            type("Row", (), {"tag": "backend", "count": 1})(),
        ]
        session.execute.return_value = mock_result

        doc_ids = [uuid4()]
        result = await compute_facets(doc_ids, session, ["tags"], 20)

        assert len(result["tags"]) == 3
        # Each tag should have count 1 (from single document)
        for bucket in result["tags"]:
            assert bucket.count == 1

    @pytest.mark.asyncio
    async def test_duplicate_tags_across_documents(self):
        """Test duplicate tags across multiple documents."""
        from unittest.mock import AsyncMock, MagicMock

        session = AsyncMock()
        session.execute = AsyncMock()

        # Mock result: "python" appears in 3 documents, "javascript" in 2
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            type("Row", (), {"tag": "python", "count": 3})(),
            type("Row", (), {"tag": "javascript", "count": 2})(),
            type("Row", (), {"tag": "database", "count": 1})(),
        ]
        session.execute.return_value = mock_result

        doc_ids = [uuid4(), uuid4(), uuid4(), uuid4()]  # 4 documents
        result = await compute_facets(doc_ids, session, ["tags"], 20)

        assert result["tags"][0].value == "python"
        assert result["tags"][0].count == 3
        assert result["tags"][1].value == "javascript"
        assert result["tags"][1].count == 2

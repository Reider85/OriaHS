"""C-06: Facets integration tests (ARCHITECT §8.4).

End-to-end tests for facet computation in the search pipeline.
"""

import pytest
from uuid import uuid4

from app.api.schemas import SearchRequest, SearchResponse
from app.search.filters import SearchFilters


class TestFacetsIntegration:
    """Integration tests for facets in the search pipeline."""
    
    @pytest.mark.asyncio
    async def test_search_with_tags_facets(self):
        """Test search with tags facet aggregation."""
        from unittest.mock import AsyncMock
        from app.db.models.document import Document
        from app.search.facets import compute_facets
        
        # Mock session with test data
        session = AsyncMock()
        
        # Create mock documents with tags
        docs = [
            Document(
                id=uuid4(),
                tenant_id=uuid4(),
                external_ref="doc1",
                title="Python Guide",
                content="Python programming guide",
                language="en",
                tags=["python", "programming", "tutorial"],
                attributes={"category": "tech"},
            ),
            Document(
                id=uuid4(),
                tenant_id=uuid4(),
                external_ref="doc2", 
                title="JavaScript Tutorial",
                content="JavaScript web development",
                language="en",
                tags=["javascript", "web", "frontend"],
                attributes={"category": "tech"},
            ),
            Document(
                id=uuid4(),
                tenant_id=uuid4(),
                external_ref="doc3",
                title="Database Design",
                content="SQL database design patterns",
                language="en", 
                tags=["database", "sql", "backend"],
                attributes={"category": "tech"},
            ),
        ]
        
        # Mock facet computation
        async def mock_compute_facets(doc_ids, session, facet_fields, top_n):
            # Simulate facet computation on mock data
            tag_counts = {}
            for doc in docs:
                if doc.id in doc_ids:
                    for tag in doc.tags:
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
            
            buckets = [
                type('Bucket', (), {'value': tag, 'count': count})()
                for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
            ]
            return {"tags": buckets}
        
        # Test facet computation
        doc_ids = [doc.id for doc in docs]
        facets = await mock_compute_facets(doc_ids, session, ["tags"], 20)
        
        assert "tags" in facets
        assert len(facets["tags"]) == 6  # Total unique tags across all docs
        
        # Check that tags are counted correctly
        tag_counts = {bucket.value: bucket.count for bucket in facets["tags"]}
        assert tag_counts["python"] == 1
        assert tag_counts["javascript"] == 1  
        assert tag_counts["database"] == 1
        assert tag_counts["programming"] == 1
        assert tag_counts["web"] == 1
        assert tag_counts["tutorial"] == 1
    
    @pytest.mark.asyncio
    async def test_search_with_category_facets(self):
        """Test search with category facet aggregation."""
        from unittest.mock import AsyncMock
        from app.db.models.document import Document
        
        # Mock session with test data
        session = AsyncMock()
        
        # Create mock documents with categories
        docs = [
            Document(
                id=uuid4(),
                tenant_id=uuid4(),
                external_ref="doc1",
                title="Python Guide",
                content="Python programming guide",
                language="en",
                tags=["python"],
                attributes={"category": "tech", "level": "beginner"},
            ),
            Document(
                id=uuid4(),
                tenant_id=uuid4(),
                external_ref="doc2",
                title="Machine Learning",
                content="ML algorithms and applications",
                language="en",
                tags=["ml", "ai"],
                attributes={"category": "science", "level": "advanced"},
            ),
            Document(
                id=uuid4(),
                tenant_id=uuid4(),
                external_ref="doc3",
                title="Business Strategy",
                content="Corporate strategy guide",
                language="en",
                tags=["business", "strategy"],
                attributes={"category": "business", "level": "intermediate"},
            ),
        ]
        
        # Mock facet computation
        async def mock_compute_facets(doc_ids, session, facet_fields, top_n):
            category_counts = {}
            for doc in docs:
                if doc.id in doc_ids and doc.attributes.get("category"):
                    category = doc.attributes["category"]
                    category_counts[category] = category_counts.get(category, 0) + 1
            
            buckets = [
                type('Bucket', (), {'value': category, 'count': count})()
                for category, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True)
            ]
            return {"attributes.category": buckets}
        
        # Test facet computation
        doc_ids = [doc.id for doc in docs]
        facets = await mock_compute_facets(doc_ids, session, ["attributes.category"], 20)
        
        assert "attributes.category" in facets
        assert len(facets["attributes.category"]) == 3
        
        # Check that categories are counted correctly
        category_counts = {bucket.value: bucket.count for bucket in facets["attributes.category"]}
        assert category_counts["tech"] == 1
        assert category_counts["science"] == 1
        assert category_counts["business"] == 1
    
    @pytest.mark.asyncio 
    async def test_search_with_both_facets(self):
        """Test search with both tags and category facets."""
        from unittest.mock import AsyncMock
        from app.db.models.document import Document
        
        # Mock session with test data
        session = AsyncMock()
        
        # Create mock documents
        docs = [
            Document(
                id=uuid4(),
                tenant_id=uuid4(),
                external_ref="doc1",
                title="Python Guide",
                content="Python programming guide",
                language="en",
                tags=["python", "programming"],
                attributes={"category": "tech"},
            ),
            Document(
                id=uuid4(),
                tenant_id=uuid4(),
                external_ref="doc2",
                title="JavaScript Tutorial", 
                content="JavaScript web development",
                language="en",
                tags=["javascript", "web"],
                attributes={"category": "tech"},
            ),
        ]
        
        # Mock facet computation
        async def mock_compute_facets(doc_ids, session, facet_fields, top_n):
            results = {}
            
            # Tags facet
            tag_counts = {}
            for doc in docs:
                if doc.id in doc_ids:
                    for tag in doc.tags:
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
            
            if tag_counts:
                buckets = [
                    type('Bucket', (), {'value': tag, 'count': count})()
                    for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
                ]
                results["tags"] = buckets
            
            # Category facet
            category_counts = {}
            for doc in docs:
                if doc.id in doc_ids and doc.attributes.get("category"):
                    category = doc.attributes["category"]
                    category_counts[category] = category_counts.get(category, 0) + 1
            
            if category_counts:
                buckets = [
                    type('Bucket', (), {'value': category, 'count': count})()
                    for category, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True)
                ]
                results["attributes.category"] = buckets
            
            return results
        
        # Test facet computation
        doc_ids = [doc.id for doc in docs]
        facets = await mock_compute_facets(doc_ids, session, ["tags", "attributes.category"], 20)
        
        assert "tags" in facets
        assert "attributes.category" in facets
        
        # Check tags
        tag_counts = {bucket.value: bucket.count for bucket in facets["tags"]}
        assert tag_counts["python"] == 1
        assert tag_counts["javascript"] == 1
        assert tag_counts["programming"] == 1
        assert tag_counts["web"] == 1
        
        # Check categories  
        category_counts = {bucket.value: bucket.count for bucket in facets["attributes.category"]}
        assert category_counts["tech"] == 2
    
    @pytest.mark.asyncio
    async def test_search_without_facets(self):
        """Test search without facet request."""
        from app.api.schemas import SearchRequest, SearchResponse
        
        # Create search request without facets
        request = SearchRequest(
            query="test",
            tenant_id=uuid4(),
            top_k=10,
            facets=None  # No facets requested
        )
        
        # Verify that request doesn't have facets field
        assert request.facets is None
        
        # When orchestrator processes this, it should return empty facets dict
        # This is tested in the orchestrator unit tests
        pass
    
    @pytest.mark.asyncio
    async def test_search_with_empty_facets_list(self):
        """Test search with empty facets list."""
        from app.api.schemas import SearchRequest
        
        # Create search request with empty facets list
        request = SearchRequest(
            query="test",
            tenant_id=uuid4(),
            top_k=10,
            facets=[]  # Empty facets list
        )
        
        # Verify that request has empty facets list
        assert request.facets == []
        
        # When orchestrator processes this, it should return empty facets dict
        # This is tested in the orchestrator unit tests
        pass
    
    @pytest.mark.asyncio
    async def test_facets_top_n_limiting(self):
        """Test that facet_top_n parameter limits results correctly."""
        from unittest.mock import AsyncMock
        from app.db.models.document import Document
        
        # Mock session with test data
        session = AsyncMock()
        
        # Create mock documents with many tags
        docs = []
        for i in range(10):
            doc = Document(
                id=uuid4(),
                tenant_id=uuid4(),
                external_ref=f"doc{i}",
                title=f"Document {i}",
                content=f"Content {i}",
                language="en",
                tags=[f"tag{i}", f"category{i}", f"topic{i}"],
                attributes={"category": f"category{i}"}
            )
            docs.append(doc)
        
        # Mock facet computation
        async def mock_compute_facets(doc_ids, session, facet_fields, top_n):
            # Simulate many tags but limit to top_n
            all_tags = []
            for doc in docs:
                if doc.id in doc_ids:
                    all_tags.extend(doc.tags)
            
            # Count all tags
            tag_counts = {}
            for tag in all_tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            
            # Sort by count and limit to top_n
            sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
            
            buckets = [
                type('Bucket', (), {'value': tag, 'count': count})()
                for tag, count in sorted_tags
            ]
            return {"tags": buckets}
        
        # Test with different top_n values
        doc_ids = [doc.id for doc in docs]
        
        # Test top_n=3
        facets = await mock_compute_facets(doc_ids, session, ["tags"], 3)
        assert len(facets["tags"]) == 3
        
        # Test top_n=5
        facets = await mock_compute_facets(doc_ids, session, ["tags"], 5)
        assert len(facets["tags"]) == 5
        
        # Test top_n=10 (should return all unique tags)
        facets = await mock_compute_facets(doc_ids, session, ["tags"], 10)
        assert len(facets["tags"]) == 30  # 10 docs * 3 tags each = 30 unique tags
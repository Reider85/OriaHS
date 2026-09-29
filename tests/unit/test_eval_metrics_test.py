"""Unit tests for evaluation metrics (C-07)."""

import pytest
from app.eval.metrics import recall_at_10, ndcg_at_10, mrr


class TestRecallAt10:
    """Test Recall@10 calculation."""
    
    def test_perfect_recall(self):
        """Test when all relevant docs are retrieved."""
        retrieved = ["doc1", "doc2", "doc3", "doc4", "doc5", "doc6", "doc7", "doc8", "doc9", "doc10"]
        relevant = {"doc1", "doc2", "doc3", "doc4", "doc5"}
        
        result = recall_at_10(retrieved, relevant)
        assert result == 1.0
    
    def test_partial_recall(self):
        """Test when some relevant docs are retrieved."""
        retrieved = ["doc1", "doc2", "doc3", "doc6", "doc7", "doc8", "doc9", "doc10", "doc11", "doc12"]
        relevant = {"doc1", "doc2", "doc3", "doc4", "doc5"}
        
        result = recall_at_10(retrieved, relevant)
        assert result == 0.6  # 3 out of 5 relevant docs
    
    def test_no_recall(self):
        """Test when no relevant docs are retrieved."""
        retrieved = ["doc6", "doc7", "doc8", "doc9", "doc10", "doc11", "doc12", "doc13", "doc14", "doc15"]
        relevant = {"doc1", "doc2", "doc3", "doc4", "doc5"}
        
        result = recall_at_10(retrieved, relevant)
        assert result == 0.0
    
    def test_empty_relevant(self):
        """Test edge case with empty relevant set."""
        retrieved = ["doc1", "doc2", "doc3"]
        relevant = set()
        
        result = recall_at_10(retrieved, relevant)
        assert result == 0.0
    
    def test_top_10_only(self):
        """Test that only top-10 are considered."""
        retrieved = ["doc1", "doc2", "doc3", "doc4", "doc5", "doc6", "doc7", "doc8", "doc9", "doc10", "doc11"]
        relevant = {"doc1", "doc2", "doc3", "doc11"}  # doc11 is relevant but beyond top-10
        
        result = recall_at_10(retrieved, relevant)
        assert result == 0.75  # 3 out of 4 relevant docs in top-10


class TestNdcgAt10:
    """Test nDCG@10 calculation."""
    
    def test_perfect_ndcg(self):
        """Test when all relevant docs are retrieved in perfect order."""
        retrieved = ["doc1", "doc2", "doc3", "doc4", "doc5"]
        relevant = {"doc1", "doc2", "doc3", "doc4", "doc5"}
        
        result = ndcg_at_10(retrieved, relevant)
        assert result == 1.0
    
    def test_imperfect_ndcg(self):
        """Test when relevant docs are retrieved in suboptimal order."""
        retrieved = ["doc3", "doc1", "doc2", "doc6", "doc7", "doc8", "doc9", "doc10", "doc11", "doc12"]
        relevant = {"doc1", "doc2", "doc3"}
        
        # DCG = 1/log2(1) + 1/log2(2) + 1/log2(3) = 1 + 0.5 + 0.333 = 1.833
        # IDCG = 1/log2(1) + 1/log2(2) + 1/log2(3) = 1.833
        # nDCG = 1.833 / 1.833 = 1.0 (order doesn't matter for binary relevance)
        result = ndcg_at_10(retrieved, relevant)
        assert result == pytest.approx(1.0, rel=1e-3)
    
    def test_no_ndcg(self):
        """Test when no relevant docs are retrieved."""
        retrieved = ["doc6", "doc7", "doc8", "doc9", "doc10", "doc11", "doc12", "doc13", "doc14", "doc15"]
        relevant = {"doc1", "doc2", "doc3"}
        
        result = ndcg_at_10(retrieved, relevant)
        assert result == 0.0
    
    def test_empty_relevant(self):
        """Test edge case with empty relevant set."""
        retrieved = ["doc1", "doc2", "doc3"]
        relevant = set()
        
        result = ndcg_at_10(retrieved, relevant)
        assert result == 0.0
    
    def test_partial_ndcg(self):
        """Test when only some relevant docs are retrieved."""
        retrieved = ["doc1", "doc2", "doc6", "doc7", "doc8", "doc9", "doc10", "doc11", "doc12", "doc13"]
        relevant = {"doc1", "doc2", "doc3"}
        
        # Стандартная формула nDCG: discount 1/log2(rank+1) при rank с 1,
        # т.е. первый документ получает скидку 1/log2(2) = 1 (не 1/log2(1)).
        # DCG  = 1/log2(2) + 1/log2(3)        = 1 + 0.6309 = 1.6309
        # IDCG = 1/log2(2) + 1/log2(3) + 1/log2(4) = 1 + 0.6309 + 0.5 = 2.1309
        # nDCG = 1.6309 / 2.1309 = 0.7654
        result = ndcg_at_10(retrieved, relevant)
        assert result == pytest.approx(0.7654, rel=1e-3)


class TestMrr:
    """Test Mean Reciprocal Rank calculation."""
    
    def test_first_rank(self):
        """Test when relevant doc is first."""
        retrieved = ["doc1", "doc2", "doc3", "doc4", "doc5"]
        relevant = {"doc1", "doc3", "doc5"}
        
        result = mrr(retrieved, relevant)
        assert result == 1.0  # 1/1
    
    def test_second_rank(self):
        """Test when relevant doc is second."""
        retrieved = ["doc2", "doc1", "doc3", "doc4", "doc5"]
        relevant = {"doc1", "doc3", "doc5"}
        
        result = mrr(retrieved, relevant)
        assert result == 0.5  # 1/2
    
    def test_third_rank(self):
        """Test when relevant doc is third."""
        retrieved = ["doc2", "doc4", "doc1", "doc3", "doc5"]
        relevant = {"doc1", "doc3", "doc5"}
        
        result = mrr(retrieved, relevant)
        assert result == pytest.approx(1 / 3, rel=1e-6)  # doc1 на 3-й позиции
    
    def test_no_relevant_in_top_10(self):
        """Test when no relevant docs in top-10."""
        retrieved = ["doc6", "doc7", "doc8", "doc9", "doc10", "doc11", "doc12", "doc13", "doc14", "doc15"]
        relevant = {"doc1", "doc2", "doc3"}
        
        result = mrr(retrieved, relevant)
        assert result == 0.0
    
    def test_multiple_relevant(self):
        """Test when multiple relevant docs, takes first."""
        retrieved = ["doc1", "doc3", "doc2", "doc4", "doc5"]
        relevant = {"doc1", "doc2", "doc3", "doc5"}
        
        result = mrr(retrieved, relevant)
        assert result == 1.0  # Takes first relevant doc at rank 1
    
    def test_empty_relevant(self):
        """Test edge case with empty relevant set."""
        retrieved = ["doc1", "doc2", "doc3"]
        relevant = set()
        
        result = mrr(retrieved, relevant)
        assert result == 0.0
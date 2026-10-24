"""Unit tests for ShadowTrafficService (PR-08).

Tests strategy parsing, shadow traffic execution, sampling logic, and error handling.
Uses mocks for SearchOrchestrator and database operations.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.schemas import SearchRequest, SearchResponse
from app.config import FeatureFlags, ShadowTrafficConfig
from app.services.shadow_traffic import ShadowControlError, ShadowResult, ShadowTrafficService


class TestShadowTrafficConfig:
    """Test ShadowTrafficService configuration and sampling."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ShadowTrafficConfig()
        assert config.enabled is False
        assert config.strategies == []
        assert config.log_variant is True
        assert config.experiment_id is None
        assert config.sample_rate == 1.0

    def test_env_override(self):
        """Test environment variable override."""
        with patch.dict("os.environ", {
            "SHADOW_EXPERIMENT_ID": "shadow-exp-123",
            "SHADOW_SAMPLE_RATE": "0.1",
            "SHADOW_ENABLED": "true",
            "SHADOW_STRATEGIES": '["weighted+rerank"]',
        }):
            config = ShadowTrafficConfig()
            assert config.experiment_id == "shadow-exp-123"
            assert config.sample_rate == 0.1
            assert config.enabled is True
            assert config.strategies == ["weighted+rerank"]

    def test_sample_rate_bounds(self):
        """Test sample_rate validation bounds."""
        with pytest.raises(ValueError):
            ShadowTrafficConfig(sample_rate=-0.1)  # Below 0

        with pytest.raises(ValueError):
            ShadowTrafficConfig(sample_rate=1.1)  # Above 1

    @patch("random.random")
    def test_should_sample_gates(self, mock_random):
        """Test shadow traffic sampling logic with various gate conditions."""
        config = ShadowTrafficConfig()
        feature_flags = FeatureFlags()
        service = ShadowTrafficService(config, feature_flags)

        # Test disabled feature flag
        feature_flags.shadow_traffic_enabled = False
        assert service.should_sample() is None

        # Test disabled config
        feature_flags.shadow_traffic_enabled = True
        config.enabled = False
        assert service.should_sample() is None

        # Test no experiment_id
        config.enabled = True
        config.experiment_id = None
        assert service.should_sample() is None

        # Test empty strategies
        config.experiment_id = "test-exp"
        config.strategies = []
        assert service.should_sample() is None

        # Test sampling rate 0.0
        config.strategies = ["weighted+rerank"]
        config.sample_rate = 0.0
        mock_random.return_value = 0.5  # Above 0.0, should not sample
        assert service.should_sample() is None

        # Test sampling rate 1.0
        config.sample_rate = 1.0
        mock_random.return_value = 0.5  # Below 1.0, should sample
        assert service.should_sample() == "test-exp"

        # Test sampling rate 0.5
        config.sample_rate = 0.5
        mock_random.return_value = 0.3  # Below 0.5, should sample
        assert service.should_sample() == "test-exp"

        mock_random.return_value = 0.7  # Above 0.5, should not sample
        assert service.should_sample() is None


class TestStrategyParsing:
    """Test strategy string parsing."""

    def setup_method(self):
        """Setup test fixtures."""
        config = ShadowTrafficConfig()
        feature_flags = FeatureFlags()
        self.service = ShadowTrafficService(config, feature_flags)

    def test_parse_simple_strategies(self):
        """Test parsing of simple strategy strings."""
        params = self.service.parse_strategy("rrf")
        assert params == {"fusion": "rrf"}

        params = self.service.parse_strategy("weighted")
        assert params == {"fusion": "weighted"}

        params = self.service.parse_strategy("rerank")
        assert params == {"rerank": True}

        params = self.service.parse_strategy("personalize")
        assert params == {"personalize": True}

        params = self.service.parse_strategy("diversify")
        assert params == {"diversify": True}

    def test_parse_combined_strategies(self):
        """Test parsing of combined strategy strings."""
        params = self.service.parse_strategy("weighted+rerank")
        assert params == {"fusion": "weighted", "rerank": True}

        params = self.service.parse_strategy("rrf+personalize+diversify")
        assert params == {
            "fusion": "rrf",
            "personalize": True,
            "diversify": True,
        }

        params = self.service.parse_strategy("weighted+alpha:0.7")
        assert params == {"fusion": "weighted", "alpha": 0.7}

    def test_parse_invalid_strategies(self):
        """Test parsing of invalid strategy strings."""
        with pytest.raises(ValueError, match="Unknown strategy token"):
            self.service.parse_strategy("invalid_token")

        with pytest.raises(ValueError, match="Invalid alpha value"):
            self.service.parse_strategy("alpha:invalid")

        with pytest.raises(ValueError, match="Invalid alpha value"):
            self.service.parse_strategy("alpha:")


class TestShadowTrafficExecution:
    """Test shadow traffic execution logic."""

    def setup_method(self):
        """Setup test fixtures."""
        self.config = ShadowTrafficConfig(
            enabled=True,
            experiment_id="shadow-exp",
            strategies=["weighted+rerank"],
            sample_rate=1.0,
        )
        self.feature_flags = FeatureFlags()
        self.feature_flags.shadow_traffic_enabled = True
        self.service = ShadowTrafficService(self.config, self.feature_flags)

    @patch("app.search.orchestrator.SearchOrchestrator.fork")
    async def test_run_shadow_success(self, mock_fork):
        """Test successful shadow traffic execution."""
        # Mock request and orchestrator
        request = SearchRequest(
            query="test query",
            tenant_id="12345678-1234-1234-1234-123456789abc",
            user_id="12345678-1234-1234-1234-123456789def",
        )

# Mock orchestrator and forked instances
        mock_orchestrator = MagicMock()
        mock_control_orchestrator = AsyncMock()
        mock_treatment_orchestrator = AsyncMock()
        mock_orchestrator.fork = MagicMock(side_effect=[mock_control_orchestrator, mock_treatment_orchestrator])

        # Mock search responses
        control_response = SearchResponse(
            hits=[],
            facets={},
            total_lexical=10,
            total_vector=5,
            latency_ms=100,
            degraded=False,
            partial=False,
            personalization=None,
            variant="control",
        )

        treatment_response = SearchResponse(
            hits=[],
            facets={},
            total_lexical=8,
            total_vector=7,
            latency_ms=120,
            degraded=False,
            partial=False,
            personalization=None,
            variant="treatment",
        )

        mock_control_orchestrator.search.return_value = control_response
        mock_treatment_orchestrator.search.return_value = treatment_response

        # Execute shadow traffic
        result = await self.service.run_shadow(request, mock_orchestrator)

        # Verify results
        assert result.request == request
        assert result.control == control_response
        assert len(result.treatments) == 1
        assert "treatment" in result.treatments
        assert result.treatments["treatment"] == treatment_response
        assert result.latency_ms > 0

        # Verify fork was called
        assert mock_orchestrator.fork.call_count == 2

        # Verify search was called on forked orchestrators
        mock_control_orchestrator.search.assert_called_once()
        mock_treatment_orchestrator.search.assert_called_once()

    @patch("app.search.orchestrator.SearchOrchestrator.fork")
    async def test_run_shadow_multi_strategy(self, mock_fork):
        """Test shadow traffic execution with multiple treatment strategies."""
        self.config.strategies = ["weighted+rerank", "rrf+personalize"]
        service = ShadowTrafficService(self.config, self.feature_flags)

        # Mock request and orchestrator
        request = SearchRequest(
            query="test query",
            tenant_id="12345678-1234-1234-1234-123456789abc",
            user_id="12345678-1234-1234-1234-123456789def",
        )

# Mock orchestrator and forked instances
        mock_orchestrator = MagicMock()
        mock_control_orchestrator = AsyncMock()
        mock_treatment1_orchestrator = AsyncMock()
        mock_treatment2_orchestrator = AsyncMock()
        mock_orchestrator.fork = MagicMock(side_effect=[
            mock_control_orchestrator,
            mock_treatment1_orchestrator,
            mock_treatment2_orchestrator,
        ])

        # Mock responses
        control_response = SearchResponse(
            hits=[], facets={}, total_lexical=10, total_vector=5, latency_ms=100,
            degraded=False, partial=False, personalization=None, variant="control"
        )

        treatment1_response = SearchResponse(
            hits=[], facets={}, total_lexical=8, total_vector=7, latency_ms=120,
            degraded=False, partial=False, personalization=None, variant="treatment_1"
        )

        treatment2_response = SearchResponse(
            hits=[], facets={}, total_lexical=9, total_vector=6, latency_ms=110,
            degraded=False, partial=False, personalization=None, variant="treatment_2"
        )

        mock_control_orchestrator.search.return_value = control_response
        mock_treatment1_orchestrator.search.return_value = treatment1_response
        mock_treatment2_orchestrator.search.return_value = treatment2_response

        # Execute shadow traffic
        result = await service.run_shadow(request, mock_orchestrator)

        # Verify results
        assert len(result.treatments) == 2
        assert "treatment_1" in result.treatments
        assert "treatment_2" in result.treatments

    @patch("app.search.orchestrator.SearchOrchestrator.fork")
    async def test_run_shadow_timeout(self, mock_fork):
        """Test shadow traffic execution with treatment timeout."""
        # Mock a long-running treatment
        async def slow_search(*args, **kwargs):
            await asyncio.sleep(0.1)  # Simulate timeout
            return SearchResponse(
                hits=[], facets={}, total_lexical=5, total_vector=5, latency_ms=1000,
                degraded=False, partial=False, personalization=None, variant="treatment"
            )

        # Mock orchestrator and forked instances
        mock_orchestrator = MagicMock()
        mock_control_orchestrator = AsyncMock()
        mock_treatment_orchestrator = AsyncMock()
        mock_orchestrator.fork = MagicMock(side_effect=[mock_control_orchestrator, mock_treatment_orchestrator])
        mock_control_orchestrator.search.return_value = SearchResponse(
            hits=[], facets={}, total_lexical=10, total_vector=5, latency_ms=100,
            degraded=False, partial=False, personalization=None, variant="control"
        )
        mock_treatment_orchestrator.search.side_effect = slow_search

        # Mock asyncio.wait_for to raise timeout
        with patch("asyncio.wait_for", side_effect=TimeoutError("timeout")):
            result = await self.service.run_shadow(
                SearchRequest(query="test", tenant_id="12345678-1234-1234-1234-123456789abc", user_id="12345678-1234-1234-1234-123456789def"),
                mock_orchestrator
            )

        # Verify control response is present, treatment is not
        assert result.control is not None
        assert len(result.treatments) == 0  # Treatment failed due to timeout

    @patch("app.search.orchestrator.SearchOrchestrator.fork")
    async def test_run_shadow_control_failure(self, mock_fork):
        """Test shadow traffic execution with control search failure."""
# Mock orchestrator and forked instances
        mock_orchestrator = MagicMock()
        mock_control_orchestrator = AsyncMock()
        mock_orchestrator.fork = MagicMock(return_value=mock_control_orchestrator)
        mock_control_orchestrator.search.side_effect = Exception("Control failed")

        # Should raise ShadowControlError
        with pytest.raises(ShadowControlError):
            await self.service.run_shadow(
                SearchRequest(query="test", tenant_id="12345678-1234-1234-1234-123456789abc", user_id="12345678-1234-1234-1234-123456789def"),
                mock_orchestrator
            )

    @patch("app.search.orchestrator.SearchOrchestrator.fork")
    async def test_run_shadow_invalid_strategy(self, mock_fork):
        """Test shadow traffic execution with invalid strategy."""
        self.config.strategies = ["invalid_strategy"]
        ShadowTrafficService(self.config, self.feature_flags)

        # Mock orchestrator and forked instances
        mock_orchestrator = MagicMock()

        # Should raise ValueError for invalid strategy
        with pytest.raises(ValueError, match="Invalid strategy"):
            await self.service.run_shadow(
                SearchRequest(query="test", tenant_id="12345678-1234-1234-1234-123456789abc", user_id="12345678-1234-1234-1234-123456789def"),
                mock_orchestrator
            )


class TestShadowLogging:
    """Test shadow traffic logging functionality."""

    def setup_method(self):
        """Setup test fixtures."""
        self.config = ShadowTrafficConfig(
            enabled=True,
            experiment_id="shadow-exp",
            strategies=["weighted+rerank"],
            sample_rate=1.0,
        )
        self.feature_flags = FeatureFlags()
        self.service = ShadowTrafficService(self.config, self.feature_flags)

    @patch("app.db.queries.search_events.SearchEventQueries")
    @patch("app.db.session.async_session_factory")
    @patch("app.config.get_settings")
    async def test_log_shadow_success(self, mock_get_settings, mock_session_factory, mock_queries_class):
        """Test successful shadow traffic logging."""
        # Mock settings
        mock_settings = AsyncMock()
        mock_settings.search_events = AsyncMock()
        mock_get_settings.return_value = mock_settings

        # Mock queries and session
        mock_queries = AsyncMock()
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        # Mock SearchEventQueries constructor to return our mock
        def mock_search_event_queries_constructor(config):
            return mock_queries

        mock_queries_class.side_effect = mock_search_event_queries_constructor
        mock_queries.insert_search_event = AsyncMock()

        # Mock shadow result
        request = SearchRequest(query="test", tenant_id="12345678-1234-1234-1234-123456789abc", user_id="12345678-1234-1234-1234-123456789def")
        control_response = SearchResponse(
            hits=[], facets={}, total_lexical=2, total_vector=0,
            latency_ms=100, degraded=False, partial=False, personalization=None,
            variant="control"
        )
        treatment_response = SearchResponse(
            hits=[], facets={}, total_lexical=0, total_vector=2,
            latency_ms=120, degraded=False, partial=False, personalization=None,
            variant="treatment"
        )

        shadow_result = ShadowResult(
            request=request,
            control=control_response,
            treatments={"treatment": treatment_response},
            latency_ms=150,
        )

        # Execute logging
        await self.service.log_shadow(shadow_result, "user123", "tenant456", "shadow-exp")

        # Verify insert calls
        await asyncio.sleep(0.1)  # Give async calls time to complete
        assert mock_queries.insert_search_event.call_count == 2

        # Check control call
        control_call = mock_queries.insert_search_event.call_args_list[0]
        assert control_call[1]["user_id"] == "user123"
        assert control_call[1]["tenant_id"] == "tenant456"
        assert control_call[1]["query_text"] == "test"
        assert control_call[1]["variant"] == "control"
        assert control_call[1]["experiment_id"] == "shadow-exp"

        # Check treatment call
        treatment_call = mock_queries.insert_search_event.call_args_list[1]
        assert treatment_call[1]["user_id"] == "user123"
        assert treatment_call[1]["tenant_id"] == "tenant456"
        assert treatment_call[1]["query_text"] == "test"
        assert treatment_call[1]["variant"] == "treatment"
        assert treatment_call[1]["experiment_id"] == "shadow-exp"

    @patch("app.db.queries.search_events.SearchEventQueries")
    @patch("app.db.session.async_session_factory")
    @patch("app.config.get_settings")
    async def test_log_shadow_multi_strategy(self, mock_get_settings, mock_session_factory, mock_queries_class):
        """Test shadow traffic logging with multiple treatment strategies."""
        self.config.strategies = ["weighted", "rrf"]
        ShadowTrafficService(self.config, self.feature_flags)

        # Mock settings
        mock_settings = AsyncMock()
        mock_settings.search_events = AsyncMock()
        mock_get_settings.return_value = mock_settings

        # Mock queries
        mock_queries = AsyncMock()
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        # Mock SearchEventQueries constructor to return our mock
        def mock_search_event_queries_constructor(config):
            return mock_queries

        mock_queries_class.side_effect = mock_search_event_queries_constructor
        mock_queries.insert_search_event = AsyncMock()

        # Mock shadow result with multiple treatments
        request = SearchRequest(query="test", tenant_id="12345678-1234-1234-1234-123456789abc", user_id="12345678-1234-1234-1234-123456789def")
        control_response = SearchResponse(
            hits=[], facets={}, total_lexical=1, total_vector=0,
            latency_ms=100, degraded=False, partial=False, personalization=None,
            variant="control"
        )
        treatment1_response = SearchResponse(
            hits=[], facets={}, total_lexical=0, total_vector=1,
            latency_ms=110, degraded=False, partial=False, personalization=None,
            variant="treatment_1"
        )
        treatment2_response = SearchResponse(
            hits=[{"doc_id": "12345678-1234-1234-1234-123456789abc", "score": 0.8, "title": "Doc 3", "snippet": "Snippet 3", "attributes": {}}], facets={}, total_lexical=0, total_vector=1,
            latency_ms=120, degraded=False, partial=False, personalization=None,
            variant="treatment_2"
        )

        shadow_result = ShadowResult(
            request=request,
            control=control_response,
            treatments={"treatment_1": treatment1_response, "treatment_2": treatment2_response},
            latency_ms=150,
        )

        # Execute logging
        await service.log_shadow(shadow_result, "user123", "tenant456", "shadow-exp")

        # Verify insert calls
        assert mock_queries.insert_search_event.call_count == 3  # control + 2 treatments


class TestShadowIntegration:
    """Test shadow traffic integration with orchestrator."""

    def setup_method(self):
        """Setup test fixtures."""
        self.config = ShadowTrafficConfig(
            enabled=True,
            experiment_id="shadow-exp",
            strategies=["weighted+rerank"],
            sample_rate=1.0,
        )
        self.feature_flags = FeatureFlags()
        self.feature_flags.shadow_traffic_enabled = True

    @patch("app.db.queries.search_events.SearchEventQueries")
    @patch("app.services.shadow_traffic.ShadowTrafficService.run_shadow")
    async def test_orchestrator_shadow_integration(self, mock_run_shadow, mock_log_shadow):
        """Test orchestrator integration with shadow traffic."""
        from app.search.orchestrator import SearchOrchestrator

        # Create mock orchestrator with shadow service
        mock_session = AsyncMock()
        service = ShadowTrafficService(self.config, self.feature_flags)
        mock_orchestrator = SearchOrchestrator(
            session=mock_session,
            qdrant_client=AsyncMock(),
            embedding_service=AsyncMock(),
            embedding_cache=AsyncMock(),
            config=AsyncMock(),
            shadow_traffic_service=service,
        )

        # Mock shadow result
        request = SearchRequest(query="test", tenant_id="12345678-1234-1234-1234-123456789abc", user_id="12345678-1234-1234-1234-123456789def")
        mock_shadow_result = AsyncMock()

        mock_run_shadow.return_value = mock_shadow_result

        # Test that shadow service methods are called
        # This would be tested more thoroughly in integration tests
        # Here we just verify the service exists and methods are accessible
        assert mock_orchestrator._shadow is not None
        assert mock_orchestrator._shadow.should_sample() == "shadow-exp"

        # Test fork method exists
        forked_orchestrator = mock_orchestrator.fork(mock_session)
        assert forked_orchestrator is not None
        assert forked_orchestrator is not mock_orchestrator  # Different instance

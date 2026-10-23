"""Unit tests for OpenTelemetry distributed tracing (PR-06).

Verifies that:
- setup_tracing initializes OTel SDK with correct configuration
- span context manager creates spans with attributes
- trace_id is properly generated and correlated with logs
- user_id hashing works for privacy
- tracing_enabled=False disables spans (no-op)
- OTel metrics counters increment correctly
"""

import hashlib
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider

from app.config import FeatureFlags, TracingConfig
from app.observability import metrics
from app.observability.tracing import (
    CountingSpanExporter,
    get_tracer,
    hash_user_id,
    setup_tracing,
    span,
)


@pytest.fixture
def mock_app():
    """Mock FastAPI application."""
    return MagicMock(spec=FastAPI)


@pytest.fixture
def mock_tracer_provider():
    """Mock OTel TracerProvider."""
    return MagicMock(spec=TracerProvider)


@pytest.fixture
def mock_exporter():
    """Mock OTLP SpanExporter."""
    return MagicMock()


@pytest.fixture
def mock_fastapi_instrumentor():
    """Mock FastAPIInstrumentor."""
    with patch("app.observability.tracing.FastAPIInstrumentor") as mock:
        yield mock


@pytest.fixture
def mock_redis_instrumentor():
    """Mock RedisInstrumentor."""
    with patch("app.observability.tracing.RedisInstrumentor") as mock:
        yield mock


@pytest.fixture
def mock_logging_handler():
    """Mock OTel LoggingHandler."""
    with patch("app.observability.tracing.LoggingHandler") as mock:
        yield mock


def test_hash_user_id():
    """Test user_id hashing for privacy."""
    # Test UUID
    uuid_str = "123e4567-e89b-12d3-a456-426614174000"
    hashed = hash_user_id(uuid_str)
    expected = hashlib.sha256(uuid_str.encode()).hexdigest()
    assert hashed == expected
    assert len(hashed) == 64  # SHA256 hex length

    # Test None
    assert hash_user_id(None) == hashlib.sha256(b"").hexdigest()

    # Test string
    assert hash_user_id("test_user") == hashlib.sha256(b"test_user").hexdigest()


def test_setup_tracing_disabled(mock_app, mock_fastapi_instrumentor, mock_redis_instrumentor):
    """Test that tracing setup is skipped when disabled."""
    config = TracingConfig()
    feature_flags = FeatureFlags(tracing_enabled=False)

    setup_tracing(mock_app, config, feature_flags)

    # Verify instrumentations were not called
    mock_fastapi_instrumentor.return_value.instrument_app.assert_not_called()
    mock_redis_instrumentor.return_value.instrument.assert_not_called()


def test_setup_tracing_enabled(mock_app, mock_fastapi_instrumentor, mock_redis_instrumentor):
    """Test that tracing setup works when enabled."""
    config = TracingConfig(
        otel_endpoint="http://test-collector:4317",
        sample_rate=0.1,
    )
    feature_flags = FeatureFlags(tracing_enabled=True)

    setup_tracing(mock_app, config, feature_flags)

    # Verify instrumentations were called
    mock_fastapi_instrumentor.return_value.instrument_app.assert_called_once()
    mock_redis_instrumentor.return_value.instrument.assert_called_once()


def test_span_context_manager():
    """Test span context manager creates and ends spans."""
    tracer = get_tracer("test")

    with span(tracer, "test.span", attributes={"key": "value"}) as span_obj:
        assert span_obj is not None
        assert span_obj.name == "test.span"
        span_obj.set_attribute("test_attr", "test_value")

    # Span should be ended after context exit
    assert span_obj.is_recording() is False


def test_span_context_manager_no_attributes():
    """Test span context manager without attributes."""
    tracer = get_tracer("test")

    with span(tracer, "test.span") as span_obj:
        assert span_obj is not None
        assert span_obj.name == "test.span"


@pytest.mark.asyncio
async def test_embed_query_span():
    """Test that embed_query creates a span."""
    from app.embedding.service import EmbeddingService

    # Mock the cache to return None (cache miss)
    with patch.object(EmbeddingService, "embed_texts") as mock_embed:
        mock_embed.return_value = np.array([[0.1, 0.2, 0.3]])

        service = EmbeddingService()

        with patch("app.observability.tracing.get_tracer") as mock_get_tracer:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_tracer.start_span.return_value.__enter__ = mock_span
            mock_tracer.start_span.return_value.__exit__ = MagicMock()
            mock_get_tracer.return_value = mock_tracer

            await service.embed_query("test query")

            # Verify span was created
            mock_tracer.start_span.assert_called_once_with(
                "embedding.query",
                attributes={
                    "cache_hit": False,
                    "model_name": "bge-m3",
                }
            )


@pytest.mark.asyncio
async def test_rerank_span():
    """Test that rerank creates a span."""
    from app.reranker.schemas import RerankCandidate
    from app.reranker.service import RerankerService

    # Mock the model to avoid loading
    with patch.object(RerankerService, "_lazy_load"), \
         patch.object(RerankerService, "_mock_rerank") as mock_mock:
        mock_mock.return_value = []

        service = RerankerService(config=RerankerConfig(mock_mode=True))

        with patch("app.observability.tracing.get_tracer") as mock_get_tracer:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_tracer.start_span.return_value.__enter__ = mock_span
            mock_tracer.start_span.return_value.__exit__ = MagicMock()
            mock_get_tracer.return_value = mock_tracer

            candidates = [RerankCandidate(doc_id="doc1", text="test", score=0.5)]
            await service.rerank("query", candidates)

            # Verify span was created
            mock_tracer.start_span.assert_called_once_with(
                "rerank",
                attributes={
                    "n_docs": 1,
                    "device": "cpu",  # default fallback
                    "mock_mode": True,
                    "inference_ms": 0,
                }
            )


def test_counting_span_exporter_increment():
    """Test that CountingSpanExporter increments Prometheus counters."""
    mock_wrapped = MagicMock()
    mock_wrapped.export.return_value = 0  # Success

    exporter = CountingSpanExporter(mock_wrapped, "test-job")

    # Mock spans
    spans = [MagicMock(), MagicMock()]

    # Export should increment counter and delegate
    result = exporter.export(spans)

    # Verify counter was incremented
    metrics.otel_spans_exported_total.labels(job="test-job").assert_called_with(2)

    # Verify delegation
    mock_wrapped.export.assert_called_once_with(spans)
    assert result == 0


def test_counting_span_exporter_error():
    """Test that CountingSpanExporter handles errors."""
    mock_wrapped = MagicMock()
    mock_wrapped.export.side_effect = RuntimeError("Test error")

    exporter = CountingSpanExporter(mock_wrapped, "test-job")

    # Mock spans
    spans = [MagicMock()]

    # Export should raise and increment error counter
    with pytest.raises(RuntimeError):
        exporter.export(spans)

    # Verify error counter was incremented
    metrics.otel_export_errors_total.labels(
        job="test-job",
        error_type="RuntimeError"
    ).assert_called_with(1)


def test_counting_span_exporter_shutdown():
    """Test that CountingSpanExporter shutdowns wrapped exporter."""
    mock_wrapped = MagicMock()

    exporter = CountingSpanExporter(mock_wrapped, "test-job")
    exporter.shutdown()

    mock_wrapped.shutdown.assert_called_once()


def test_metrics_integration():
    """Test that metrics are properly integrated."""
    # Verify the new metrics exist
    assert hasattr(metrics, "otel_spans_exported_total")
    assert hasattr(metrics, "otel_export_errors_total")

    # Verify they're counters
    assert hasattr(metrics.otel_spans_exported_total, "inc")
    assert hasattr(metrics.otel_export_errors_total, "inc")


def test_tracer_provider_initialization():
    """Test that tracer provider is properly initialized with sampling."""
    config = TracingConfig(sample_rate=0.5)
    feature_flags = FeatureFlags(tracing_enabled=True)

    with patch("app.observability.tracing.TracerProvider") as mock_provider, \
         patch("app.observability.tracing.OTLPSpanExporter") as mock_exporter, \
         patch("app.observability.tracing.BatchSpanProcessor") as mock_processor:

        mock_provider.return_value = MagicMock()
        mock_exporter.return_value = MagicMock()
        mock_processor.return_value = MagicMock()

        setup_tracing(MagicMock(), config, feature_flags)

        # Verify provider was created with correct sampling
        mock_provider.assert_called_once()

        args, kwargs = mock_provider.call_args
        assert "sampler" in kwargs

        # Verify processor was added
        mock_provider.return_value.add_span_processor.assert_called_once()

def test_logging_handler_setup():
    """Test that OTel logging handler is set up."""
    config = TracingConfig()
    feature_flags = FeatureFlags(tracing_enabled=True)

    with patch("app.observability.tracing.stdlib_logging") as mock_logging, \
         patch("app.observability.tracing.structlog") as mock_structlog:

        setup_tracing(MagicMock(), config, feature_flags)

        # Verify logging handler was added to root logger
        mock_logging.getLogger.return_value.addHandler.assert_called_once()


def test_structlog_processor_added():
    """Test that OTel log processor is added to structlog."""
    config = TracingConfig()
    feature_flags = FeatureFlags(tracing_enabled=True)

    with patch("app.observability.tracing.structlog") as mock_structlog:

        setup_tracing(MagicMock(), config, feature_flags)

        # Verify OTel processor was added
        processors = mock_structlog.configure.call_args[1]["processors"]
        assert any("otel_log_processor" in str(p) for p in processors)


class TestTracingIntegration:
    """Integration tests for tracing components."""

    def test_trace_id_format(self):
        """Test that trace_id is 32-char hex string."""
        tracer = get_tracer("test")

        with span(tracer, "test.span") as span_obj:
            # OTel trace_id is uint64, format as 32-char hex
            trace_id_hex = f"{span_obj.get_span_context().trace_id:032x}"
            assert len(trace_id_hex) == 32
            assert all(c in "0123456789abcdef" for c in trace_id_hex)

    def test_parent_child_spans(self):
        """Test that parent-child span relationships work."""
        tracer = get_tracer("test")

        with span(tracer, "parent") as parent:
            with span(tracer, "child") as child:
                # Child should have parent context
                assert child.parent.span_id == parent.get_span_context().span_id
                assert child.parent.trace_id == parent.get_span_context().trace_id

    def test_span_attributes_types(self):
        """Test that span attributes are properly typed."""
        tracer = get_tracer("test")

        attributes = {
            "string": "test",
            "int": 42,
            "float": 3.14,
            "bool": True,
            "none": None,
        }

        with span(tracer, "test", attributes=attributes) as span_obj:
            # Verify attributes are set
            assert span_obj.attributes["string"] == "test"
            assert span_obj.attributes["int"] == 42
            assert span_obj.attributes["float"] == 3.14
            assert span_obj.attributes["bool"] is True
            assert span_obj.attributes["none"] is None

"""Unit tests for structured logging (P-15, ARCHITECT §11.4).

Verifies that ``setup_logging()`` produces valid JSON output, that contextvars
``request_id`` / ``tenant_id`` / ``trace_id`` are injected, and that
``get_logger`` returns a structlog logger proxy.
"""

from __future__ import annotations

import io
import json
import sys

from app.observability.logging import (
    get_logger,
    request_id_ctx,
    setup_logging,
    tenant_id_ctx,
    trace_id_ctx,
)


def _capture_logs(capture_into: io.StringIO) -> list[dict]:  # type: ignore[type-arg]
    """Run a block with logging redirected to *capture_into* and return parsed JSON lines."""
    old_stdout = sys.stdout
    sys.stdout = capture_into  # type: ignore[assignment]
    try:
        setup_logging(level="DEBUG")
        logger = get_logger("test.logger")
        logger.info("hello", key="value")
        logger.warning("oops", code=42)
    finally:
        sys.stdout = old_stdout
    lines = [line for line in capture_into.getvalue().splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


class TestSetupLogging:
    def test_produces_valid_json(self) -> None:
        buf = io.StringIO()
        logs = _capture_logs(buf)
        assert len(logs) >= 2
        for entry in logs:
            assert isinstance(entry, dict)
            assert "timestamp" in entry
            assert "level" in entry

    def test_request_id_present(self) -> None:
        token = request_id_ctx.set("req-123")
        try:
            buf = io.StringIO()
            logs = _capture_logs(buf)
            assert all(entry.get("request_id") == "req-123" for entry in logs)
        finally:
            request_id_ctx.reset(token)

    def test_tenant_id_present(self) -> None:
        token = tenant_id_ctx.set("tenant-abc")
        try:
            buf = io.StringIO()
            logs = _capture_logs(buf)
            assert all(entry.get("tenant_id") == "tenant-abc" for entry in logs)
        finally:
            tenant_id_ctx.reset(token)

    def test_trace_id_present(self) -> None:
        token = trace_id_ctx.set("trace-xyz")
        try:
            buf = io.StringIO()
            logs = _capture_logs(buf)
            assert all(entry.get("trace_id") == "trace-xyz" for entry in logs)
        finally:
            trace_id_ctx.reset(token)

    def test_level_is_uppercase(self) -> None:
        buf = io.StringIO()
        logs = _capture_logs(buf)
        levels = {entry["level"] for entry in logs}
        assert levels == {"INFO", "WARNING"}

    def test_message_field(self) -> None:
        buf = io.StringIO()
        logs = _capture_logs(buf)
        # structlog uses 'event' as the message key
        events = {entry.get("event") for entry in logs}
        assert "hello" in events
        assert "oops" in events


class TestGetLogger:
    def test_returns_structlog_logger(self) -> None:
        setup_logging(level="INFO")
        logger = get_logger("my.module")
        # structlog.get_logger returns a BoundLoggerLazyProxy or BoundLogger
        # depending on version; both support .info()/.warning()
        assert hasattr(logger, "info")
        assert hasattr(logger, "warning")

    def test_logger_emits_output(self) -> None:
        setup_logging(level="INFO")
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf  # type: ignore[assignment]
        try:
            logger = get_logger("named.module")
            logger.info("test_event")
        finally:
            sys.stdout = old_stdout
        output = buf.getvalue()
        # The logger produces valid JSON output with the event
        import json

        entry = json.loads(output.strip())
        assert entry["event"] == "test_event"
        assert entry["level"] == "INFO"


class TestContextVars:
    def test_request_id_default_empty(self) -> None:
        assert request_id_ctx.get("") == ""

    def test_tenant_id_default_empty(self) -> None:
        assert tenant_id_ctx.get("") == ""

    def test_trace_id_default_empty(self) -> None:
        assert trace_id_ctx.get("") == ""

    def test_set_and_reset(self) -> None:
        token = request_id_ctx.set("val")
        assert request_id_ctx.get("") == "val"
        request_id_ctx.reset(token)
        assert request_id_ctx.get("") == ""

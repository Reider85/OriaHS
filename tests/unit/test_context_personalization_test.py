"""Unit tests for contextual personalization (PR-05)."""

from app.services.personalization import PersonalizationService


def test_resolve_context_key_none():
    """Test resolve_context_key with None context."""
    result = PersonalizationService.resolve_context_key(None)
    assert result is None


def test_resolve_context_key_session():
    """Test resolve_context_key with session context."""
    from app.api.schemas import SearchContext

    context = SearchContext(session_id="test-session", device=None, time_of_day=None)
    result = PersonalizationService.resolve_context_key(context)
    assert result == "session:test-session"


def test_resolve_context_key_time_of_day():
    """Test resolve_context_key with time_of_day context (no session)."""
    from app.api.schemas import SearchContext

    context = SearchContext(session_id=None, device=None, time_of_day="morning")
    result = PersonalizationService.resolve_context_key(context)
    assert result == "time-of-day:morning"


def test_resolve_context_key_device():
    """Test resolve_context_key with device context (no session or time_of_day)."""
    from app.api.schemas import SearchContext

    context = SearchContext(session_id=None, device="mobile", time_of_day=None)
    result = PersonalizationService.resolve_context_key(context)
    assert result == "device:mobile"


def test_resolve_context_key_priority():
    """Test context key priority: session > time_of_day > device."""
    from app.api.schemas import SearchContext

    # Test session takes priority
    context = SearchContext(session_id="test-session", device="mobile", time_of_day="morning")
    result = PersonalizationService.resolve_context_key(context)
    assert result == "session:test-session"

    # Test time_of_day takes priority over device
    context = SearchContext(session_id=None, device="mobile", time_of_day="morning")
    result = PersonalizationService.resolve_context_key(context)
    assert result == "time-of-day:morning"

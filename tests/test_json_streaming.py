"""Tests for the incremental JSON-closing helper."""

from __future__ import annotations

from emergency_ai.core.client import (
    _extract_complete_keys,
    _finalize,
    _try_close_json,
)


def test_close_object_missing_brace():
    assert _try_close_json('{"a": 1') == '{"a": 1}'


def test_close_unterminated_string():
    closed = _try_close_json('{"a": "hello')
    assert closed is not None
    # Trailing comma/colon/dangling key removed; string is closed
    assert closed.endswith('"}')


def test_close_unterminated_array():
    closed = _try_close_json('{"a": [1, 2')
    assert closed == '{"a": [1, 2]}'


def test_close_handles_nested():
    closed = _try_close_json('{"a": [1, {"b": [2')
    assert closed == '{"a": [1, {"b": [2]}]}'


def test_extract_complete_keys_emits_only_closed_values():
    buffer = '{"urgency": "critical", "time_to_act_seconds": 30, "actions": ["a'
    keys = _extract_complete_keys(buffer, set())
    # urgency and time_to_act_seconds are closed (followed by comma); actions is still streaming
    fields = [k for k, _ in keys]
    assert "urgency" in fields
    assert "time_to_act_seconds" in fields
    assert "actions" not in fields


def test_extract_complete_keys_skips_already_emitted():
    buffer = '{"urgency": "critical", "time_to_act_seconds": 30,'
    seen = {"urgency"}
    keys = _extract_complete_keys(buffer, seen)
    fields = [k for k, _ in keys]
    assert "urgency" not in fields
    assert "time_to_act_seconds" in fields


def test_finalize_complete_object():
    assert _finalize('{"a": 1}') == {"a": 1}


def test_finalize_missing_brace():
    assert _finalize('{"a": 1') == {"a": 1}


def test_finalize_garbage_returns_none():
    assert _finalize("not json at all") is None

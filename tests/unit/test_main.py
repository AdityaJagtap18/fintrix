"""api.main itself is already exercised indirectly by every other test that builds a
TestClient — this file only covers the one piece of real logic that lives at module level:
the guard deciding whether to fire up Azure Monitor telemetry.
"""

from __future__ import annotations

import pytest

from api.main import _telemetry_enabled


def test_telemetry_is_disabled_under_pytest_even_with_a_connection_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PYTEST_VERSION is real, ambient state here — this file is only ever imported by
    # pytest — so this asserts the guard actually reads it, not just that it defaults off.
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=fake")
    assert "PYTEST_VERSION" in __import__("os").environ

    assert _telemetry_enabled() is False


def test_telemetry_is_disabled_with_no_connection_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)

    assert _telemetry_enabled() is False


def test_telemetry_would_enable_outside_pytest_given_a_connection_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=fake")
    monkeypatch.delenv("PYTEST_VERSION", raising=False)

    assert _telemetry_enabled() is True

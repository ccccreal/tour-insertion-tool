"""Smoke test of the web app with Streamlit's test runner (no browser)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "streamlit_demo" / "app.py")


def test_app_starts_without_result():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert at.title[0].value                     # a title is shown
    assert len(at.metric) == 0                   # nothing computed yet


def test_app_runs_winterthur_example():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.radio[0].set_value(at.radio[0].options[1]).run()     # Winterthur example
    at.button[0].click().run()
    assert not at.exception and not at.error
    values = {m.label: m.value for m in at.metric}
    assert values["Touren"] == "2" and values["Eingefügt"] == "6"
    assert values["Nicht einplanbar"] == "1"


def test_app_switches_language():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.selectbox[0].set_value("Français").run()
    assert at.title[0].value == "Insérer des commandes dans les tournées"


def test_app_optimize_shows_comparison():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.checkbox[0].check().run()                             # optimise tours
    at.radio[0].set_value(at.radio[0].options[1]).run()     # Winterthur example
    at.button[0].click().run()
    assert not at.exception and not at.error
    assert any(h.value == "Optimierung" for h in at.subheader)

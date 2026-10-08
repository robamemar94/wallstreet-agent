from app.interfaces.views.settings_views import merge_sections


def test_saving_settings_keeps_fields_the_form_does_not_send():
    current = {"performance": {"benchmark_ticker": "SPY", "cache_duration_min": 30, "start_date": "2026-02-12"},
               "ui": {"dark_mode": True}}
    incoming = {"performance": {"benchmark_ticker": "URTH", "cache_duration_min": 60}, "ui": {"dark_mode": False}}
    merged = merge_sections(current, incoming)
    assert merged["performance"] == {"benchmark_ticker": "URTH", "cache_duration_min": 60, "start_date": "2026-02-12"}
    assert merged["ui"] == {"dark_mode": False}


def test_lists_and_new_sections_are_replaced():
    merged = merge_sections({"rules": [1, 2]}, {"rules": [3], "nuevo": {"a": 1}})
    assert merged == {"rules": [3], "nuevo": {"a": 1}}

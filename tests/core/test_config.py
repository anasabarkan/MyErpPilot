import logging

from app.core.config import Settings


def test_stock_alert_min_threshold_uses_env_value():
    custom_settings = Settings(_env_file=None, STOCK_ALERT_MIN_THRESHOLD=8)

    assert custom_settings.stock_alert_min_threshold == 8.0


def test_stock_alert_scan_interval_minutes_drives_runtime_seconds():
    custom_settings = Settings(_env_file=None, STOCK_ALERT_SCAN_INTERVAL_MINUTES=12)

    assert custom_settings.stock_alert_scan_interval_minutes == 12
    assert custom_settings.stock_alert_scan_interval_seconds == 720


def test_legacy_threshold_config_still_works_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        custom_settings = Settings(_env_file=None, DEFAULT_LOW_STOCK_THRESHOLD=9)
        threshold = custom_settings.stock_alert_min_threshold

    assert threshold == 9.0
    assert "DEFAULT_LOW_STOCK_THRESHOLD is deprecated" in caplog.text


def test_legacy_seconds_config_still_works_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        custom_settings = Settings(_env_file=None, STOCK_ALERT_SCAN_INTERVAL_SECONDS=900)
        minutes = custom_settings.stock_alert_scan_interval_minutes
        seconds = custom_settings.stock_alert_scan_interval_seconds

    assert minutes == 15
    assert seconds == 900
    assert "STOCK_ALERT_SCAN_INTERVAL_SECONDS is deprecated" in caplog.text

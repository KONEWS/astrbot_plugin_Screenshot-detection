from unittest.mock import MagicMock, patch

from main import ScreenshotDetectionPlugin

DEFAULT_CONFIG = {
    "interval": 1200,
    "quiet_time": "0-8",
    "target_umo": "",
    "custom_provider_id": "",
    "analysis_prompt": "测试提示词 {{current_time}}",
    "send_image": True,
    "screenshot_only": False,
    "image_max_size": 1280,
    "max_screenshots": 10,
    "auto_start": False,
}


class _FakeConfig(dict):
    def save_config(self):
        pass


def _make_plugin(**overrides):
    config = _FakeConfig(DEFAULT_CONFIG)
    config.update(overrides)
    context = MagicMock()
    context.get_all_providers.return_value = []
    context.get_current_chat_provider_id = MagicMock(return_value="test_provider")
    return ScreenshotDetectionPlugin(context, config)


def test_parse_quiet_time_valid():
    p = _make_plugin()
    p._parse_quiet_time("2-6")
    assert p._quiet_start == 2
    assert p._quiet_end == 6


def test_parse_quiet_time_invalid_keeps_previous():
    p = _make_plugin()
    p._quiet_start, p._quiet_end = 3, 5
    p._parse_quiet_time("abc")
    assert p._quiet_start == 3
    assert p._quiet_end == 5


def test_parse_quiet_time_out_of_range_keeps_previous():
    p = _make_plugin()
    p._quiet_start, p._quiet_end = 3, 5
    p._parse_quiet_time("25-30")
    assert p._quiet_start == 3
    assert p._quiet_end == 5


def test_parse_quiet_time_empty():
    p = _make_plugin()
    p._parse_quiet_time("")
    assert p._quiet_start == 0
    assert p._quiet_end == 8


def test_is_quiet_time_within():
    p = _make_plugin()
    p._parse_quiet_time("0-8")
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value.hour = 5
        assert p._is_quiet_time() is True


def test_is_quiet_time_outside():
    p = _make_plugin()
    p._parse_quiet_time("0-8")
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value.hour = 12
        assert p._is_quiet_time() is False


def test_is_quiet_time_crossing_midnight():
    p = _make_plugin()
    p._parse_quiet_time("22-6")
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value.hour = 23
        assert p._is_quiet_time() is True
        mock_dt.now.return_value.hour = 4
        assert p._is_quiet_time() is True
        mock_dt.now.return_value.hour = 12
        assert p._is_quiet_time() is False


def test_normalize_umo_three_parts():
    p = _make_plugin()
    assert p._normalize_umo("default:FriendMessage:123") == "default:FriendMessage:123"


def test_normalize_umo_friend_no_platform():
    p = _make_plugin()
    assert p._normalize_umo("FriendMessage:123") == "default:FriendMessage:123"


def test_normalize_umo_friend_with_platform():
    p = _make_plugin()
    assert p._normalize_umo("FriendMessage:123", "qq") == "qq:FriendMessage:123"


def test_normalize_umo_unknown_first_part():
    p = _make_plugin()
    assert p._normalize_umo("qq:123") == "qq:FriendMessage:123"


def test_format_interval_hours():
    p = _make_plugin()
    assert p._format_interval(7200) == "2小时"


def test_format_interval_minutes():
    p = _make_plugin()
    assert p._format_interval(300) == "5分钟"


def test_format_interval_seconds():
    p = _make_plugin()
    assert p._format_interval(90) == "90秒"


def test_parse_interval_units():
    p = _make_plugin()
    assert p._parse_interval("2h") == 7200
    assert p._parse_interval("5m") == 300
    assert p._parse_interval("30s") == 30
    assert p._parse_interval("300") == 300


def test_take_screenshot_dedup():
    p = _make_plugin()
    with patch("main.ImageGrab.grab") as mock_grab, patch("main.HAS_PIL", True):
        img = MagicMock()
        img.size = (1920, 1080)
        mock_grab.return_value = img
        import io
        from PIL import Image

        test_img = Image.new("RGB", (100, 100), color="red")
        buffer = io.BytesIO()
        test_img.save(buffer, format="PNG")
        img.resize.return_value = test_img
        img.resize = lambda size, resample: test_img

        p._take_screenshot()
        first_hash = p._last_screenshot_hash
        p._take_screenshot()
        assert p._last_screenshot_hash == first_hash
        assert p._is_duplicate is True


def test_take_screenshot_resize():
    p = _make_plugin()
    with patch("main.ImageGrab.grab") as mock_grab, patch("main.HAS_PIL", True):
        import io
        from PIL import Image

        test_img = Image.new("RGB", (2000, 1000), color="blue")
        mock_grab.return_value = test_img

        data = p._take_screenshot()
        assert data is not None
        assert p._last_screenshot_hash


def test_save_config_roundtrip():
    p = _make_plugin()
    p.config.save_config = MagicMock()
    p._interval = 300
    p._quiet_start, p._quiet_end = 1, 2
    p._target_umo = "default:FriendMessage:123"
    p._save_config()
    assert p.config["interval"] == 300
    assert p.config["quiet_time"] == "1-2"
    assert p.config["target_umo"] == "default:FriendMessage:123"
    assert p.config.save_config.called


def test_save_screenshot_cleanup():
    import os
    import tempfile

    p = _make_plugin()
    p._max_screenshots = 2
    with tempfile.TemporaryDirectory() as tmp:
        p._screenshot_dir = tmp
        for i in range(3):
            with open(os.path.join(tmp, f"screenshot_{i}.png"), "wb") as f:
                f.write(b"x" * 10)
        p._save_screenshot(b"y" * 10)
        assert p._get_screenshot_count() <= 2

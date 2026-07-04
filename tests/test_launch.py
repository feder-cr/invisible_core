import json
from pathlib import Path
from invisible_core.launch import write_user_js, build_launch_env


def test_write_user_js_emits_user_pref_lines(tmp_path):
    prefs = {"intl.accept_languages": "it-IT, it", "network.proxy.type": 1, "stealthfox.humanize": True}
    out = write_user_js(tmp_path / "prof", prefs)
    assert out == tmp_path / "prof" / "user.js"
    lines = out.read_text(encoding="utf-8").splitlines()
    assert 'user_pref("intl.accept_languages", "it-IT, it");' in lines
    assert 'user_pref("network.proxy.type", 1);' in lines
    assert 'user_pref("stealthfox.humanize", true);' in lines


def test_write_user_js_creates_dir_and_overwrites(tmp_path):
    d = tmp_path / "nested" / "prof"
    write_user_js(d, {"a": 1})
    write_user_js(d, {"b": 2})
    text = (d / "user.js").read_text(encoding="utf-8")
    assert 'user_pref("b", 2);' in text
    assert "a" not in text  # overwritten, not appended


def test_build_launch_env_sets_font_and_webrtc():
    prefs = {"zoom.stealth.font.fontlist": "Arial,Calibri", "zoom.stealth.font.system_ui": "Segoe UI"}
    env = build_launch_env(prefs, timezone="America/New_York", egress_ip="1.2.3.4", base_env={})
    assert env["STEALTHFOX_FONTLIST"] == "Arial,Calibri"
    assert env["STEALTHFOX_SYSTEMUI"] == "Segoe UI"
    assert env["STEALTHFOX_WEBRTC_PUBLIC_IP"] == "1.2.3.4"
    assert env["STEALTHFOX_WEBRTC_DISABLE_IPV6"] == "1"
    assert env["TZ"]  # a POSIX TZ string was set


def test_build_launch_env_no_proxy_no_webrtc_no_tz():
    env = build_launch_env({}, base_env={})
    assert "STEALTHFOX_WEBRTC_PUBLIC_IP" not in env
    assert "TZ" not in env


def test_build_launch_env_caller_webrtc_wins():
    env = build_launch_env({}, egress_ip="9.9.9.9", base_env={"STEALTHFOX_WEBRTC_PUBLIC_IP": "1.1.1.1"})
    assert env["STEALTHFOX_WEBRTC_PUBLIC_IP"] == "1.1.1.1"

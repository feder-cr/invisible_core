import json
from pathlib import Path
from invisible_core.launch import write_user_js


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

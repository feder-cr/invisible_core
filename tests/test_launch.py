import json
from pathlib import Path
from invisible_core.launch import write_user_js, build_launch_env, build_launch_plan


def test_write_user_js_float_is_quoted(tmp_path):
    # Firefox has no float pref type; a fractional value must be a STRING or the
    # user.js fails to parse from that line on. Regression: device-pixel-ratio
    # 1.25 was emitted as a bare `1.25` -> "prefs parse error: unexpected char".
    out = write_user_js(tmp_path / "p", {"zoom.stealth.screen.dpr": 1.25})
    assert out.read_text(encoding="utf-8").strip() == 'user_pref("zoom.stealth.screen.dpr", "1.25");'


def test_build_launch_plan_writes_userjs_env_and_argv(tmp_path, monkeypatch):
    import invisible_core.download as _dl
    import invisible_core._geo as _geo
    import invisible_core._fpforge as _fp
    import invisible_core.prefs as _prefs
    import invisible_core._proxy as _proxy
    from invisible_core._geo import SessionGeo

    monkeypatch.setattr(_dl, "ensure_binary", lambda ver=None: "/fake/firefox")
    monkeypatch.setattr(_geo, "prepare_session_geo",
                        lambda tz, proxy: SessionGeo("America/New_York", "198.51.100.4"))
    monkeypatch.setattr(_geo, "resolve_session_locale", lambda ip, proxy: "en-US")
    monkeypatch.setattr(_fp, "generate_profile", lambda seed, pin=None: object())
    # include a float pref to exercise the serialization end to end
    monkeypatch.setattr(_prefs, "translate_profile_to_prefs",
                        lambda fp, **kw: {"zoom.stealth.screen.dpr": 1.25})
    monkeypatch.setattr(_proxy, "configure_proxy", lambda proxy, prefs: None)

    pdir = tmp_path / "p"
    plan = build_launch_plan(42, profile_dir=pdir, timezone="auto", locale="auto")

    assert plan.binary == "/fake/firefox"
    # ⛔ NO URL AT THE END, and it is the class that matters, not the string: a
    # URL on the command line takes precedence over the startup page, so the
    # "about:blank" default that used to sit here suppressed about:home
    # regardless of the prefs. Removed 2026-08-20 with the newtab revert.
    assert plan.argv == ["/fake/firefox", "-no-remote", "-profile", str(pdir)]
    # And whoever wants one passes it: the parameter is still there.
    explicit = build_launch_plan(42, profile_dir=pdir, timezone="auto",
                                 locale="auto", url="https://example.invalid/")
    assert explicit.argv[-1] == "https://example.invalid/"
    text = (pdir / "user.js").read_text(encoding="utf-8")
    assert 'user_pref("zoom.stealth.screen.dpr", "1.25");' in text            # float -> string
    assert 'user_pref("toolkit.startup.max_resumed_crashes", -1);' in text    # no Safe Mode prompt
    assert 'user_pref("browser.sessionstore.resume_from_crash", false);' in text
    assert plan.env["STEALTHFOX_WEBRTC_PUBLIC_IP"] == "198.51.100.4"


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


def test_build_launch_env_no_font_env_but_webrtc():
    # Fonts are handled entirely in the binary (always bundle-only, self-contained),
    # so build_launch_env must NOT set any STEALTHFOX_FONTLIST/SYSTEMUI env - even
    # when legacy font prefs are passed. WebRTC egress + TZ are still wired.
    prefs = {"zoom.stealth.font.fontlist": "Arial,Calibri", "zoom.stealth.font.system_ui": "Segoe UI"}
    env = build_launch_env(prefs, timezone="America/New_York", srflx_declared="198.51.100.4", base_env={})
    assert "STEALTHFOX_FONTLIST" not in env
    assert "STEALTHFOX_SYSTEMUI" not in env
    assert env["STEALTHFOX_WEBRTC_PUBLIC_IP"] == "198.51.100.4"
    assert env["STEALTHFOX_WEBRTC_DISABLE_IPV6"] == "1"
    assert env["TZ"]  # a POSIX TZ string was set


def test_build_launch_env_applies_the_surface_env_over_a_cleared_slot():
    """The hidden surface's ``launch_env()`` is applied HERE, last: variables
    to set, and - with ``None`` - variables the browser must not carry; the
    desktop slot is cleared first whatever the caller's environment says.
    Until 34.26.0 only the wrapper's twin knew this half of the contract, so a
    consumer on this builder could not express a removal. The known-bad input
    is an ``update()`` of the dict: it would keep ``WAYLAND_DISPLAY``, hand a
    ``None`` to the subprocess and let a stale desktop name through."""
    base = {"PATH": "/usr/bin", "WAYLAND_DISPLAY": "wayland-0",
            "INVPW_DESKTOP": "invpw_stale", "DISPLAY": ":0"}
    env = build_launch_env({}, base_env=base,
                           display_env={"DISPLAY": ":123", "GDK_BACKEND": "x11",
                                        "WAYLAND_DISPLAY": None})
    assert env["DISPLAY"] == ":123"
    assert env["GDK_BACKEND"] == "x11"
    assert "WAYLAND_DISPLAY" not in env
    assert "INVPW_DESKTOP" not in env, "a stale desktop name reached the browser"
    assert all(v is not None for v in env.values())
    assert base["WAYLAND_DISPLAY"] == "wayland-0", "the caller's mapping was written into"

    windows = build_launch_env({}, base_env={"INVPW_DESKTOP": "invpw_stale"},
                               display_env={"INVPW_DESKTOP": "invpw_fresh"})
    assert windows["INVPW_DESKTOP"] == "invpw_fresh"
    assert "INVPW_DESKTOP" not in build_launch_env({}, base_env={"INVPW_DESKTOP": "x"})


def test_build_launch_env_no_proxy_no_webrtc_no_tz():
    env = build_launch_env({}, base_env={})
    assert "STEALTHFOX_WEBRTC_PUBLIC_IP" not in env
    assert "TZ" not in env


def test_build_launch_env_caller_webrtc_wins():
    env = build_launch_env({}, srflx_declared="203.0.113.9", base_env={"STEALTHFOX_WEBRTC_PUBLIC_IP": "198.51.100.1"})
    assert env["STEALTHFOX_WEBRTC_PUBLIC_IP"] == "198.51.100.1"

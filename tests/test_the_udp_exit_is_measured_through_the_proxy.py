"""The UDP exit of a SOCKS5 proxy is MEASURED by the core and declared WITH the proxy.

A page behind a proxy that grants SOCKS5 UDP ASSOCIATE but sends the UDP out of an
address other than the TCP exit sees srflx != the address of its HTTP: the classic
leak. The core already discovers the TCP exit through the proxy (IP, timezone, city);
`invisible_core._udp_exit` also measures the UDP exit through the same proxy (UDP
ASSOCIATE, then a STUN Binding request with ICE's retransmissions) and the answer
travels in the proxy's declaration on the pipe, as the optional field ``udp`` of
``Browser.setBrowserProxy`` / ``Browser.setContextProxy``:

    coherent    a Binding response came back and its mapped address is the TCP exit
    no          the proxy refused UDP, or the UDP leaves from another address
    unmeasured  nothing came back within the budget, or an error before the answer
    (absent)    not a SOCKS5 proxy: nothing was measured; the engine reads it as `no`

Hermetic: a fake SOCKS5 gateway with a UDP relay and STUN responders on loopback
(`_udp_gateway.py`); the real STUN servers are constants the test replaces through
the `udp_stun_servers` parameter of the launch.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import sys
import time

import pytest

from _udp_gateway import FakeStun, UdpGateway

from invisible_core import _geo, _udp_exit
from invisible_core._udp_exit import measure_udp_exit

pytestmark = pytest.mark.unit

EXIT = "203.0.113.7"
OTHER = "198.51.100.4"
#: A short schedule and budget, the same shape as the real ones (0, 500, 1000 ms in 3 s).
FAST = dict(schedule=(0.0, 0.15, 0.3), budget=1.2)


@pytest.fixture
def stuns():
    made = []

    def make(mapped_ip=EXIT, count=2, **kw):
        for _ in range(count):
            made.append(FakeStun(mapped_ip, **kw))
        return made[-count:]

    yield make
    for s in made:
        s.close()


def _addrs(stun_list):
    """The servers in the form the launch parameter takes: "ip:port" strings."""
    return ["%s:%d" % s.address for s in stun_list]


# (1) the same address for UDP and TCP ----------------------------------------------------------
def test_the_udp_that_leaves_from_the_tcp_exit_is_coherent(stuns):
    servers = stuns()
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(servers), **FAST) == "coherent"
        assert gw.associates == 1
    # The association lived as long as the exchange: the control connection was open when the datagrams arrived.
    assert gw.control_open_during_udp and all(gw.control_open_during_udp)


def test_a_plain_mapped_address_is_read_when_there_is_no_xor_one(stuns):
    servers = stuns(mode="plain")
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(servers), **FAST) == "coherent"


def test_a_relay_that_names_no_address_is_reached_at_the_address_of_the_proxy(stuns):
    servers = stuns()
    with UdpGateway(unspecified_bnd=True) as gw:
        assert measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(servers), **FAST) == "coherent"


def test_the_measurement_asks_the_servers_by_number_with_a_binding_request_and_no_attributes(stuns):
    servers = stuns()
    with UdpGateway() as gw:
        measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(servers), **FAST)
    asked = {(h, p) for h, p, _ in gw.datagrams}
    assert asked == {s.address for s in servers}
    for _h, _p, payload in gw.datagrams:
        assert len(payload) == 20 and payload[:2] == b"\x00\x01" and payload[2:4] == b"\x00\x00"
        assert payload[4:8] == b"\x21\x12\xa4\x42"
    txids = {p[8:20] for _, _, p in gw.datagrams}
    assert len(txids) == 2, "one random 96-bit transaction id per server, kept across retransmissions"


# (2) a different mapped address ----------------------------------------------------------------
def test_a_udp_that_leaves_from_another_address_is_no(stuns):
    servers = stuns(OTHER)
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(servers), **FAST) == "no"


def test_an_ipv4_mapped_answer_against_an_ipv6_exit_is_no(stuns):
    servers = stuns(EXIT)
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, "2001:db8::7", udp_stun_servers=_addrs(servers), **FAST) == "no"


def test_the_comparison_is_on_the_normalised_address(stuns):
    servers = stuns(EXIT)
    with UdpGateway() as gw:
        # Compared as addresses (after `ipaddress` normalisation), not as text.
        assert measure_udp_exit(gw.proxy, " 203.0.113.7 ", udp_stun_servers=_addrs(servers), **FAST) == "coherent"


# (3) lost datagrams ----------------------------------------------------------------------------
@pytest.mark.parametrize("lost", [1, 2])
def test_a_lost_first_datagram_still_gives_coherent(stuns, lost):
    servers = stuns()
    with UdpGateway(drop_first=lost) as gw:
        assert measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(servers), **FAST) == "coherent"
        sent = len(gw.datagrams)
    assert sent >= lost + 1, "the answer came from a retransmission"


def test_three_lost_datagrams_within_the_budget_is_unmeasured(stuns):
    servers = stuns()
    with UdpGateway(drop_first=3) as gw:
        assert measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(servers), **FAST) == "unmeasured"
        # Exactly 3 transmissions to each of the two servers.
        counts = {}
        for h, p, _ in gw.datagrams:
            counts[(h, p)] = counts.get((h, p), 0) + 1
    assert sorted(counts.values()) == [3, 3], counts


def test_one_server_that_is_down_does_not_spoil_the_other(stuns):
    dead = stuns(count=1, answer=False)
    alive = stuns(count=1)
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(dead + alive), **FAST) == "coherent"


def test_an_answer_with_another_transaction_id_is_not_an_answer(stuns):
    servers = stuns(wrong_txid=True)
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(servers), **FAST) == "unmeasured"


# (4) refusals ----------------------------------------------------------------------------------
def test_an_associate_refused_with_command_not_supported_is_no(stuns):
    servers = stuns()
    with UdpGateway(associate_reply=7) as gw:
        assert measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(servers), **FAST) == "no"
        assert gw.datagrams == [], "nothing is sent to a relay that was never granted"


def test_a_proxy_that_refuses_the_method_is_no(stuns):
    servers = stuns()
    with UdpGateway(user="somebody", password="pw") as gw:
        # The same gateway, asked WITHOUT the credentials it demands: it answers 0xFF.
        bare = {"server": gw.proxy["server"]}
        assert measure_udp_exit(bare, EXIT, udp_stun_servers=_addrs(servers), **FAST) == "no"


def test_credentials_the_proxy_does_not_accept_are_no(stuns):
    servers = stuns()
    with UdpGateway(user="somebody", password="pw") as gw:
        wrong = dict(gw.proxy, password="not-it")
        assert measure_udp_exit(wrong, EXIT, udp_stun_servers=_addrs(servers), **FAST) == "no"


# (5) silence -----------------------------------------------------------------------------------
def test_no_answer_at_all_is_unmeasured_inside_the_budget(stuns):
    servers = stuns()
    budget = 0.6
    with UdpGateway(silent=True) as gw:
        started = time.monotonic()
        verdict = measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(servers),
                                   schedule=(0.0, 0.1, 0.2), budget=budget)
        elapsed = time.monotonic() - started
    assert verdict == "unmeasured"
    assert budget * 0.9 <= elapsed < budget + 0.4, elapsed


def test_a_proxy_nothing_listens_at_is_unmeasured_inside_the_budget():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    started = time.monotonic()
    assert measure_udp_exit({"server": "socks5://127.0.0.1:%d" % port}, EXIT,
                            udp_stun_servers=["127.0.0.1:9"], **FAST) == "unmeasured"
    # (Windows retries a refused loopback connect for about a second: the budget is the bound, not instantly.)
    assert time.monotonic() - started < FAST["budget"] + 0.4


def test_the_real_budget_is_three_seconds_and_the_schedule_is_ices():
    assert _udp_exit.BUDGET == 3.0
    assert _udp_exit.SCHEDULE == (0.0, 0.5, 1.0)


# (6) only SOCKS5 -------------------------------------------------------------------------------
@pytest.mark.parametrize("scheme", ["http", "https", "socks4"])
def test_a_proxy_that_is_not_socks5_is_not_measured_and_has_no_field(stuns, scheme):
    servers = stuns()
    with UdpGateway() as gw:
        proxy = {"server": "%s://127.0.0.1:%d" % (scheme, gw.port)}
        assert measure_udp_exit(proxy, EXIT, udp_stun_servers=_addrs(servers), **FAST) is None
        assert gw.accepts == 0, "not even a connection was opened"


def test_the_pipe_command_of_a_proxy_that_was_not_measured_has_no_udp_field():
    for server in ("http://10.0.0.1:8080", "https://10.0.0.1:8080", "socks4://10.0.0.1:1080"):
        assert "udp" not in _udp_exit.launch_proxy_command({"server": server})
        assert "udp" not in _udp_exit.context_proxy_command({"server": server, "udp": "coherent"})


# (7) the credentials ---------------------------------------------------------------------------
def test_the_credentials_go_on_the_socket_only(stuns, caplog, monkeypatch):
    servers = stuns()
    user, password = "u-4f1c9a7e2b", "p-8d03e65a19"
    env_before = dict(os.environ)
    with UdpGateway(user=user, password=password) as gw:
        with caplog.at_level(logging.DEBUG):
            assert measure_udp_exit(gw.proxy, EXIT, udp_stun_servers=_addrs(servers), **FAST) == "coherent"
        assert gw.credentials_seen == [(user, password)]
    text = "\n".join(r.getMessage() + " " + repr(r.args) + " " + str(r.exc_info) for r in caplog.records)
    assert user not in text and password not in text
    assert dict(os.environ) == env_before
    assert not [a for a in sys.argv if user in a or password in a]


def test_a_failure_does_not_put_the_credentials_in_the_log(caplog):
    user, password = "u-0a1b2c3d4e", "p-5f6a7b8c9d"
    proxy = {"server": "socks5://127.0.0.1:1", "username": user, "password": password}
    with caplog.at_level(logging.DEBUG):
        measure_udp_exit(proxy, EXIT, udp_stun_servers=["127.0.0.1:9"], **FAST)
    text = "\n".join(r.getMessage() + " " + repr(r.args) + " " + str(r.exc_info) for r in caplog.records)
    assert user not in text and password not in text


# the servers: an explicit parameter of the launch ----------------------------------------------------------
def test_the_default_servers_are_one_constant_of_two_ipv4_literals_with_a_port():
    from invisible_core._udp_exit import DEFAULT_STUN_SERVERS
    assert DEFAULT_STUN_SERVERS == ("141.101.90.1:3478", "162.159.207.1:3478")
    for entry in DEFAULT_STUN_SERVERS:
        host, _, port = entry.rpartition(":")
        assert ipaddress.ip_address(host).version == 4 and 0 < int(port) < 65536
    # The form the launch parameter takes: the constant can be passed back as it is.
    assert _udp_exit.stun_servers(DEFAULT_STUN_SERVERS) == _udp_exit.stun_servers(None)


def test_no_operator_name_or_documentation_address_is_written_in_the_module_or_its_tests():
    import pathlib
    here = pathlib.Path(__file__).resolve()
    for path in (pathlib.Path(_udp_exit.__file__), here, here.with_name("_udp_gateway.py")):
        text = path.read_text(encoding="utf-8").lower()
        assert "cloud" + "flare" not in text, path.name
        assert "developers" + "." not in text, path.name


def test_the_servers_are_validated_and_a_bad_one_names_the_parameter():
    assert _udp_exit.stun_servers(["127.0.0.1:3478", "10.9.8.7:19302"]) == (("127.0.0.1", 3478), ("10.9.8.7", 19302))
    assert _udp_exit.stun_servers([("127.0.0.1", 3478)]) == (("127.0.0.1", 3478),)
    for bad in (["stun.example.org:3478"], ["127.0.0.1"], ["127.0.0.1:0"], ["::1:3478"], ["127.0.0.1:70000"],
                [], "127.0.0.1:3478", [None]):
        with pytest.raises(ValueError, match="udp_stun_servers"):
            _udp_exit.stun_servers(bad)


def test_every_entry_point_of_the_launch_refuses_a_bad_server_list():
    from invisible_core import get_default_stealth_prefs, prepare_session_geo
    from invisible_core.launch import build_launch_plan
    with pytest.raises(ValueError, match="udp_stun_servers"):
        prepare_session_geo("Asia/Tokyo", None, udp_stun_servers=["stun.example.org:3478"])
    with pytest.raises(ValueError, match="udp_stun_servers"):
        get_default_stealth_prefs(1, udp_stun_servers=["127.0.0.1:99999"])
    with pytest.raises(ValueError, match="udp_stun_servers"):
        build_launch_plan(1, profile_dir="unused", binary_path="x", udp_stun_servers=["name:1"])


def test_the_environment_is_not_a_way_to_name_the_servers(stuns, monkeypatch):
    """No variable declares them: one set in the process is not read."""
    from invisible_core import environment
    assert not [n for n in environment.READ if "STUN" in n]
    servers = stuns(OTHER)
    monkeypatch.setenv("STEALTHFOX_UDP_STUN_SERVERS", ",".join(_addrs(servers)))
    assert _udp_exit.stun_servers(None) == _udp_exit.stun_servers(_udp_exit.DEFAULT_STUN_SERVERS)


# (8) the launch: prepare_session_geo measures, the first pipe command carries it ------------------
@pytest.fixture
def declared(stuns, monkeypatch):
    """A fake STUN pair that maps to `mapped_ip`, as the launch parameter takes it; the TCP exit is EXIT."""
    monkeypatch.setattr(_geo, "discover_egress_ip", lambda proxy=None, **kw: EXIT)
    return lambda mapped_ip: _addrs(stuns(mapped_ip))


def test_the_launch_geo_step_measures_the_udp_exit(declared):
    from invisible_core import prepare_session_geo
    with UdpGateway() as gw:
        assert prepare_session_geo("Asia/Tokyo", gw.proxy, udp_stun_servers=declared(EXIT)).udp_exit == "coherent"


def test_the_launch_geo_step_reports_a_udp_exit_that_differs(declared):
    from invisible_core import prepare_session_geo
    with UdpGateway() as gw:
        assert prepare_session_geo("Asia/Tokyo", gw.proxy, udp_stun_servers=declared(OTHER)).udp_exit == "no"


def test_the_launch_geo_step_measures_nothing_without_a_socks5_proxy(declared):
    from invisible_core import prepare_session_geo
    servers = declared(EXIT)
    assert prepare_session_geo("Asia/Tokyo", None, udp_stun_servers=servers).udp_exit is None
    assert prepare_session_geo("Asia/Tokyo", {"server": "http://127.0.0.1:9"}, udp_stun_servers=servers).udp_exit is None


def test_the_launch_geo_step_does_not_wait_for_the_measurement_after_the_tcp_discovery(declared, monkeypatch):
    """The two run side by side: a TCP discovery of 0.8 s and a silent UDP of 0.8 s cost 0.8 s, not 1.6."""
    from invisible_core import prepare_session_geo

    def slow(proxy=None, **kw):
        time.sleep(0.8)
        return EXIT

    monkeypatch.setattr(_geo, "discover_egress_ip", slow)
    monkeypatch.setattr(_udp_exit, "BUDGET", 0.8)
    monkeypatch.setattr(_udp_exit, "SCHEDULE", (0.0, 0.1, 0.2))
    servers = declared(EXIT)
    with UdpGateway(silent=True) as gw:
        started = time.monotonic()
        geo = prepare_session_geo("Asia/Tokyo", gw.proxy, udp_stun_servers=servers)
        elapsed = time.monotonic() - started
    assert geo.udp_exit == "unmeasured"
    assert elapsed < 1.3, elapsed


def test_a_failed_tcp_discovery_leaves_the_udp_unmeasured_but_a_refusal_is_still_no(declared, monkeypatch):
    from invisible_core import prepare_session_geo

    def boom(proxy=None, **kw):
        raise _geo.GeoTimezoneError("no egress")

    monkeypatch.setattr(_geo, "discover_egress_ip", boom)
    servers = declared(EXIT)
    with UdpGateway() as gw:
        assert prepare_session_geo("Asia/Tokyo", gw.proxy, udp_stun_servers=servers).udp_exit == "unmeasured"
    with UdpGateway(associate_reply=7) as gw:
        assert prepare_session_geo("Asia/Tokyo", gw.proxy, udp_stun_servers=servers).udp_exit == "no"


def _first_pipe_command(monkeypatch, command):
    """Launch against the fake browser with `command` as the proxy command; return the browser, with what the
    process was given (argv, environment) in `.seen`."""
    from invisible_core.juggler import connection
    from test_a_launch_has_one_deadline import _Browser
    browser = _Browser()
    seen = {}
    real_spawn = browser.spawn

    def spawn(executable, argv, env):
        seen["argv"], seen["env"] = list(argv), dict(env)
        return real_spawn(executable, argv, env)

    monkeypatch.setattr(connection, "_spawn_posix", spawn)
    monkeypatch.setattr(connection, "_spawn_windows", spawn)
    c = connection.launch("firefox", "profile", ready_timeout=3.0, proxy=command)
    try:
        c.startup_send("Browser.enable", {})
    finally:
        c.close()
    browser.seen = seen
    return browser


def test_the_first_pipe_command_of_a_socks5_launch_carries_the_measure(declared, monkeypatch):
    from invisible_core import prepare_session_geo
    for mapped, expected in ((EXIT, "coherent"), (OTHER, "no")):
        servers = declared(mapped)
        with UdpGateway() as gw:
            prepare_session_geo("Asia/Tokyo", gw.proxy, udp_stun_servers=servers)
            browser = _first_pipe_command(monkeypatch, _udp_exit.launch_proxy_command(gw.proxy, servers))
        assert browser.methods[:2] == ["Browser.setBrowserProxy", "Browser.enable"]
        assert browser.params["Browser.setBrowserProxy"]["udp"] == expected


def test_a_socks5_launch_nothing_measured_says_unmeasured(monkeypatch):
    browser = _first_pipe_command(monkeypatch, _udp_exit.launch_proxy_command({"server": "socks5://10.0.0.1:1080"}))
    assert browser.params["Browser.setBrowserProxy"]["udp"] == "unmeasured"


def test_a_session_without_a_proxy_carries_no_udp(monkeypatch):
    browser = _first_pipe_command(monkeypatch, None)
    assert browser.methods[:2] == ["Browser.setNoBrowserProxy", "Browser.enable"]
    assert browser.params["Browser.setNoBrowserProxy"] in (None, {})


def test_no_stun_address_reaches_the_browser(declared, monkeypatch, tmp_path):
    """The STUN list is a parameter of the Python launch, not a browser declaration: the pipe commands, the
    environment, the argv and the profile the core writes hold none of it."""
    from invisible_core import prepare_session_geo
    from invisible_core.launch import build_launch_plan
    servers = declared(EXIT)
    needles = list(servers)        # "ip:port" (the proxy is on the same loopback address, so the bare IP proves nothing)
    with UdpGateway() as gw:
        prepare_session_geo("Asia/Tokyo", gw.proxy, udp_stun_servers=servers)
        browser = _first_pipe_command(monkeypatch, _udp_exit.launch_proxy_command(gw.proxy, servers))
    dumped = repr(browser.params) + repr(browser.seen)
    assert not [n for n in needles if n in dumped], "an address of the STUN list reached the pipe or the process"
    assert not [k for k in browser.seen["env"] if "STUN" in k.upper()]
    plan = build_launch_plan(1, profile_dir=tmp_path, binary_path="firefox", timezone="Asia/Tokyo", locale="en-US",
                             udp_stun_servers=servers)
    dumped = repr((plan.argv, plan.env)) + (tmp_path / "user.js").read_text(encoding="utf-8")
    assert not [n for n in needles if n in dumped], "an address of the STUN list reached the plan"
    assert not [k for k in plan.env if "STUN" in k.upper()]


def test_parse_proxy_never_reads_a_udp_key():
    from invisible_core._proxy import parse_proxy
    for value in ("coherent", "no", "unmeasured", "yes", 7):
        endpoint = parse_proxy({"server": "socks5://10.0.0.1:1080", "udp": value})
        assert endpoint.udp is None
        assert "udp" not in endpoint.as_engine_command()


# the caller never declares udp: the only source is the measurement -----------------------------------------
def test_a_launch_proxy_written_with_udp_coherent_carries_the_measure_not_the_claim(declared, monkeypatch):
    from invisible_core import prepare_session_geo
    servers = declared(OTHER)                      # the UDP exits elsewhere than the TCP
    with UdpGateway() as gw:
        prepare_session_geo("Asia/Tokyo", gw.proxy, udp_stun_servers=servers)
        browser = _first_pipe_command(
            monkeypatch, _udp_exit.launch_proxy_command(dict(gw.proxy, udp="coherent"), servers))
    assert browser.params["Browser.setBrowserProxy"]["udp"] == "no"


def test_a_launch_proxy_written_with_udp_coherent_and_never_measured_is_unmeasured(monkeypatch):
    browser = _first_pipe_command(
        monkeypatch, _udp_exit.launch_proxy_command({"server": "socks5://10.0.0.9:1080", "udp": "coherent"}))
    assert browser.params["Browser.setBrowserProxy"]["udp"] == "unmeasured"


def test_a_context_proxy_written_with_udp_coherent_carries_the_measure_not_the_claim(declared):
    import types
    from invisible_core.juggler.instance import Browser
    sent = []
    fake = types.SimpleNamespace(ENGINE_OPTIONS=Browser.ENGINE_OPTIONS, udp_stun_servers=declared(OTHER),
                                 context_send=lambda method, params, **kw: sent.append((method, params)))
    with UdpGateway() as gw:
        Browser.apply_context_options(fake, "ctx-1", {"proxy": dict(gw.proxy, udp="coherent")})
    (method, params), = [x for x in sent if x[0] == "Browser.setContextProxy"]
    assert params["udp"] == "no"


# the context proxy, the core's own client -----------------------------------------------------------
def test_a_context_proxy_of_the_core_client_is_measured_before_it_is_sent(declared):
    import types
    from invisible_core.juggler.instance import Browser
    sent = []
    fake = types.SimpleNamespace(ENGINE_OPTIONS=Browser.ENGINE_OPTIONS, udp_stun_servers=declared(EXIT),
                                 context_send=lambda method, params, **kw: sent.append((method, params)))
    with UdpGateway() as gw:
        Browser.apply_context_options(fake, "ctx-1", {"proxy": gw.proxy})
    (method, params), = [s for s in sent if s[0] == "Browser.setContextProxy"]
    assert params["udp"] == "coherent" and params["browserContextId"] == "ctx-1"
    assert params["type"] == "socks"


def test_the_core_client_keeps_the_launch_servers_for_its_contexts():
    import inspect
    from invisible_core.juggler import instance
    assert "udp_stun_servers" in inspect.signature(instance.launch).parameters
    assert "udp_stun_servers" in inspect.signature(instance.Browser.__init__).parameters


def test_one_proxy_is_measured_once_per_session_of_calls(declared):
    servers = declared(EXIT)
    with UdpGateway() as gw:
        first = _udp_exit.measure_proxy_udp(gw.proxy, servers)
        second = _udp_exit.measure_proxy_udp(dict(gw.proxy), servers)
        assert first == second == "coherent"
        assert gw.associates == 1, "cached by endpoint, a hash of the credentials and the servers"


def test_the_cache_key_is_not_the_secret():
    from invisible_core._proxy import parse_proxy
    key = _udp_exit.cache_key(parse_proxy({"server": "socks5://h:1", "username": "a", "password": "hunter2-xyz"}))
    assert "hunter2-xyz" not in repr(key)
    other = _udp_exit.cache_key(parse_proxy({"server": "socks5://h:1", "username": "a", "password": "other"}))
    assert key != other


# the launch of a client that composed no session ----------------------------------------------------------
def test_a_launch_proxy_gets_what_the_geo_step_remembered_and_asks_no_network(declared):
    from invisible_core import prepare_session_geo
    servers = declared(EXIT)
    with UdpGateway() as gw:
        assert prepare_session_geo("Asia/Tokyo", gw.proxy, udp_stun_servers=servers).udp_exit == "coherent"
        before = gw.associates
        assert _udp_exit.launch_proxy_command(dict(gw.proxy), servers)["udp"] == "coherent"
        assert gw.associates == before, "the network was not asked a second time"

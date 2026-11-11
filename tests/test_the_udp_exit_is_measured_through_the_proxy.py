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
the `servers` parameter or the declaration ``STEALTHFOX_UDP_STUN_SERVERS``.
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
    return [s.address for s in stun_list]


# (1) the same address for UDP and TCP ----------------------------------------------------------
def test_the_udp_that_leaves_from_the_tcp_exit_is_coherent(stuns):
    servers = stuns()
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, EXIT, servers=_addrs(servers), **FAST) == "coherent"
        assert gw.associates == 1
    # The association lived as long as the exchange: the control connection was open when the datagrams arrived.
    assert gw.control_open_during_udp and all(gw.control_open_during_udp)


def test_a_plain_mapped_address_is_read_when_there_is_no_xor_one(stuns):
    servers = stuns(mode="plain")
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, EXIT, servers=_addrs(servers), **FAST) == "coherent"


def test_a_relay_that_names_no_address_is_reached_at_the_address_of_the_proxy(stuns):
    servers = stuns()
    with UdpGateway(unspecified_bnd=True) as gw:
        assert measure_udp_exit(gw.proxy, EXIT, servers=_addrs(servers), **FAST) == "coherent"


def test_the_measurement_asks_the_servers_by_number_with_a_binding_request_and_no_attributes(stuns):
    servers = stuns()
    with UdpGateway() as gw:
        measure_udp_exit(gw.proxy, EXIT, servers=_addrs(servers), **FAST)
    asked = {(h, p) for h, p, _ in gw.datagrams}
    assert asked == set(_addrs(servers))
    for _h, _p, payload in gw.datagrams:
        assert len(payload) == 20 and payload[:2] == b"\x00\x01" and payload[2:4] == b"\x00\x00"
        assert payload[4:8] == b"\x21\x12\xa4\x42"
    txids = {p[8:20] for _, _, p in gw.datagrams}
    assert len(txids) == 2, "one random 96-bit transaction id per server, kept across retransmissions"


# (2) a different mapped address ----------------------------------------------------------------
def test_a_udp_that_leaves_from_another_address_is_no(stuns):
    servers = stuns(OTHER)
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, EXIT, servers=_addrs(servers), **FAST) == "no"


def test_an_ipv4_mapped_answer_against_an_ipv6_exit_is_no(stuns):
    servers = stuns(EXIT)
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, "2001:db8::7", servers=_addrs(servers), **FAST) == "no"


def test_the_comparison_is_on_the_normalised_address(stuns):
    servers = stuns(EXIT)
    with UdpGateway() as gw:
        # Compared as addresses (after `ipaddress` normalisation), not as text.
        assert measure_udp_exit(gw.proxy, " 203.0.113.7 ", servers=_addrs(servers), **FAST) == "coherent"


# (3) lost datagrams ----------------------------------------------------------------------------
@pytest.mark.parametrize("lost", [1, 2])
def test_a_lost_first_datagram_still_gives_coherent(stuns, lost):
    servers = stuns()
    with UdpGateway(drop_first=lost) as gw:
        assert measure_udp_exit(gw.proxy, EXIT, servers=_addrs(servers), **FAST) == "coherent"
        sent = len(gw.datagrams)
    assert sent >= lost + 1, "the answer came from a retransmission"


def test_three_lost_datagrams_within_the_budget_is_unmeasured(stuns):
    servers = stuns()
    with UdpGateway(drop_first=3) as gw:
        assert measure_udp_exit(gw.proxy, EXIT, servers=_addrs(servers), **FAST) == "unmeasured"
        # Exactly 3 transmissions to each of the two servers.
        counts = {}
        for h, p, _ in gw.datagrams:
            counts[(h, p)] = counts.get((h, p), 0) + 1
    assert sorted(counts.values()) == [3, 3], counts


def test_one_server_that_is_down_does_not_spoil_the_other(stuns):
    dead = stuns(count=1, answer=False)
    alive = stuns(count=1)
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, EXIT, servers=_addrs(dead + alive), **FAST) == "coherent"


def test_an_answer_with_another_transaction_id_is_not_an_answer(stuns):
    servers = stuns(wrong_txid=True)
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, EXIT, servers=_addrs(servers), **FAST) == "unmeasured"


# (4) refusals ----------------------------------------------------------------------------------
def test_an_associate_refused_with_command_not_supported_is_no(stuns):
    servers = stuns()
    with UdpGateway(associate_reply=7) as gw:
        assert measure_udp_exit(gw.proxy, EXIT, servers=_addrs(servers), **FAST) == "no"
        assert gw.datagrams == [], "nothing is sent to a relay that was never granted"


def test_a_proxy_that_refuses_the_method_is_no(stuns):
    servers = stuns()
    with UdpGateway(user="somebody", password="pw") as gw:
        # The same gateway, asked WITHOUT the credentials it demands: it answers 0xFF.
        bare = {"server": gw.proxy["server"]}
        assert measure_udp_exit(bare, EXIT, servers=_addrs(servers), **FAST) == "no"


def test_credentials_the_proxy_does_not_accept_are_no(stuns):
    servers = stuns()
    with UdpGateway(user="somebody", password="pw") as gw:
        wrong = dict(gw.proxy, password="not-it")
        assert measure_udp_exit(wrong, EXIT, servers=_addrs(servers), **FAST) == "no"


# (5) silence -----------------------------------------------------------------------------------
def test_no_answer_at_all_is_unmeasured_inside_the_budget(stuns):
    servers = stuns()
    budget = 0.6
    with UdpGateway(silent=True) as gw:
        started = time.monotonic()
        verdict = measure_udp_exit(gw.proxy, EXIT, servers=_addrs(servers),
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
                            servers=[("127.0.0.1", 9)], **FAST) == "unmeasured"
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
        assert measure_udp_exit(proxy, EXIT, servers=_addrs(servers), **FAST) is None
        assert gw.accepts == 0, "not even a connection was opened"


def test_the_pipe_command_of_a_proxy_that_was_not_measured_has_no_udp_field():
    from invisible_core._proxy import configure_proxy, parse_proxy
    for server in ("http://10.0.0.1:8080", "socks4://10.0.0.1:1080"):
        kept = configure_proxy({"server": server}, {}, udp=None)
        assert "udp" not in parse_proxy(kept).as_engine_command()
        # A value written by hand into the dict of a proxy that is not SOCKS5 does not reach the pipe either.
        forged = parse_proxy(dict(kept, udp="coherent")).as_engine_command()
        assert "udp" not in forged


# (7) the credentials ---------------------------------------------------------------------------
def test_the_credentials_go_on_the_socket_only(stuns, caplog, monkeypatch):
    servers = stuns()
    user, password = "u-4f1c9a7e2b", "p-8d03e65a19"
    env_before = dict(os.environ)
    with UdpGateway(user=user, password=password) as gw:
        with caplog.at_level(logging.DEBUG):
            assert measure_udp_exit(gw.proxy, EXIT, servers=_addrs(servers), **FAST) == "coherent"
        assert gw.credentials_seen == [(user, password)]
    text = "\n".join(r.getMessage() + " " + repr(r.args) + " " + str(r.exc_info) for r in caplog.records)
    assert user not in text and password not in text
    assert dict(os.environ) == env_before
    assert not [a for a in sys.argv if user in a or password in a]


def test_a_failure_does_not_put_the_credentials_in_the_log(caplog):
    user, password = "u-0a1b2c3d4e", "p-5f6a7b8c9d"
    proxy = {"server": "socks5://127.0.0.1:1", "username": user, "password": password}
    with caplog.at_level(logging.DEBUG):
        measure_udp_exit(proxy, EXIT, servers=[("127.0.0.1", 9)], **FAST)
    text = "\n".join(r.getMessage() + " " + repr(r.args) + " " + str(r.exc_info) for r in caplog.records)
    assert user not in text and password not in text


# the servers: a declaration -------------------------------------------------------------------
def test_the_default_servers_are_two_ip_literals_with_their_source():
    assert len(_udp_exit.STUN_SERVERS) == 2
    for host, port in _udp_exit.STUN_SERVERS:
        assert ipaddress.ip_address(host).version == 4, "an IP literal: the core never resolves a name"
        assert 0 < port < 65536
    src = open(_udp_exit.__file__, encoding="utf-8").read()
    assert "2026-11-11" in src and "developers.cloudflare.com/realtime/turn/faq" in src


def test_the_servers_can_be_declared_through_the_environment(monkeypatch):
    from invisible_core import environment
    monkeypatch.delenv(environment.UDP_STUN_SERVERS, raising=False)
    assert _udp_exit.declared_stun_servers() == _udp_exit.STUN_SERVERS
    monkeypatch.setenv(environment.UDP_STUN_SERVERS, "127.0.0.1:3478, 10.9.8.7:19302")
    assert _udp_exit.declared_stun_servers() == (("127.0.0.1", 3478), ("10.9.8.7", 19302))


@pytest.mark.parametrize("bad", ["stun.example.org:3478", "127.0.0.1", "127.0.0.1:0", "::1:3478", "127.0.0.1:70000"])
def test_a_declaration_that_is_not_an_ip_literal_with_a_port_is_refused(monkeypatch, bad):
    from invisible_core import environment
    monkeypatch.setenv(environment.UDP_STUN_SERVERS, bad)
    with pytest.raises(ValueError, match=environment.UDP_STUN_SERVERS):
        _udp_exit.declared_stun_servers()


def test_the_measurement_reads_the_declaration_when_given_no_servers(stuns, monkeypatch):
    from invisible_core import environment
    servers = stuns()
    monkeypatch.setenv(environment.UDP_STUN_SERVERS, ",".join("%s:%d" % a for a in _addrs(servers)))
    with UdpGateway() as gw:
        assert measure_udp_exit(gw.proxy, EXIT, **FAST) == "coherent"


# (8) the launch: prepare_session_geo measures, the first pipe command carries it ------------------
@pytest.fixture
def declared(stuns, monkeypatch):
    """The session's STUN servers declared as loopback fakes, and the TCP exit as EXIT."""
    from invisible_core import environment

    def set_servers(mapped_ip):
        servers = stuns(mapped_ip)
        monkeypatch.setenv(environment.UDP_STUN_SERVERS, ",".join("%s:%d" % a for a in _addrs(servers)))

    monkeypatch.setattr(_geo, "discover_egress_ip", lambda proxy=None, **kw: EXIT)
    return set_servers


def test_the_launch_geo_step_measures_the_udp_exit(declared):
    from invisible_core import prepare_session_geo
    declared(EXIT)
    with UdpGateway() as gw:
        assert prepare_session_geo("Asia/Tokyo", gw.proxy).udp_exit == "coherent"


def test_the_launch_geo_step_reports_a_udp_exit_that_differs(declared):
    from invisible_core import prepare_session_geo
    declared(OTHER)
    with UdpGateway() as gw:
        assert prepare_session_geo("Asia/Tokyo", gw.proxy).udp_exit == "no"


def test_the_launch_geo_step_measures_nothing_without_a_socks5_proxy(declared):
    from invisible_core import prepare_session_geo
    declared(EXIT)
    assert prepare_session_geo("Asia/Tokyo", None).udp_exit is None
    assert prepare_session_geo("Asia/Tokyo", {"server": "http://127.0.0.1:9"}).udp_exit is None


def test_the_launch_geo_step_does_not_wait_for_the_measurement_after_the_tcp_discovery(declared, monkeypatch):
    """The two run side by side: a TCP discovery of 0.8 s and a silent UDP of 0.8 s cost 0.8 s, not 1.6."""
    from invisible_core import prepare_session_geo

    def slow(proxy=None, **kw):
        time.sleep(0.8)
        return EXIT

    monkeypatch.setattr(_geo, "discover_egress_ip", slow)
    monkeypatch.setattr(_udp_exit, "BUDGET", 0.8)
    monkeypatch.setattr(_udp_exit, "SCHEDULE", (0.0, 0.1, 0.2))
    declared(EXIT)
    with UdpGateway(silent=True) as gw:
        started = time.monotonic()
        geo = prepare_session_geo("Asia/Tokyo", gw.proxy)
        elapsed = time.monotonic() - started
    assert geo.udp_exit == "unmeasured"
    assert elapsed < 1.3, elapsed


def test_a_failed_tcp_discovery_leaves_the_udp_unmeasured_but_a_refusal_is_still_no(declared, monkeypatch):
    from invisible_core import prepare_session_geo

    def boom(proxy=None, **kw):
        raise _geo.GeoTimezoneError("no egress")

    monkeypatch.setattr(_geo, "discover_egress_ip", boom)
    declared(EXIT)
    with UdpGateway() as gw:
        assert prepare_session_geo("Asia/Tokyo", gw.proxy).udp_exit == "unmeasured"
    with UdpGateway(associate_reply=7) as gw:
        assert prepare_session_geo("Asia/Tokyo", gw.proxy).udp_exit == "no"


def _first_pipe_command(monkeypatch, proxy_dict):
    from invisible_core.juggler import connection
    from invisible_core._proxy import parse_proxy
    from test_a_launch_has_one_deadline import _Browser, _launch
    browser = _Browser()
    command = parse_proxy(proxy_dict).as_engine_command() if proxy_dict else None
    c = _launch(monkeypatch, browser, ready_timeout=3.0, proxy=command)
    try:
        c.startup_send("Browser.enable", {})
    finally:
        c.close()
    return browser


def test_the_first_pipe_command_of_a_socks5_launch_carries_the_measure(declared, monkeypatch):
    from invisible_core import configure_proxy, prepare_session_geo
    for mapped, expected in ((EXIT, "coherent"), (OTHER, "no")):
        declared(mapped)
        with UdpGateway() as gw:
            geo = prepare_session_geo("Asia/Tokyo", gw.proxy)
            declared_proxy = configure_proxy(gw.proxy, {}, udp=geo.udp_exit)
            browser = _first_pipe_command(monkeypatch, declared_proxy)
        assert browser.methods[:2] == ["Browser.setBrowserProxy", "Browser.enable"]
        assert browser.params["Browser.setBrowserProxy"]["udp"] == expected


def test_an_unmeasured_exit_travels_as_unmeasured(monkeypatch):
    from invisible_core import configure_proxy
    declared_proxy = configure_proxy({"server": "socks5://10.0.0.1:1080"}, {}, udp="unmeasured")
    browser = _first_pipe_command(monkeypatch, declared_proxy)
    assert browser.params["Browser.setBrowserProxy"]["udp"] == "unmeasured"


def test_a_session_without_a_proxy_carries_no_udp(monkeypatch):
    browser = _first_pipe_command(monkeypatch, None)
    assert browser.methods[:2] == ["Browser.setNoBrowserProxy", "Browser.enable"]
    assert browser.params["Browser.setNoBrowserProxy"] in (None, {})


def test_a_value_that_is_not_one_of_the_three_is_refused():
    from invisible_core._proxy import parse_proxy
    with pytest.raises(ValueError, match="udp"):
        parse_proxy({"server": "socks5://10.0.0.1:1080", "udp": "yes"})


def test_a_proxy_the_caller_wrote_cannot_claim_a_coherent_udp(monkeypatch):
    """`configure_proxy` is where the measurement enters: whatever the caller put under `udp` is replaced
    by what was measured, and removed when nothing was."""
    from invisible_core import configure_proxy
    kept = configure_proxy({"server": "socks5://10.0.0.1:1080", "udp": "coherent"}, {}, udp=None)
    assert "udp" not in kept
    kept = configure_proxy({"server": "socks5://10.0.0.1:1080", "udp": "coherent"}, {}, udp="no")
    assert kept["udp"] == "no"


# the context proxy, the core's own client -----------------------------------------------------------
def test_a_context_proxy_of_the_core_client_is_measured_before_it_is_sent(declared, monkeypatch):
    import types
    from invisible_core.juggler.instance import Browser
    declared(EXIT)
    sent = []
    fake = types.SimpleNamespace(ENGINE_OPTIONS=Browser.ENGINE_OPTIONS, context_send=lambda method, params, **kw: sent.append((method, params)))
    with UdpGateway() as gw:
        Browser.apply_context_options(fake, "ctx-1", {"proxy": gw.proxy})
    (method, params), = [s for s in sent if s[0] == "Browser.setContextProxy"]
    assert params["udp"] == "coherent" and params["browserContextId"] == "ctx-1"
    assert params["type"] == "socks"


def test_one_proxy_is_measured_once_per_session_of_calls(declared, monkeypatch):
    declared(EXIT)
    with UdpGateway() as gw:
        first = _udp_exit.measure_proxy_udp(gw.proxy)
        second = _udp_exit.measure_proxy_udp(dict(gw.proxy))
        assert first == second == "coherent"
        assert gw.associates == 1, "cached by endpoint and a hash of the credentials"


def test_the_cache_key_is_not_the_secret():
    key = _udp_exit.cache_key(_udp_exit.parse_proxy({"server": "socks5://h:1", "username": "a", "password": "hunter2-xyz"}))
    assert "hunter2-xyz" not in repr(key)
    other = _udp_exit.cache_key(_udp_exit.parse_proxy({"server": "socks5://h:1", "username": "a", "password": "other"}))
    assert key != other


# the launch of a client that composed no session ----------------------------------------------------------
def test_a_launch_proxy_without_the_measure_gets_what_the_geo_step_remembered_and_asks_no_network(declared):
    from invisible_core import prepare_session_geo
    declared(EXIT)
    with UdpGateway() as gw:
        assert prepare_session_geo("Asia/Tokyo", gw.proxy).udp_exit == "coherent"
        before = gw.associates
        assert _udp_exit.declare_launch_proxy(dict(gw.proxy))["udp"] == "coherent"
        assert gw.associates == before, "the network was not asked a second time"


def test_a_socks5_launch_proxy_nothing_measured_is_unmeasured_never_coherent():
    assert _udp_exit.declare_launch_proxy({"server": "socks5://10.9.9.9:1080"})["udp"] == "unmeasured"


def test_a_launch_proxy_that_is_not_socks5_is_left_as_it_is():
    proxy = {"server": "http://10.9.9.9:8080"}
    assert _udp_exit.declare_launch_proxy(proxy) == proxy

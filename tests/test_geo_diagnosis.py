"""A failed egress lookup has to name WHICH failure it was.

Until 2026-09-02 five unrelated faults printed one sentence. Wrong proxy
credentials, a proxy hostname with a typo, a proxy that was up but not routing,
a third-party echo endpoint having an outage, and a captive portal returning
HTML all arrived as "could not discover the proxy egress IP via N endpoint(s)",
plus the repr of whichever attempt happened to fail LAST. Only the last one
survived, so the actionable cause was routinely the one thrown away: a 407 on
the first endpoint was hidden by a timeout on the third.

Three things are checked here, and they are not the same claim.

`_classify` is a pure function, so its branches are checked directly, with no
socket and no clock. The exception texts it is fed are not invented: each was
produced by driving the real fault against a real local socket and recording
what `requests` raised. The reproduction bench is not in the suite because
several of its cases cost a full timeout each.

Second, the ADVICE is path-dependent while the class is not, and the pairing is
checked separately. A class names the mechanism; only the caller knows whether a
proxy was in the path. The first version got this wrong in the way this whole
file exists to prevent: on a direct lookup the message read "could not discover
the egress IP directly (no proxy set): the proxy HOSTNAME does not resolve -
check it for a typo", denying and blaming a proxy in one sentence.

Third, one assumption a pure test cannot hold up. A proxy's status code reaches
this code only as TEXT inside a chained cause, so the classification parses a
message rather than inspecting a type, and a message is upstream's to reword.
`test_the_tunnel_failure_text_is_still_what_urllib3_writes` pins that format
against a real socket, because if it drifts, every hand-written case above it
keeps passing while production stops telling 407 from 502.
"""
from __future__ import annotations

import ast
import inspect
import ipaddress
import socket
import threading
import time

import pytest
import requests

from invisible_core import _geo

pytestmark = pytest.mark.unit


def _proxy_error(inner: str) -> requests.exceptions.ProxyError:
    """A ProxyError shaped like the ones urllib3 actually hands to requests.

    Measured shape: ProxyError -> MaxRetryError -> ProxyError -> the cause. What
    the classification reads is the flattened text, so the nesting is reproduced
    only as far as the text is concerned.
    """
    return requests.exceptions.ProxyError(
        f"HTTPSConnectionPool(host='api.ipify.org', port=443): Max retries "
        f"exceeded with url: / (Caused by ProxyError('Unable to connect to "
        f"proxy', {inner}))")


def _http_error(status: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status} Server Error", response=response)


# Every case: the exception one attempt raised, and the class it must be given.
_CASES = [
    ("wrong credentials",
     _proxy_error("OSError('Tunnel connection failed: 407 Proxy Authentication Required')"),
     "proxy_auth"),
    ("proxy refuses the tunnel",
     _proxy_error("OSError('Tunnel connection failed: 403 Forbidden')"),
     "proxy_rejected"),
    ("provider upstream is down",
     _proxy_error("OSError('Tunnel connection failed: 502 Bad Gateway')"),
     "proxy_rejected"),
    ("hostname does not resolve, http proxy",
     _proxy_error("NameResolutionError(\"Failed to resolve 'nope.invalid'\")"),
     "dns_failure"),
    # The SOCKS spelling of the same fault. urllib3's socks path writes NEITHER
    # `NameResolutionError` NOR the type name `gaierror`: measured 2026-09-02, a
    # socks5h:// proxy with a bad hostname produces a ConnectionError whose text
    # says only `[Errno 11001] getaddrinfo failed`. Matching the first token
    # alone sent every SOCKS hostname typo to `connect_failed`, whose advice is
    # to go and check the PORT. socks5h is this module's own translation of
    # `socks5://`, so this is the primary shape, not an exotic one.
    ("hostname does not resolve, socks proxy",
     requests.exceptions.ConnectionError(
         "SOCKSHTTPSConnectionPool(host='api.ipify.org', port=443): Max retries "
         "exceeded with url: / (Caused by NewConnectionError('SOCKSHTTPSConnection "
         "object: Failed to establish a new connection: [Errno 11001] "
         "getaddrinfo failed'))"),
     "dns_failure"),
    ("a bare socket.gaierror, classified directly",
     socket.gaierror(11001, "getaddrinfo failed"),
     "dns_failure"),
    ("nothing listening at the proxy",
     _proxy_error("ConnectTimeoutError('timed out')"),
     "connect_failed"),
    ("same, behind socks, where the top-level type differs",
     requests.exceptions.ConnectTimeout(
         "SOCKSHTTPSConnectionPool(...): Max retries exceeded "
         "(Caused by ProxyConnectionError(...))"),
     "connect_failed"),
    ("proxy accepts and then says nothing",
     requests.exceptions.ReadTimeout("Read timed out. (read timeout=5)"),
     "read_timeout"),
    # SSLError subclasses ConnectionError, so without its own branch ahead of
    # that arm a certificate failure - which is what a TLS-intercepting captive
    # portal looks like, one of the cases this classification exists to separate
    # - was reported as a dead TCP port.
    ("tls interception",
     requests.exceptions.SSLError(
         "HTTPSConnectionPool(host='api.ipify.org', port=443): Max retries "
         "exceeded with url: / (Caused by SSLError(SSLCertVerificationError(1, "
         "'certificate verify failed: self signed certificate')))"),
     "tls_failed"),
    ("the echo endpoint itself is down",
     _http_error(503),
     "endpoint_http_error"),
    ("a captive portal answers with HTML",
     ValueError("'<!DOCTYPE html>' does not appear to be an IPv4 or IPv6 address"),
     "endpoint_not_an_ip"),
    ("socks proxy without PySocks",
     requests.exceptions.InvalidSchema("Missing dependencies for SOCKS support."),
     "socks_missing"),
    ("something never seen before",
     MemoryError("boom"),
     "unknown"),
]


@pytest.mark.parametrize("name,exc,expected", _CASES, ids=[c[0] for c in _CASES])
def test_classify_names_the_failure(name, exc, expected):
    kind, detail = _geo._classify(exc)
    assert kind == expected, f"{name}: got {kind} with detail {detail!r}"
    assert detail, "a class with no detail tells an operator nothing"


def test_classify_never_names_the_path(monkeypatch):
    """The invariant that keeps `_classify` a pure function of the exception.

    A class that asserted a proxy would have to be decided differently on the
    direct path, and the classifier has no way to know which path it is on. That
    fact lives in the caller, and it stays there.
    """
    for name, exc, _expected in _CASES:
        kind, detail = _geo._classify(exc)
        assert "no proxy" not in detail.lower(), name
    # Three classes still carry `proxy` in their NAME, and that is deliberate:
    # a 407 or a refused CONNECT means something spoke the proxy protocol at us,
    # whether or not we configured one. The advice for that case differs by path
    # and lives in _REMEDY, not here.
    named = {k for k in _geo._REMEDY if k.startswith("proxy_") or k == "socks_missing"}
    assert named == {"proxy_auth", "proxy_rejected", "socks_missing"}, (
        f"a class name asserts a proxy that _classify cannot know about: {named}")


# --------------------------------------------------------------------------
# The remedy table: one class, two pieces of advice.
# --------------------------------------------------------------------------

def test_every_remedy_is_a_pair_of_real_sentences():
    """Structural, so collapsing the pair back to one string cannot pass.

    That collapse IS the original defect: one advice string per class is what
    put "check the proxy hostname for a typo" under a header line reading
    "directly (no proxy set)".
    """
    for kind, value in _geo._REMEDY.items():
        assert isinstance(value, tuple), f"{kind}: remedy is not a pair"
        assert len(value) == 2, f"{kind}: expected (proxied, direct)"
        for half in value:
            assert isinstance(half, str) and len(half) > 20, f"{kind}: {half!r}"


@pytest.mark.parametrize("kind", ["dns_failure", "connect_failed", "read_timeout",
                                  "not_routing", "proxy_auth", "proxy_rejected"])
def test_the_two_halves_differ_where_the_path_changes_the_advice(kind):
    """These are the classes whose proxied advice names a proxy to go and check.

    Identical halves here would mean the direct path is being handed advice
    written for the other one, which is the regression this pairing exists to
    stop. Classes whose advice genuinely does not depend on the path (a stale
    database, a missing PySocks) are correctly identical and are not listed.
    """
    proxied, direct = _geo._REMEDY[kind]
    assert proxied != direct, f"{kind}: the direct path gets proxy advice"


def test_the_direct_message_does_not_send_the_reader_to_a_proxy(monkeypatch):
    """The finding this rewrite came from, pinned end to end.

    Reproduced before the fix: with no proxy and no working DNS the message read
        could not discover the egress IP directly (no proxy set):
        the proxy HOSTNAME does not resolve - check it for a typo
    One sentence denied a proxy and blamed one, and sent the reader to a config
    line that is empty. The earlier version of this test asserted only the "no
    proxy set" half and passed on exactly that message.
    """
    def fake_get(url, proxies=None, timeout=None):
        raise requests.exceptions.ConnectionError(
            "HTTPSConnectionPool(host='api.ipify.org', port=443): Max retries "
            "exceeded (Caused by NameResolutionError('Failed to resolve'))")

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    with pytest.raises(_geo.GeoTimezoneError) as excinfo:
        _geo.discover_egress_ip(None)

    text = str(excinfo.value)
    assert "no proxy set" in text
    assert excinfo.value.kind == "dns_failure"
    for phrase in ("the proxy HOSTNAME", "check it for a typo",
                   "the proxy host:port", "the proxy accepted"):
        assert phrase not in text, (
            f"a direct lookup was told to go and check a proxy: {phrase!r}")


def test_the_same_failure_through_a_proxy_does_say_proxy(monkeypatch):
    """The other side, so the test above cannot pass by removing all advice."""
    def fake_get(url, proxies=None, timeout=None):
        raise requests.exceptions.ConnectionError(
            "Caused by NameResolutionError('Failed to resolve')")

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    with pytest.raises(_geo.GeoTimezoneError) as excinfo:
        _geo.discover_egress_ip({"server": "http://proxy.example:8080"})

    text = str(excinfo.value)
    assert "through the proxy" in text
    assert "the proxy HOSTNAME does not resolve" in text
    assert excinfo.value.kind == "dns_failure", "same class, different advice"


# --------------------------------------------------------------------------
# Which classes exist, and whether anything can explain them.
# --------------------------------------------------------------------------

def _module_tree() -> ast.Module:
    return ast.parse(inspect.getsource(_geo))


def _kinds_classify_returns() -> set:
    returned = set()
    for node in ast.walk(ast.parse(inspect.getsource(_geo._classify))):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            first = node.value.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                returned.add(first.value)
    return returned


def _kinds_assigned_in_discovery() -> set:
    """String literals assigned to a local named `kind` in discover_egress_ip.

    `discover_egress_ip` raises with `kind=kind`, a VARIABLE, so a walk that only
    reads literal keywords sees nothing at the single most important raise in the
    module. This reads where that variable gets its literal values instead.
    """
    found = set()
    tree = ast.parse(inspect.getsource(_geo.discover_egress_ip))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "kind" for t in node.targets):
            continue
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                found.add(sub.value)
    # The attempt records build their class positionally rather than through a
    # keyword, so they are named here explicitly rather than pattern-matched.
    found.add("not_routing")
    return found


def _literal_kind_keywords() -> "tuple[set, list]":
    """(`kind="X"` literals, the source lines where `kind=` is NOT a literal).

    The second half is what makes this walk honest. A class routed through a
    variable is invisible to a literal scan, and the failure mode of a scan that
    silently skips what it cannot read is a gate that reports PASS about nothing.
    """
    literals, opaque = set(), []
    for node in ast.walk(_module_tree()):
        for kw in getattr(node, "keywords", []) or []:
            if kw.arg != "kind":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                literals.add(kw.value.value)
            else:
                opaque.append(getattr(kw.value, "lineno", -1))
    return literals, opaque


def _kinds_the_module_can_produce() -> set:
    return (_kinds_classify_returns()
            | _kinds_assigned_in_discovery()
            | _literal_kind_keywords()[0])


def test_the_walk_can_see_every_place_a_class_is_chosen():
    """Guards the two tests below against passing vacuously.

    Every non-literal `kind=` in the module must be the one in
    `discover_egress_ip`, whose literals are harvested separately. A new one
    anywhere else means the walk is blind to it, and this test says so instead of
    quietly reporting that everything has a remedy.
    """
    literals, opaque = _literal_kind_keywords()
    assert literals, "no literal kind= found at all; the walk is broken"
    source = inspect.getsource(_geo).split("\n")
    discovery = inspect.getsource(_geo.discover_egress_ip)
    for lineno in opaque:
        line = source[lineno - 1] if 0 < lineno <= len(source) else ""
        assert line.strip() in discovery, (
            f"line {lineno} passes a non-literal kind= that this walk cannot "
            f"read, so the remedy gate is blind to it: {line.strip()!r}")


def test_every_class_the_module_can_produce_has_a_remedy():
    """A class the message cannot explain is a class that helps nobody."""
    produced = _kinds_the_module_can_produce()
    assert produced, "the AST walk found nothing, so it is not checking anything"
    missing = produced - set(_geo._REMEDY)
    assert not missing, f"these classes can be raised with no remedy text: {missing}"


def test_no_remedy_describes_a_class_that_cannot_happen():
    """The other direction: a remedy nobody can reach is a stale instruction.

    It reads as live documentation and describes a failure the code stopped
    producing, which is worse than saying nothing.

    The harvest deliberately never reads `_REMEDY` itself. The first version
    collected bare string constants from anywhere in the module, and the table's
    own keys are exactly such constants, so every class it described counted as
    reachable BECAUSE it was described - the gate proved its own premise.
    """
    stale = set(_geo._REMEDY) - _kinds_the_module_can_produce()
    assert not stale, f"remedy text for unreachable classes: {stale}"


def test_the_tested_cases_cover_every_class_classify_can_return():
    """Guards this FILE, not the module: a new branch in `_classify` with no
    case here would otherwise be added and never exercised."""
    returned = _kinds_classify_returns()
    covered = {expected for _, _, expected in _CASES}
    assert returned - covered == set(), (
        f"_classify can return {returned - covered} and no case here produces it")


def test_branch_order_and_the_guard_are_two_independent_defences():
    """`InvalidSchema` inherits from BOTH RequestException and ValueError.

    Two things keep a missing PySocks out of the malformed-reply branch: the
    socks branch comes FIRST, and the ValueError branch carries
    `not isinstance(exc, requests.RequestException)`. An earlier version of this
    test claimed the order alone was load-bearing and named a swap as the
    mutation that would break it. That claim was false - the guard alone is
    enough, so the swap leaves the suite green - and a false claim in a docstring
    is worse than no docstring, because the next person trusts it.

    Both defences are asserted here, on the real class hierarchy rather than on
    anyone's memory of it.
    """
    exc = requests.exceptions.InvalidSchema("Missing dependencies for SOCKS support.")
    assert isinstance(exc, ValueError), "the trap itself"
    assert isinstance(exc, requests.RequestException), "and the guard's premise"
    assert _geo._classify(exc)[0] == "socks_missing"

    source = inspect.getsource(_geo._classify)
    socks_at = source.index("socks_missing")
    value_at = source.index("endpoint_not_an_ip")
    assert socks_at < value_at, "defence 1: the socks branch must come first"
    assert "not isinstance(exc, requests.RequestException)" in source, (
        "defence 2: the ValueError branch must exclude requests' own exceptions")


def test_sslerror_needs_its_own_branch_because_of_the_class_hierarchy():
    """Stated as an assertion so the reason cannot be edited away by accident."""
    assert issubclass(requests.exceptions.SSLError,
                      requests.exceptions.ConnectionError), (
        "if this is ever False, the ordering comment in _classify is stale")
    source = inspect.getsource(_geo._classify)
    assert source.index("tls_failed") < source.index("connect_failed")


# --------------------------------------------------------------------------
# The IP that parses and still cannot be an egress address.
# --------------------------------------------------------------------------

class _Reply:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


def _answering(monkeypatch, text: str) -> list:
    calls: list = []

    def fake_get(url, proxies=None, timeout=None):
        calls.append(url)
        return _Reply(text)

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    return calls


@pytest.mark.parametrize("address", [
    "10.8.0.4", "192.168.1.10", "172.16.0.9", "127.0.0.1", "169.254.10.1",
    # RFC 6598, which a carrier-grade NAT hands out. Not covered by
    # `is_private` either, so nothing else would have caught it.
    "100.64.0.1",
    # The IPv6 rows, which no test exercised in the first version: three
    # networks that had only ever printed PASS.
    "::1", "fd00::1", "fe80::1", "::", "fec0::1",
])
def test_a_private_address_is_refused_as_an_egress_ip(monkeypatch, address):
    """It used to be RETURNED, and the failure surfaced two steps later.

    A proxy that has stopped routing answers with an address on the local
    network. That parses, so it was handed back as the egress IP, and the launch
    then died in the geoip lookup with "not present in the geoip database" -
    which blames the database for a proxy that was not proxying.
    """
    _answering(monkeypatch, address)
    with pytest.raises(_geo.GeoTimezoneError) as excinfo:
        _geo.discover_egress_ip({"server": "http://proxy.example:8080"})
    assert excinfo.value.kind == "not_routing"
    assert address in str(excinfo.value)


@pytest.mark.parametrize("address", ["203.0.113.7", "198.51.100.4", "192.0.2.55",
                                     "203.0.113.200", "2001:db8::1"])
def test_a_public_address_is_still_returned(monkeypatch, address):
    """The case that must NOT fire, and it is the reason `is_private` is unusable.

    Python counts the documentation ranges (192.0.2/24, 198.51.100/24,
    203.0.113/24, and 2001:db8::/32) as private, and those are exactly the
    addresses this suite uses as stand-ins for a real egress. A check written as
    `is_private` passes every test above and rejects every one of these.

    No genuinely routable literal appears here on purpose: one was written and
    `test_marker_vocabulary.py::test_no_test_uses_a_globally_routable_placeholder_ip`
    refused it, correctly. The documentation ranges exercise the same branch,
    since none of them is in `_NOT_ROUTABLE`.
    """
    _answering(monkeypatch, address)
    assert _geo.discover_egress_ip({"server": "http://proxy.example:8080"}) == address


def test_mixing_address_versions_does_not_raise():
    """`_NOT_ROUTABLE` holds both families, and every reply is checked against all
    of them. If `IPv4Address in IPv6Network` raised, a perfectly ordinary reply
    would leave `discover_egress_ip` as something that is not a GeoTimezoneError.
    """
    for address in ("203.0.113.7", "2001:db8::1", "10.0.0.1", "fd00::1"):
        parsed = ipaddress.ip_address(address)
        assert isinstance(any(parsed in net for net in _geo._NOT_ROUTABLE), bool)


def test_the_obvious_one_line_version_of_this_check_would_be_wrong():
    """Guards the implementation against being "simplified" into a bug.

    `if parsed.is_private` reads like the same check and is not: Python puts the
    documentation ranges inside it, and leaves carrier-grade NAT outside. Stated
    as assertions so that anyone who reaches for the shorter version meets this
    instead of a broken suite.
    """
    assert ipaddress.ip_address("203.0.113.7").is_private, (
        "if this ever becomes False, `is_private` has changed meaning and the "
        "reason for the explicit network list should be re-checked")
    assert not ipaddress.ip_address("100.64.0.1").is_private, (
        "carrier-grade NAT is outside is_private, which is the other half of "
        "why the shortcut is unusable")
    for public in ("203.0.113.7", "2001:db8::1"):
        assert not any(ipaddress.ip_address(public) in net
                       for net in _geo._NOT_ROUTABLE)
    for local in ("10.8.0.4", "100.64.0.1", "fd00::1"):
        assert any(ipaddress.ip_address(local) in net
                   for net in _geo._NOT_ROUTABLE)


# --------------------------------------------------------------------------
# What the message says, rather than what it contains.
# --------------------------------------------------------------------------

def test_the_message_lists_every_endpoint_not_just_the_last(monkeypatch):
    """The defect this whole file exists for.

    The old message kept `last_err` only, so an attempt that named the real
    cause was overwritten by whatever failed after it.
    """
    faults = [
        _proxy_error("OSError('Tunnel connection failed: 407 Proxy Authentication Required')"),
        _http_error(503),
        requests.exceptions.ReadTimeout("Read timed out. (read timeout=5)"),
    ]
    seen: list = []

    def fake_get(url, proxies=None, timeout=None):
        fault = faults[len(seen)]
        seen.append(url)
        raise fault

    monkeypatch.setattr(_geo.requests, "get", fake_get)

    with pytest.raises(_geo.GeoTimezoneError) as excinfo:
        _geo.discover_egress_ip({"server": "http://proxy.example:8080"})

    error = excinfo.value
    assert error.kind == "mixed", "three different faults are not one verdict"
    assert len(error.attempts) == 3
    assert {a.kind for a in error.attempts} == {
        "proxy_auth", "endpoint_http_error", "read_timeout"}
    text = str(error)
    # The 407 is the actionable one and it failed FIRST, which is exactly the
    # position the old message discarded.
    assert "407" in text
    for endpoint in _geo._IP_ECHO_ENDPOINTS:
        assert endpoint in text


def test_the_message_says_whether_a_proxy_was_even_in_the_path(monkeypatch):
    """Reading a log, "no egress IP" means different things with and without one."""
    def fake_get(url, proxies=None, timeout=None):
        raise requests.exceptions.ReadTimeout("Read timed out.")

    monkeypatch.setattr(_geo.requests, "get", fake_get)

    with pytest.raises(_geo.GeoTimezoneError) as direct:
        _geo.discover_egress_ip(None)
    assert "no proxy set" in str(direct.value)
    assert "the proxy accepted" not in str(direct.value)

    with pytest.raises(_geo.GeoTimezoneError) as proxied:
        _geo.discover_egress_ip({"server": "http://proxy.example:8080"})
    assert "through the proxy" in str(proxied.value)
    assert "the proxy accepted the connection" in str(proxied.value)


def test_a_budget_that_expires_before_anything_runs_says_so(monkeypatch):
    """Zero attempts is not "the endpoints failed for different reasons".

    That is what it used to say, in the prose AND in `.kind` - the attribute the
    class docstring tells callers to branch on instead of the prose - while
    promising a list of differing reasons that was empty.
    """
    def fake_get(url, proxies=None, timeout=None):
        raise AssertionError("no request should be made with no budget left")

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    with pytest.raises(_geo.GeoTimezoneError) as excinfo:
        _geo.discover_egress_ip(None, budget=0.0)

    error = excinfo.value
    assert error.kind == "no_endpoint_tried"
    assert error.attempts == ()
    text = str(error)
    assert "DIFFERENT reasons" not in text
    assert "budget expired before a single endpoint" in text


# --------------------------------------------------------------------------
# Which STEP failed. Three unrelated faults used to share one face.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("resolve", ["prepare_session_geo", "resolve_session_timezone"])
def test_a_database_failure_does_not_look_like_a_proxy_failure(monkeypatch, resolve):
    """The egress lookup SUCCEEDED here. Only the database step failed.

    Before the split, both steps came out of one blanket `except Exception:
    raise` in each resolver, so a failed mmdb download reached the caller with
    the download's own message and nothing saying which half of the work it came
    from. The reflex that invites is to go and check the proxy, which is fine.
    """
    from invisible_core import download

    _answering(monkeypatch, "203.0.113.7")
    monkeypatch.setattr(download, "ensure_geoip_mmdb", lambda *a, **k: (_ for _ in ()).throw(
        OSError("disk full while writing the mmdb")))

    proxy = {"server": "http://proxy.example:8080"}
    with pytest.raises(_geo.GeoTimezoneError) as excinfo:
        getattr(_geo, resolve)("auto", proxy)

    error = excinfo.value
    assert error.kind == "geoip_unavailable"
    text = str(error)
    assert "203.0.113.7" in text, "the message must say the egress lookup worked"
    assert "disk full" in text, "and must keep the underlying cause"
    assert error.__cause__ is not None, "the original exception stays chained"
    # The half that matters when reading a log at speed.
    assert "NOT a proxy problem" in text


def test_a_database_failure_with_no_proxy_does_not_mention_one(monkeypatch):
    """Same step, other path: with no proxy there is nothing to exonerate.

    Asserted on `_geoip_database` rather than through a resolver, because the
    resolvers deliberately do NOT raise on the direct path - a transient lookup
    failure must not break a launch that has no proxy to contradict. The message
    still reaches a person: `_warn_locale_fallback` prints it to stderr, which
    the test below covers.
    """
    from invisible_core import download

    monkeypatch.setattr(download, "ensure_geoip_mmdb", lambda *a, **k: (_ for _ in ()).throw(
        OSError("disk full while writing the mmdb")))

    with pytest.raises(_geo.GeoTimezoneError) as excinfo:
        _geo._geoip_database("203.0.113.7", proxied=False)
    text = str(excinfo.value)
    assert excinfo.value.kind == "geoip_unavailable"
    assert "proxy" not in text, f"a proxyless failure mentioned a proxy: {text}"

    # And the proxied half, so the assertion above cannot pass by saying nothing.
    with pytest.raises(_geo.GeoTimezoneError) as proxied:
        _geo._geoip_database("203.0.113.7", proxied=True)
    assert "NOT a proxy problem" in str(proxied.value)


def test_the_locale_warning_on_a_direct_run_does_not_blame_a_proxy(monkeypatch, capsys):
    """Where the direct-path message actually reaches a person.

    `resolve_session_locale` swallows the failure and warns on stderr, printing
    the exception's own text inside a sentence that already says "with no
    proxy". Before the remedies were split by path, that single line read:

        could not resolve the session locale with no proxy (GeoTimezoneError:
        could not discover the egress IP directly (no proxy set): the proxy
        HOSTNAME does not resolve - check it for a typo ...)

    which denies a proxy and blames one, twice, in one sentence.
    """
    def fake_get(url, proxies=None, timeout=None):
        raise requests.exceptions.ConnectionError(
            "Caused by NameResolutionError('Failed to resolve')")

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    _geo.resolve_session_locale(None, None)

    printed = capsys.readouterr().err
    assert "with no proxy" in printed, "the warning itself must still be emitted"
    for phrase in ("the proxy HOSTNAME", "check it for a typo",
                   "the proxy host:port", "the proxy accepted"):
        assert phrase not in printed, (
            f"a proxyless run was told to go and check a proxy: {phrase!r}")


def test_the_locale_warning_keeps_its_advice_on_its_own_line(monkeypatch, capsys):
    """The cause goes after the sentence, not inside it.

    The warning was written when the egress failure was ONE line and interpolated
    it into the middle of its sentence. The failure now carries a line per
    endpoint, so spliced into the middle it left the warning's own advice - the
    part telling the reader to pass `locale=` - stranded after the last endpoint
    line, which is not where anybody looks or greps.
    """
    def fake_get(url, proxies=None, timeout=None):
        raise requests.exceptions.ReadTimeout("Read timed out.")

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    _geo.resolve_session_locale(None, None)

    lines = capsys.readouterr().err.strip().split("\n")
    assert 'pass locale="xx-XX" to set it explicitly.' in lines[0], (
        "the advice must be in the FIRST line, before the multi-line cause")
    assert len(lines) > 1, "the cause is still reported"
    for line in lines[1:]:
        assert line.startswith("    "), (
            f"the cause must read as an indented block under the sentence: {line!r}")


def test_a_discovery_failure_still_reports_the_discovery_class(monkeypatch):
    """The other side of the same split, so the test above cannot pass by
    turning every failure into a database failure."""
    def fake_get(url, proxies=None, timeout=None):
        raise requests.exceptions.ProxyError(
            "Caused by ProxyError('Unable to connect to proxy', "
            "OSError('Tunnel connection failed: 407 Proxy Authentication Required'))")

    monkeypatch.setattr(_geo.requests, "get", fake_get)
    with pytest.raises(_geo.GeoTimezoneError) as excinfo:
        _geo.prepare_session_geo("auto", {"server": "http://proxy.example:8080"})
    assert excinfo.value.kind == "proxy_auth"


# --------------------------------------------------------------------------
# The one assumption a pure test cannot hold up.
# --------------------------------------------------------------------------

def test_the_tunnel_failure_text_is_still_what_urllib3_writes(monkeypatch):
    """Pins the ONE string the classification parses, against a real socket.

    Everything above feeds `_classify` text recorded from a measurement. If
    urllib3 rewords this line, all of it keeps passing while production loses
    the ability to tell wrong credentials from a provider outage. So this test
    stands up a socket that answers 407 to CONNECT and asserts the code reaches
    us at all.

    Cheap on purpose: the proxy answers immediately, so nothing waits for a
    timeout. Measured 2026-09-02 at about 20ms.

    The environment is neutralised first. `requests` reads HTTP_PROXY/HTTPS_PROXY
    and a machine that has them set would route this request somewhere else, so
    the test would measure that proxy instead of the socket it just started.
    """
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)

    crlf = chr(13) + chr(10)
    reply = ("HTTP/1.1 407 Proxy Authentication Required" + crlf
             + "Content-Length: 0" + crlf + crlf).encode()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5.0)
    port = listener.getsockname()[1]

    def serve():
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        # The accepted connection gets its own deadline too. Bounding only
        # accept() leaves this thread able to block forever on a peer that
        # connects and then says nothing.
        conn.settimeout(5.0)
        try:
            conn.recv(4096)
            conn.sendall(reply)
        except OSError:
            pass
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        with pytest.raises(requests.exceptions.RequestException) as excinfo:
            requests.get("https://api.ipify.org",
                         proxies={"https": f"http://127.0.0.1:{port}"},
                         timeout=(5, 5))
        elapsed = time.monotonic() - started
    finally:
        thread.join(timeout=5.0)
        listener.close()

    assert elapsed < 5.0, "this must fail on the 407, not on a timeout"
    assert _geo._TUNNEL_STATUS.search(f"{type(excinfo.value).__name__}: {excinfo.value}"), (
        "urllib3 no longer writes 'Tunnel connection failed: <code>', so the "
        "proxy status code is not reaching the classification any more")
    assert _geo._classify(excinfo.value)[0] == "proxy_auth"

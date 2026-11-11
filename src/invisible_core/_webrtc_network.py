"""The host network a session declares to the engine, with or without a proxy: one adapter, one private IPv4 address, one bit for IPv6.

WHY. The engine used to read the machine's interface list (``GetAdaptersAddresses``, netlink) and gather
every address it found. A page cannot read those addresses (the host candidates are ``<uuid>.local`` names), but it counts
the names and reads their ranks, so the number of adapters and the presence of IPv6 described the machine under the
browser, not the persona: three names on a PC with Hyper-V, one in a container. A retail Firefox on a given Windows machine
shows what THAT machine has; a persona must show what its declared machine has, on every host. The declaration is one
environment variable, ``STEALTHFOX_WEBRTC_NETWORK``, ``v4=<private address>;v6=<0|1>;p6=<the exit's /64 as <prefix>::/64, empty
without IPv6>``, read by the engine at the three points where it asked the host (design:
``invisible_WebRTC/notes/declared-interfaces-design.md``). The engine reads it (``nr_stealth_network.cpp``): a missing or
malformed variable, or a ``p6`` it refuses, gives NO host candidate at all, IPv4 included, so what the core writes must be
exactly what that parser accepts (:class:`DeclaredNetwork` validates ``p6`` for that reason).

IPv6 IS ONE FACT FOR THE WHOLE BROWSER. ``v6=0`` means no IPv6 for DNS, HTTP and ICE alike. The ENGINE derives
``network.dns.disableIPv6`` from this same declaration, so the core writes NO pref for it: the declaration is the single
source (a test pins that no pref or launch-plan key of the core mentions ``disableIPv6``).

WHAT IS DECLARED.

* ``v4``: one private address, drawn from the seed (:func:`declared_network`) with the frequencies of a measured population
  of home LANs (:data:`DATASET`). It is invisible to a page that has no camera or microphone permission (the host
  candidates are mDNS names, the srflx ``raddr`` is ``0.0.0.0``, and ``getStats`` shows no resolved address); with the
  permission granted Firefox stops obfuscating and the address is what the page reads, so it is drawn like a real one.
* ``v6``: ``1`` only when the EXIT has IPv6 and the road (the relay, behind a proxy) is PROVEN to carry an IPv6 destination. An IPv6 host
  address with no IPv6 road behind it would need an IPv6 srflx that cannot exist, and a persona without IPv6 behind a
  dual-stack exit contradicts the HTTP that reaches IPv6-only hosts through the proxy; following the exit is consistent
  by construction. Nothing in the core proves a relay for IPv6, so the answer is
  ``0`` today and the two inputs exist for the day they can both be said.

EVERY session declares, proxied or not. The engine no longer reads the host's adapters at all (a build without the
declaration gives no host candidates), so the same declaration describes a direct session too: a persona is the same
machine with and without a proxy, and nothing about the network depends on the host under the browser.

DECLARED RESIDUAL DIFFERENCE. ``v6`` is ``0`` for every session today, so a direct session on a dual-stack network
declares only IPv4: the shape of a home without IPv6, which is about half of the users (Google measured 50.1% over IPv6 on
2026-03-28, https://pulse.internetsociety.org/blog/18-years-later-ipv6-reaches-majority). A retail Firefox on the same
dual-stack machine would also show an IPv6 host and an IPv6 srflx. ``v6`` becomes 1 only when the EXIT (proxy or direct) has
IPv6 and the road for it is proven; no measurement of the exit's IPv6 is written until that proof exists, because its result
would be ANDed with a proof that is always false.

The same fact has a second face: a direct session on a host that has IPv6 and NO IPv4 route has no connectivity, because
every lookup asks A only. The core refuses that launch (``GeoTimezoneError``, ``kind="ipv6_only_no_proxy"``: "an IPv6-only
host without a proxy is not supported"; use a proxy or a host with IPv4). The refusal comes from the direct egress echo, which
is made over IPv4 only: when no IPv4 echo can connect and an IPv6-only echo answers, the host is IPv6-only; when both fail
the failure is the usual one, so an offline machine is not misdiagnosed. With a proxy the exit is the proxy's and this does
not apply.

⛔ THE DATASET IS NOT THE POPULATION OF WINDOWS PCs. It is 1.79 million WD Cloud network drives (a NAS), read by
https://blog.benjojo.co.uk/post/picking-unused-rfc1918-ip-space (2025-06-05), the only public count of private LAN
addresses found with real numbers. It skews to Europe (the FritzBox default is 13% of it) and to people who own a NAS. The
sources that rank router defaults without counts (busuk.org, speedguide.net) agree on the first three. What the dataset
does not give is assumed uniform and said so below: the /24s of 10/8 and 172.16/12, the third octet of the 192.168/16 tail,
the host octet. Revisit when a better count exists; the e2e window of the engine tests does not depend on any of it.
"""
from __future__ import annotations

import ipaddress
import random
from dataclasses import dataclass
from typing import Dict

from .seedmix import sub_seed

#: The counts, from the post above. ``blocks``: devices by RFC 1918 block. ``slash24_in_192_168``: the 13 most used /24s of
#: 192.168/16 by third octet (192.168.<n>.0/24); the rest of the block is the tail. Counts are of DEVICES, not of homes.
DATASET: Dict[str, Dict] = {
    "blocks": {"192.168.0.0/16": 1_602_092, "10.0.0.0/8": 149_901, "172.16.0.0/12": 22_132},
    "slash24_in_192_168": {1: 647_220, 0: 280_952, 178: 229_398, 2: 98_341, 68: 36_555, 50: 33_942, 100: 26_695,
                           4: 24_834, 86: 24_736, 10: 20_381, 3: 14_311, 8: 13_554, 188: 11_000},
}

_TAG = "webrtc-network"

#: What the engine's ``PrefixOf`` (nr_stealth_network.cpp) refuses inside global unicast space.
_NOT_A_HOME_PREFIX = tuple(ipaddress.IPv6Network(n) for n in ("2001::/32", "2002::/16", "2001:db8::/32"))
_GLOBAL_UNICAST = ipaddress.IPv6Network("2000::/3")


def _engine_prefix6(text: str) -> str:
    """The /64 named by ``text`` in the form the engine parses (``<prefix>::/64``), or a ``ValueError``."""
    address, slash, length = text.partition("/")
    if "%" in address or (slash and length != "64"):
        raise ValueError("the exit prefix %r is not an IPv6 address or a /64 of one" % (text,))
    try:
        network = ipaddress.IPv6Network((ipaddress.IPv6Address(address), 64), strict=False)
    except ValueError:
        raise ValueError("the exit prefix %r is not an IPv6 address or a /64 of one" % (text,)) from None
    if not network.subnet_of(_GLOBAL_UNICAST) or any(network.overlaps(n) for n in _NOT_A_HOME_PREFIX):
        raise ValueError("the exit prefix %r is not a global unicast /64 a home could have (the engine refuses it)" % (text,))
    return "%s/64" % network.network_address


@dataclass(frozen=True)
class DeclaredNetwork:
    """One adapter with one private IPv4 address and, or not, a global IPv6 address."""

    v4: str
    v6: bool
    #: The /64 of the exit's IPv6, empty without IPv6. A home host sits in the same /64 as the srflx the NAT-less IPv6 path
    #: shows, so the engine builds its IPv6 host address from it. Said by the caller as an IPv6 address (or ``<address>/64``)
    #: in global unicast space; it is stored in the one form the engine parser reads, ``<prefix>::/64``
    #: (``2a01:e0a:1f2:3450::/64``). Not the bare four groups (``2a01:e0a:1f2:3450``): ``inet_pton`` refuses them and the
    #: engine drops the whole declaration, the IPv4 address with it. Documentation (2001:db8::/32), Teredo (2001::/32), 6to4
    #: (2002::/16), link-local, unique-local and any length but 64 are refused here for the same reason: a ``ValueError``.
    prefix6: str = ""

    def __post_init__(self) -> None:
        if self.prefix6:
            object.__setattr__(self, "prefix6", _engine_prefix6(self.prefix6))

    def env_value(self) -> str:
        """The value of ``STEALTHFOX_WEBRTC_NETWORK``."""
        return "v4=%s;v6=%d;p6=%s" % (self.v4, 1 if self.v6 else 0, self.prefix6)


def _weighted(rng: random.Random, weights) -> object:
    """One key of ``weights`` (``[(key, count)]``), chosen with probability count/total."""
    total = sum(count for _, count in weights)
    pick = rng.randrange(total)
    for key, count in weights:
        if pick < count:
            return key
        pick -= count
    raise AssertionError("unreachable")


def _address(rng: random.Random) -> str:
    blocks = list(DATASET["blocks"].items())
    block = _weighted(rng, blocks)
    if block == "192.168.0.0/16":
        listed = DATASET["slash24_in_192_168"]
        tail = DATASET["blocks"][block] - sum(listed.values())
        choices = list(listed.items()) + [(None, tail)]
        third = _weighted(rng, choices)
        if third is None:
            # the rest of the block: no counts, so uniform over the third octets the table did not name
            third = rng.choice([n for n in range(256) if n not in listed])
        prefix = (192, 168, third)
    elif block == "10.0.0.0/8":
        prefix = (10, rng.randrange(256), rng.randrange(256))   # no /24 counts for 10/8: uniform
    else:
        prefix = (172, rng.randrange(16, 32), rng.randrange(256))  # nor for 172.16/12: uniform
    host = rng.randrange(2, 255)  # .1 is the gateway, .0 the network, .255 the broadcast; no counts: uniform
    return str(ipaddress.IPv4Address(".".join(str(o) for o in prefix + (host,))))


def declared_network(seed: int, *, exit_ipv6: bool = False, relay_ipv6: bool = False,
                     exit_prefix6: str = "") -> DeclaredNetwork:
    """The network of the persona ``seed`` names. A pure function of the seed (and of the two proofs about IPv6)."""
    rng = random.Random(sub_seed(seed, _TAG))
    v6 = bool(exit_ipv6 and relay_ipv6)
    return DeclaredNetwork(v4=_address(rng), v6=v6, prefix6=exit_prefix6 if v6 else "")


def network_to_declare(seed: int, *, exit_ipv6: bool = False, relay_ipv6: bool = False,
                       exit_prefix6: str = "") -> str:
    """THE ONE PLACE where the question "what network does this session declare" is answered, as the value for
    ``STEALTHFOX_WEBRTC_NETWORK``. Every session declares. The two env builders (the core's ``build_launch_plan`` and the
    wrapper's ``_session.build_env``) call it rather than recomputing it."""
    return declared_network(seed, exit_ipv6=exit_ipv6, relay_ipv6=relay_ipv6,
                            exit_prefix6=exit_prefix6).env_value()

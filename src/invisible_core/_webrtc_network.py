"""The host network a proxied session declares to the engine: one adapter, one private IPv4 address, one bit for IPv6.

WHY. Behind a proxy the engine used to read the machine's interface list (``GetAdaptersAddresses``, netlink) and gather
every address it found. A page cannot read those addresses (the host candidates are ``<uuid>.local`` names), but it counts
the names and reads their ranks, so the number of adapters and the presence of IPv6 described the machine under the
browser, not the persona: three names on a PC with Hyper-V, one in a container. A retail Firefox on a given Windows machine
shows what THAT machine has; a persona must show what its declared machine has, on every host. The declaration is one
environment variable, ``STEALTHFOX_WEBRTC_NETWORK``, ``v4=<private address>;v6=<0|1>``, read by the engine at the three
points where it asked the host (design: ``invisible_WebRTC/notes/declared-interfaces-design.md``). The engine side does not
exist yet; until it does the variable is ignored.

WHAT IS DECLARED.

* ``v4``: one private address, drawn from the seed (:func:`declared_network`) with the frequencies of a measured population
  of home LANs (:data:`DATASET`). It is invisible to a page that has no camera or microphone permission (the host
  candidates are mDNS names, the srflx ``raddr`` is ``0.0.0.0``, and ``getStats`` shows no resolved address); with the
  permission granted Firefox stops obfuscating and the address is what the page reads, so it is drawn like a real one.
* ``v6``: ``1`` only when the proxy's EXIT has IPv6 and the relay is PROVEN to carry an IPv6 destination. An IPv6 host
  address with no IPv6 road behind it would need an IPv6 srflx that cannot exist, and a persona without IPv6 behind a
  dual-stack exit contradicts the HTTP that reaches IPv6-only hosts through the proxy; following the exit is consistent
  by construction. Nothing in the core proves a relay for IPv6, so the answer is
  ``0`` today and the two inputs exist for the day they can both be said.

A session WITHOUT a proxy declares nothing: its network is the real one, which is what a retail Firefox shows.

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
from typing import Dict, Optional

from .seedmix import sub_seed

#: The counts, from the post above. ``blocks``: devices by RFC 1918 block. ``slash24_in_192_168``: the 13 most used /24s of
#: 192.168/16 by third octet (192.168.<n>.0/24); the rest of the block is the tail. Counts are of DEVICES, not of homes.
DATASET: Dict[str, Dict] = {
    "blocks": {"192.168.0.0/16": 1_602_092, "10.0.0.0/8": 149_901, "172.16.0.0/12": 22_132},
    "slash24_in_192_168": {1: 647_220, 0: 280_952, 178: 229_398, 2: 98_341, 68: 36_555, 50: 33_942, 100: 26_695,
                           4: 24_834, 86: 24_736, 10: 20_381, 3: 14_311, 8: 13_554, 188: 11_000},
}

_TAG = "webrtc-network"


@dataclass(frozen=True)
class DeclaredNetwork:
    """One adapter with one private IPv4 address and, or not, a global IPv6 address."""

    v4: str
    v6: bool

    def env_value(self) -> str:
        """The value of ``STEALTHFOX_WEBRTC_NETWORK``."""
        return "v4=%s;v6=%d" % (self.v4, 1 if self.v6 else 0)


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


def declared_network(seed: int, *, exit_ipv6: bool = False, relay_ipv6: bool = False) -> DeclaredNetwork:
    """The network of the persona ``seed`` names. A pure function of the seed (and of the two proofs about IPv6)."""
    rng = random.Random(sub_seed(seed, _TAG))
    return DeclaredNetwork(v4=_address(rng), v6=bool(exit_ipv6 and relay_ipv6))


def network_to_declare(seed: int, *, proxied: bool, exit_ipv6: bool = False,
                       relay_ipv6: bool = False) -> Optional[str]:
    """THE ONE PLACE where the question "what network does this session declare" is answered, as the value for
    ``STEALTHFOX_WEBRTC_NETWORK``, or ``None`` for a session that declares nothing (no proxy). The two env builders (the
    core's ``build_launch_plan`` and the wrapper's ``_session.build_env``) call it rather than recomputing it."""
    if not proxied:
        return None
    return declared_network(seed, exit_ipv6=exit_ipv6, relay_ipv6=relay_ipv6).env_value()

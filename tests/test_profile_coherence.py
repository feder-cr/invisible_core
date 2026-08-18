"""The bundle must be conditioned on the GPU class the browser will actually report.

`translate_profile_to_prefs` applies `select_persona(profile.seed)`
UNCONDITIONALLY, so the renderer a page sees is always the persona's. Everything
the identification service cross-checks against that renderer - storage quota,
audio output latency and sample rate, screen size, devicePixelRatio, codec
support - is drawn from the bundle. If the bundle was conditioned on a different
class, the profile contradicts itself, and the contradiction is exactly what the
per-GPU pool was calibrated to remove (the 2026-06-18 A/B: a GTX 980 string over
another card's parameters mismatched at ~0.7-0.85).

Conditioning was a CALLER'S RESPONSIBILITY until 2026-07-27, and of the five call
sites three discharged it and two did not:

    config.py                  passed fixed_gpu_class
    launcher.py (wrapper sync) passed it
    async_api.py (wrapper async) passed it
    launch.py                  DID NOT - the profile-manager's launch path
    manager/fingerprint.py     DID NOT - the profile-manager's UI preview

Measured over 500 seeds before the fix: 355 of them (71%) produced a manager
profile whose emitted prefs differed from the wrapper's for the same seed, and
every manager profile with a mismatched class was internally incoherent. Because
BOTH manager paths were wrong in the same way, the UI preview agreed with the
launch and nothing looked wrong from inside the product.

The fix is structural rather than a fourth reminder: `generate_profile` defaults
`fixed_gpu_class` to the seed's own persona class, so a call site cannot omit it.
These tests hold that default in place and prove the two explicit overrides still
win, because a default that cannot be overridden would break pinning.
"""
from __future__ import annotations

import pytest

from invisible_core import generate_profile, translate_profile_to_prefs
from invisible_core._webgl_personas import forced_gpu_class, select_persona

pytestmark = pytest.mark.unit

#: Wide enough to catch a per-class regression. The mismatch sat at 71%, so any
#: sample would have caught THIS one; the width is for the next one, which may
#: be confined to a single class.
_SWEEP = 500


def test_every_seed_gets_a_bundle_matching_the_persona_it_will_expose():
    bad = [
        (s, forced_gpu_class(s), generate_profile(seed=s).gpu.class_tier)
        for s in range(_SWEEP)
        if generate_profile(seed=s).gpu.class_tier != forced_gpu_class(s)
    ]
    assert not bad, (
        f"{len(bad)} of {_SWEEP} seeds build a bundle for one GPU class while "
        f"exposing a persona from another, e.g. {bad[:3]} (seed, persona class, "
        f"bundle class). Every parameter the service cross-checks against the "
        f"renderer comes from that bundle")


def test_the_bare_call_and_the_explicit_call_agree():
    """The two spellings the five call sites used. They must now be the same
    call - that is the whole content of the fix."""
    for seed in range(200):
        bare = translate_profile_to_prefs(generate_profile(seed=seed))
        explicit = translate_profile_to_prefs(
            generate_profile(seed, fixed_gpu_class=forced_gpu_class(seed)))
        differing = sorted(k for k in set(bare) | set(explicit)
                           if bare.get(k) != explicit.get(k))
        assert not differing, (
            f"seed {seed}: omitting fixed_gpu_class still changes {differing}. "
            f"Two call sites omitted it and three did not")


def test_the_exposed_renderer_is_the_personas_on_the_bare_call_too():
    """Ties the two halves together: the prefs carry the persona's renderer, and
    the test above says the bundle matches that persona's class."""
    for seed in (0, 1, 7, 42, 123, 1234):
        prefs = translate_profile_to_prefs(generate_profile(seed=seed))
        assert prefs["zoom.stealth.webgl.renderer"] == select_persona(seed)["renderer"]


# ── the default must not swallow the overrides ────────────────────────────

@pytest.mark.parametrize("tier", ["low_end", "mid_range", "high_end"])
def test_an_explicit_class_pin_still_wins(tier):
    """Pinning is a documented feature and it outranks the default. A default
    that could not be overridden would silently ignore `pin`."""
    assert generate_profile(7, pin={"gpu.class_tier": tier}).gpu.class_tier == tier


@pytest.mark.parametrize("tier", ["low_end", "high_end"])
def test_an_explicit_fixed_gpu_class_still_wins(tier):
    assert generate_profile(7, fixed_gpu_class=tier).gpu.class_tier == tier


def test_a_pin_outranks_fixed_gpu_class():
    """The documented precedence, unchanged: pin, then fixed_gpu_class, then the
    seed's persona."""
    p = generate_profile(7, pin={"gpu.class_tier": "high_end"},
                         fixed_gpu_class="low_end")
    assert p.gpu.class_tier == "high_end"


def test_the_profile_is_still_a_pure_function_of_the_seed():
    """The default is derived from the seed, so determinism must be untouched -
    the property every other guarantee in this package rests on."""
    for seed in (0, 42, 999):
        a, b = generate_profile(seed=seed), generate_profile(seed=seed)
        assert translate_profile_to_prefs(a) == translate_profile_to_prefs(b)


def test_a_pinned_taskbar_moves_availheight():
    """availHeight and the taskbar describe the SAME window, so a pin that
    moves one has to move the other. It did not: the sampler derives
    screen_avail_h from the default taskbar and the pins land afterwards,
    so taskbar_px=72 on a 1080 screen still reported 1032 (= 1080 - 48) and
    the two properties disagreed for anyone who read both."""
    p = generate_profile(42, pin={"screen.taskbar_px": 72})
    assert p.screen.taskbar_px == 72
    assert p.screen.avail_height == p.screen.height - 72


def test_a_pinned_availheight_outranks_the_derivation():
    """An override must not overwrite a more specific override: if the
    caller pinned availHeight as well, that is the value they asked for,
    even though it no longer matches height minus the taskbar."""
    p = generate_profile(42, pin={"screen.taskbar_px": 72,
                                  "screen.avail_height": 900})
    assert p.screen.avail_height == 900


def test_availheight_matches_the_taskbar_with_no_pin_at_all():
    """The control: the default path was already coherent, and the fix must
    not have moved it."""
    for seed in (0, 42, 999, 45061):
        s = generate_profile(seed).screen
        assert s.avail_height == s.height - s.taskbar_px


# ── Il NOME, non solo la classe ─────────────────────────────────────────────
# La classe era gia' forzata dalla persona (i test sopra). Il NOME no: veniva
# dal pool dei 444 in `webgl_renderer_pool.json`, mentre la pagina riceve quello
# della persona da `webgl_gpu_pool.json`. Due pool, due risposte alla stessa
# domanda, e il profile-manager mostrava all'utente quella che il browser non
# avrebbe mai riportato - misurato sul seme 42: GTX 1650 all'utente, Intel HD
# Graphics alla pagina.

def test_the_reported_gpu_name_is_the_one_the_page_receives():
    """La domanda "che GPU ha questo profilo" deve avere UNA risposta.

    Non e' una preferenza di stile: `p.gpu.renderer` e' il campo che qualunque
    consumatore mostra a un utente (`invisible_firefox/manager/fingerprint.py`
    lo faceva nella UI del profile-manager, prima della sua cancellazione del
    2026-08-18) - e un utente che legge un nome e ne vede un altro in una
    pagina di test conclude che il prodotto non funziona. La proprieta' resta
    valida per qualunque futuro consumatore, non solo per quello cancellato.
    """
    from invisible_core.prefs import translate_profile_to_prefs
    disaccordi = []
    for seed in range(200):
        p = generate_profile(seed)
        atteso = translate_profile_to_prefs(p).get("zoom.stealth.webgl.renderer")
        if atteso and p.gpu.renderer != atteso:
            disaccordi.append((seed, p.gpu.renderer, atteso))
    assert not disaccordi, (
        "%d semi su 200 riportano un nome di GPU diverso da quello che la "
        "pagina riceve; il primo e' %r" % (len(disaccordi), disaccordi[:1]))


def test_the_reported_vendor_follows_the_same_source():
    """Il vendor viene cross-controllato contro il renderer, quindi non basta
    correggere il nome: i due devono uscire dalla stessa persona."""
    from invisible_core._webgl_personas import select_persona
    for seed in (0, 42, 999, 45061):
        p = generate_profile(seed)
        persona = select_persona(seed)
        if persona:
            assert p.gpu.vendor == persona["vendor"]
            assert p.gpu.renderer == persona["renderer"]


def test_an_explicit_pin_still_outranks_the_persona():
    """Il caso che deve NON scattare. La persona e' la sorgente per il caso
    non specificato; una pin esplicita resta la volonta' del chiamante, come
    gia' vale per `gpu.class_tier`."""
    p = generate_profile(42, pin={"gpu.renderer": "ANGLE (Prova, Scelta Mia)"})
    assert p.gpu.renderer == "ANGLE (Prova, Scelta Mia)"


def test_the_class_still_comes_from_the_persona_not_from_the_reported_name():
    """Il secondo caso che deve NON scattare. Il nome riportato e' cambiato;
    la CLASSE su cui il bundle e' condizionato non deve essersi mossa, o
    l'estrazione pesata rimappa ogni identita'."""
    from invisible_core._webgl_personas import forced_gpu_class
    for seed in (0, 42, 999, 45061):
        atteso = forced_gpu_class(seed)
        if atteso:
            assert generate_profile(seed).gpu.class_tier == atteso

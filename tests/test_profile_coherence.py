"""The bundle must be conditioned on the GPU class the browser will actually report.

`generate_profile` chooses one validated persona for the session and everything
downstream reads it, so the renderer a page sees is always that persona's.
Until 2026-09-15 `translate_profile_to_prefs` chose its own by calling
`select_persona(profile.seed)` again, which agreed with the profile's for as long
as the only input was the seed and diverged the moment a `pin` arrived: the pin
moved the label and never the browser. Everything
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

The fix is structural rather than a fourth reminder: `generate_profile` chooses the
persona once and DERIVES the class from it, so a call site cannot omit it and cannot
pass one that contradicts it. These tests hold that in place, prove the two explicit
overrides still win - a default that cannot be overridden would break pinning - and
prove that an override the pool cannot present is refused instead of half-applied.
"""
from __future__ import annotations

import pytest

from invisible_core import generate_profile, translate_profile_to_prefs
from invisible_core._webgl_personas import _gpu_pool, select_persona

pytestmark = pytest.mark.unit

#: Wide enough to catch a per-class regression. The mismatch sat at 71%, so any
#: sample would have caught THIS one; the width is for the next one, which may
#: be confined to a single class.
_SWEEP = 500


def test_every_seed_gets_a_bundle_matching_the_persona_it_will_expose():
    """Read through the EMITTED prefs, not through a helper.

    This compared `forced_gpu_class(seed)` against `Profile.gpu.class_tier`,
    which was two calls to one function once the class became derived from the
    persona - it could not fail. The property is still worth holding, so it is
    measured the long way round: take the renderer the browser is actually told,
    find the pool entry that carries it, and compare THAT entry's class with the
    one the bundle was conditioned on. Profile, prefs and pool are three reads."""
    by_name = {(e["renderer"], e["vendor"]): e["gpu_class"] for e in _gpu_pool()}
    bad = []
    for seed in range(_SWEEP):
        profile = generate_profile(seed=seed)
        prefs = translate_profile_to_prefs(profile)
        exposed = by_name.get((prefs["zoom.stealth.webgl.renderer"],
                               prefs["zoom.stealth.webgl.vendor"]))
        if exposed is not None and exposed != profile.gpu.class_tier:
            bad.append((seed, exposed, profile.gpu.class_tier))
    assert not bad, (
        f"{len(bad)} of {_SWEEP} seeds build a bundle for one GPU class while "
        f"exposing a persona from another, e.g. {bad[:3]} (seed, exposed class, "
        f"bundle class). Every parameter the service cross-checks against the "
        f"renderer comes from that bundle")


def test_there_is_only_one_way_to_ask_for_a_class():
    """What made the two spellings agree was deleting one of them.

    This compared `generate_profile(seed)` against
    `generate_profile(seed, fixed_gpu_class=forced_gpu_class(seed))` across 200
    seeds, because five call sites used those two spellings and three of them
    disagreed with the other two. The argument is gone - the class is derived
    from the persona, and `pin["gpu.class_tier"]` is the only way to ask - so
    the comparison has nothing left to compare. What replaces it is the reason
    it can never come back: passing the old argument is an error, not a
    synonym."""
    import inspect
    assert "fixed_gpu_class" not in inspect.signature(generate_profile).parameters
    with pytest.raises(TypeError):
        generate_profile(42, fixed_gpu_class="mid_range")


def test_the_exposed_renderer_is_the_personas_on_the_bare_call_too():
    """Ties the two halves together: the prefs carry the persona's renderer, and
    the test above says the bundle matches that persona's class."""
    for seed in (0, 1, 7, 42, 123, 1234):
        prefs = translate_profile_to_prefs(generate_profile(seed=seed))
        assert prefs["zoom.stealth.webgl.renderer"] == select_persona(seed)["renderer"]


# ── the default must not swallow the overrides ────────────────────────────

@pytest.mark.parametrize("tier", ["low_end", "mid_range"])
def test_an_explicit_class_pin_still_wins(tier):
    """Pinning is a documented feature and it outranks the default. A default
    that could not be overridden would silently ignore `pin`.

    The tiers are the ones the validated pool can actually present. `high_end`
    used to be in this list and passed, because the pin reached the sampler; it
    never reached the persona, so the profile it produced carried high_end cores,
    storage and screen behind a low_end GPU string - the cross-check contradiction
    this whole file exists to prevent. It is now refused, and the refusal has its
    own test below."""
    assert generate_profile(7, pin={"gpu.class_tier": tier}).gpu.class_tier == tier


@pytest.mark.parametrize("tier", ["high_end", "integrated_old", "integrated_modern"])
def test_a_class_the_pool_cannot_present_is_refused_not_faked(tier):
    """The other half of the precedence rule, and the one that was missing.

    A class with no validated persona cannot be honoured: the bundle would be
    conditioned on it while the page reads a GPU of a different class. Silently
    conditioning half the profile is worse than not pinning at all, so it raises,
    and the message names what IS available rather than only what is not."""
    with pytest.raises(ValueError) as excinfo:
        generate_profile(7, pin={"gpu.class_tier": tier})
    assert tier in str(excinfo.value)
    assert "low_end" in str(excinfo.value) and "mid_range" in str(excinfo.value)


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
    """An explicit pin is the caller's will and still outranks the seed's draw.

    This test used to pass a renderer that exists nowhere - "ANGLE (Prova, Scelta
    Mia)" - and assert it came back out of `Profile.gpu.renderer`. It was green
    for as long as the defect existed, because the only thing it could fail on was
    the value it had just handed in being echoed back, and the browser was never
    asked. So it asserts on the emitted pref now: that is the answer that decides
    what a page reads, and it is the one the old assertion could not distinguish."""
    from invisible_core._webgl_personas import _gpu_pool
    wanted = next(p for p in _gpu_pool() if p["renderer"] != select_persona(42)["renderer"])
    p = generate_profile(42, pin={"gpu.renderer": wanted["renderer"]})
    assert p.gpu.renderer == wanted["renderer"]
    assert translate_profile_to_prefs(p)["zoom.stealth.webgl.renderer"] == wanted["renderer"]
    assert translate_profile_to_prefs(p)["zoom.stealth.webgl.vendor"] == wanted["vendor"]


def test_a_renderer_the_pool_cannot_present_is_refused_not_relabelled():
    """A free string can never be honoured, so it must not be accepted.

    The ~81 getParameter values, the shader precisions and the extension list
    travel with the name; a name over foreign params is the mismatch FP Pro
    scores at ~0.70. Accepting the string and quietly keeping the seed's persona
    is what produced a Profile that reported one GPU while the page read another."""
    with pytest.raises(ValueError) as excinfo:
        generate_profile(42, pin={"gpu.renderer": "ANGLE (Nope, Not A Real Card)"})
    assert "no validated GPU persona" in str(excinfo.value)
    assert "Available renderers" in str(excinfo.value)


def test_the_label_and_the_page_agree_for_every_seed_pinned_or_not():
    """The single property the whole refactor exists to hold.

    Whatever a caller pins, `Profile.gpu` and the emitted prefs must name the same
    GPU - they are one decision read twice, not two decisions that happen to
    agree. Measured before the fix on seed 1561645783: the profile reported an AMD
    RX 7900 XTX and `zoom.stealth.webgl.renderer` carried the seed's NVIDIA GTX
    980."""
    from invisible_core._webgl_personas import _gpu_pool
    other = _gpu_pool()[-1]
    pins = [None,
            {"gpu.class_tier": "mid_range"},
            {"gpu.class_tier": "low_end"},
            {"gpu.renderer": other["renderer"]},
            {"gpu.vendor": other["vendor"]},
            {"gpu.renderer": other["renderer"], "gpu.vendor": other["vendor"]}]
    disagreements = []
    for seed in range(60):
        for pin in pins:
            p = generate_profile(seed, pin=pin)
            prefs = translate_profile_to_prefs(p)
            if (p.gpu.renderer != prefs["zoom.stealth.webgl.renderer"]
                    or p.gpu.vendor != prefs["zoom.stealth.webgl.vendor"]):
                disagreements.append((seed, pin, p.gpu.renderer,
                                      prefs["zoom.stealth.webgl.renderer"]))
    assert not disagreements, (
        "%d profile/page disagreements, first: %r" % (len(disagreements), disagreements[:1]))


def test_the_class_still_comes_from_the_persona_not_from_the_reported_name():
    """Il secondo caso che deve NON scattare. Il nome riportato e' cambiato;
    la CLASSE su cui il bundle e' condizionato non deve essersi mossa, o
    l'estrazione pesata rimappa ogni identita'."""
    for seed in (0, 42, 999, 45061):
        persona = select_persona(seed)
        if persona:
            assert generate_profile(seed).gpu.class_tier == persona["gpu_class"]

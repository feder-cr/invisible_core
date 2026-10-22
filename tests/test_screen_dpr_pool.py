"""The device pixel ratios the screen table gives 1920x1080 panels.

Until 51.48.0 a 1920x1080 panel was at 100% in 94.8% of seeds and never at 150%,
while the Windows fleet runs that panel at 125% and 150% in about a third of
page views: StatCounter's desktop screen resolutions are CSS pixels, so the
panel reads 1920x1080 at 100%, 1536x864 at 125% and 1280x720 at 150%, and those
three split 65.7/23.4/11.0 in September 2026, 60.8/20.8/18.3 in June and
61.4/26.8/11.8 in March. The table now splits it 62/24/14
(cpt_screen_given_class_tier.json, `_meta.dpr_1080p`).
"""
import collections
import json

import pytest

from invisible_core._fpforge import generate_profile
from invisible_core._fpforge import _sampler
from invisible_core.prefs import translate_profile_to_prefs

TARGET = {1.0: 0.62, 1.25: 0.24, 1.5: 0.14}
SEEDS = 4000


def _is_1080(value, dpr=None):
    return value["w"] == 1920 and value["h"] == 1080 and (dpr is None or float(value["dpr"]) == dpr)


@pytest.mark.unit
def test_1080p_panels_follow_the_fleet_scale_mix():
    """Known-bad: the 51.48.0 table, 94.8 / 5.2 / 0.0."""
    counts = collections.Counter()
    for seed in range(1, SEEDS + 1):
        screen = generate_profile(seed).screen
        if (screen.width, screen.height) == (1920, 1080):
            counts[screen.dpr] += 1
    total = sum(counts.values())
    assert total > SEEDS / 2, "1080p is two thirds of seeds; the sample says %d of %d" % (total, SEEDS)
    for dpr, share in TARGET.items():
        assert abs(counts[dpr] / total - share) < 0.03, (dpr, counts[dpr] / total, dict(counts))
    assert set(counts) <= set(TARGET), dict(counts)


@pytest.mark.unit
def test_every_sampled_scale_has_its_frame_and_prefs():
    """A scale the table samples but the frame table does not know is refused at
    launch (test_a_scale_with_no_measured_frame_is_refused): every pair the
    table can return must translate."""
    pairs = {}
    for seed in range(1, SEEDS + 1):
        p = generate_profile(seed)
        pairs.setdefault((p.screen.width, p.screen.height, p.screen.dpr), p)
    assert {d for _, _, d in pairs} >= set(TARGET), sorted(pairs)
    for (w, h, dpr), p in pairs.items():
        prefs = translate_profile_to_prefs(p, timezone="America/New_York")
        assert prefs["layout.css.devPixelsPerPx"] == str(dpr)
        assert prefs["zoom.stealth.screen.width"] == int(w / dpr + 0.5), (w, h, dpr)


@pytest.mark.unit
def test_the_scaled_1080p_entries_sit_right_after_the_100_percent_one():
    """The scaled mass was carved from the tail of each row's 1080p@100% entry
    and placed right after it, so no other cumulative boundary moved and a seed
    changed only if its draw fell in that tail: 21.5% of seeds, only their dpr.
    Moving these entries elsewhere in a row, or deleting one, would remap
    identities that have nothing to do with the panel (prefs.md, weighted pools)."""
    table = _sampler._CPT_SCREEN
    rows_with_1080p = 0
    for key, rows in table.items():
        values = [e["value"] for e in rows]
        if not any(_is_1080(v, 1.0) for v in values):
            continue
        rows_with_1080p += 1
        i = next(n for n, v in enumerate(values) if _is_1080(v, 1.0))
        after = [float(v["dpr"]) for v in values[i + 1:i + 3] if _is_1080(v)]
        assert 1.5 in after, (key, values[i:i + 3])
        assert abs(sum(e["prob"] for e in rows) - 1.0) < 0.002, key
    assert rows_with_1080p == 18

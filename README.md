<p align="center">
  <a href="https://feder-cr.github.io/invisible_playwright/"><img src="https://raw.githubusercontent.com/feder-cr/invisible_playwright/main/docs/scrapeorbit-demo.gif" alt="ScrapeOrbit - find and scrape any company on Earth" width="760"></a>
</p>
<p align="center">
  <b>Find and scrape any company on Earth.</b>
</p>
<p align="center">
  <a href="https://feder-cr.github.io/invisible_playwright/"><img src="https://img.shields.io/badge/%E2%96%B6_Try_it_live-38f0c8?style=for-the-badge" alt="Try it live"></a>
</p>

<h2></h2>

# invisible_core

Pure config for a patched Firefox stealth profile - **zero Playwright dependency**.

`seed → fingerprint profile → Firefox prefs`, plus patched-binary download,
proxy config, and geo/timezone resolution. Importing it does not start a browser,
which is the point: the same fingerprint config backs an automation wrapper and a
desktop profile manager without either depending on the other.

The shared foundation used by:

- **[invisible_playwright](https://github.com/feder-cr/invisible_playwright)** - the
  Playwright automation wrapper (`InvisiblePlaywright`).

The engine itself lives in
[firefox_antidetect_patch](https://github.com/feder-cr/firefox_antidetect_patch),
a Firefox patched at the C++ source level so the fingerprint is produced by the
browser instead of injected into the page.

```bash
pip install invisible-core
```

```python
from invisible_core import generate_profile, translate_profile_to_prefs, ensure_binary

profile = generate_profile(seed=42)          # deterministic Bayesian fingerprint
prefs   = translate_profile_to_prefs(profile)  # dict of Firefox user prefs
binary  = ensure_binary()                     # path to the patched Firefox binary
```

Same seed → same fingerprint, every time.

## What the fields actually mean

Which surfaces a browser fingerprint is made of, what each one gives away, and why
consistency between them matters more than any single value, is written up at
**[feder-cr.github.io/invisible_playwright](https://feder-cr.github.io/invisible_playwright/)**.
Those pages are about the problem, not about this package, and several of them
record something we got wrong first. A few that document the exact fields this
package generates:

- [Pinning fingerprint fields](https://github.com/feder-cr/invisible_playwright/blob/main/docs/pinning.md) -
  how `pin=` interacts with the Bayesian sampler, and which combinations it refuses
  because they don't occur on real hardware.
- [How to make Linux and macOS report real Windows fonts](https://github.com/feder-cr/invisible_playwright/blob/main/docs/bundled-fonts-cross-platform.md) -
  the font side of `generate_profile`, and why the family list is bundled rather than
  sampled from the host.
- [Canvas and WebGL fingerprints, identical across OSes](https://github.com/feder-cr/invisible_playwright/blob/main/docs/canvas-webgl-cross-platform-consistency.md) -
  why the same seed produces a byte-identical hash regardless of what's actually
  running underneath.
- [Playwright timezone does not match the proxy IP](https://github.com/feder-cr/invisible_playwright/blob/main/docs/timezone-proxy-mismatch.md) -
  the surfaces this package's geo/timezone resolution has to keep in agreement.

---

<p align="center">
  <a href="https://github.com/feder-cr/invisible_core/actions/workflows/ci.yml"><img src="https://github.com/feder-cr/invisible_core/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
</p>

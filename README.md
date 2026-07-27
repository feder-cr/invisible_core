# invisible_core

<p>
  <a href="https://github.com/feder-cr/invisible_core/actions/workflows/ci.yml"><img src="https://github.com/feder-cr/invisible_core/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
</p>

Pure config for a patched Firefox stealth profile - **zero Playwright dependency**.

`seed → fingerprint profile → Firefox prefs`, plus patched-binary download,
proxy config, and geo/timezone resolution. Importing it does not start a browser,
which is the point: the same fingerprint config backs an automation wrapper and a
desktop profile manager without either depending on the other.

The shared foundation used by:

- **[invisible_playwright](https://github.com/feder-cr/invisible_playwright)** - the
  Playwright automation wrapper (`InvisiblePlaywright`).
- **[invisible_firefox](https://github.com/feder-cr/invisible_firefox)** - the
  antidetect profile manager (launches the binary directly).

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
record something we got wrong first.

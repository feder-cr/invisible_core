# invisible_core

<p>
  <a href="https://github.com/feder-cr/invisible_core/actions/workflows/ci.yml"><img src="https://github.com/feder-cr/invisible_core/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
</p>

Pure config for a patched Firefox stealth profile - **zero Playwright dependency**.

`seed → fingerprint profile → Firefox prefs`, plus patched-binary download,
proxy config, and geo/timezone resolution. This is the shared foundation used by:

- **invisible_playwright** - the Playwright automation wrapper (`InvisiblePlaywright`).
- **invisible_firefox** - the antidetect profile manager (launches the binary directly).

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

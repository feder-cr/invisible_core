# Third-party code in invisible_core

Two files were extracted from Microsoft Playwright's driver bundle and are
redistributed under the Apache License 2.0 (`LICENSE-APACHE`). Everything else
is MIT (`LICENSE`).

| file | what it is |
|---|---|
| `src/invisible_core/juggler/injected.js` | the selector engines and the actionability checks that run inside the page's utility world, with the stealth fixes invisible_playwright made to them |
| `src/invisible_core/juggler/keylayout.py` | the US keyboard layout (key, code, keyCode, location) |

Both came with the Juggler client, which lived in invisible_playwright (and in
a copy each in invisible_selenium and invisible_puppeteer) until 38.34.0 moved
it here. The history of the changes made to them is in invisible_playwright's
`THIRD_PARTY_FORK.md`; they are regenerated from the bundle by
`scripts/gen_injected_source.py` and `scripts/gen_key_layout.py`.

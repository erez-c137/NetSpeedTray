# Translators

NetSpeedTray's UI is available in 9 languages thanks to the community contributors below. Each name represents real time and care invested in making the app feel native to its users — thank you.

If you'd like to contribute a translation or improve an existing one, see the locale files in [`src/netspeedtray/constants/locales/`](src/netspeedtray/constants/locales/) and open a pull request. The `en_US.json` file is the source of truth — all other locales must keep key parity with it (enforced by [`test_locales_parity.py`](src/netspeedtray/tests/unit/test_locales_parity.py)).

## Contributors

| Language | Locale | Translator | Initial PR |
|----------|--------|------------|------------|
| Korean | `ko_KR` | [@VenusGirl](https://github.com/VenusGirl) ❤ | [#73](https://github.com/erez-c137/NetSpeedTray/pull/73), polished in [#97](https://github.com/erez-c137/NetSpeedTray/pull/97), [#101](https://github.com/erez-c137/NetSpeedTray/pull/101), [#139](https://github.com/erez-c137/NetSpeedTray/pull/139) |
| Dutch | `nl_NL` | [@CMTriX](https://github.com/CMTriX) | [#124](https://github.com/erez-c137/NetSpeedTray/pull/124) |
| Russian | `ru_RU` | [@ZeoNish](https://github.com/ZeoNish) | [#63](https://github.com/erez-c137/NetSpeedTray/pull/63) |
| Slovenian | `sl_SI` | [@anderlli0053](https://github.com/anderlli0053) (Andrew Poženel) | [#79](https://github.com/erez-c137/NetSpeedTray/pull/79) |
| Polish | `pl_PL` | [@FadeMind](https://github.com/FadeMind) | [#39](https://github.com/erez-c137/NetSpeedTray/pull/39) |
| French | `fr_FR` | [@logounet](https://github.com/logounet) | #94 |

Other locales (English, German, Spanish) are currently maintained by the project owner. Native-speaker reviews are very welcome — even one-line corrections to phrasing or terminology are valuable.

## How translation works in NetSpeedTray

- Each locale lives in a single JSON file under [`src/netspeedtray/constants/locales/`](src/netspeedtray/constants/locales/)
- Keys must match `en_US.json` exactly (the parity test fails the build otherwise)
- Values can use Python format-string placeholders like `{file_path}` or `{error}` — preserve these verbatim
- For new keys added by a feature PR, the maintainer typically fills the non-English values with an English placeholder so the parity test passes; translators can then submit a follow-up PR to localize them properly

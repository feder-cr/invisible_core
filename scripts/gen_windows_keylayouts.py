"""Generates `juggler/keylayouts_windows.py`: every keyboard a Windows persona can type on.

⛔ READ FROM THE FILES' BYTES, NOT FROM THE MACHINE. The tables come from the
layout files Windows ships, `kbd*.dll`, parsed as data: the
KBDTABLES structure that `KbdLayerDescriptor` returns (kbd.h of the Windows
DDK). No Windows API is called and no layout is loaded, so the result does not
depend on the machine that runs this script, nor on its Windows version: every
file must match the SHA-256 pinned below, and a file that does not is refused.
The files can sit in any directory (`--dll-dir`), on any OS.

`keyCode` follows the rules of Firefox's widget on Windows
(`widget/windows/KeyboardLayout.cpp`, `ConvertNativeKeyCodeToDOMKeyCode`, and
`widget/SharedWidgetUtils.cpp`, `ComputeKeyCodeFromChar`).

The reference is Windows 11 26H2, build 26300.9711 (cumulative KB5127648), on the 26100 base: 24H2,
25H2 and 26H2 share one servicing branch, so at the same cumulative their files are the
same; they change with cumulatives (the Arabic layouts did in 2025-2026), and these are .9711's.

WHICH keyboards a persona has is what Windows itself installs with its language
and country: the keyboards-to-install of its locale (LOCALE_SKEYBOARDSTOINSTALL)
in `locale.nls` of the same build, read from its bytes and pinned like the
layout files. The file's layout is Wine's (include/winternl.h NLS_LOCALE_HEADER,
NLS_LOCALE_DATA, NLS_LOCALE_LCNAME_INDEX; dlls/kernelbase/locale.c, wine-mirror
78e52205), whose own locale.nls writes a constant in that field; read here from
Windows' file, it gives for all 927 locales of the build what GetLocaleInfoEx
returns (checked once, by hand, as a test of the reader). Each locale has a list
of LANGID:KLID profiles: Russian has Russian and English US, Persian has two
Persian keyboards and English US. IME profiles stay out: an IME language types
Latin text through the keyboard under its IME (IME_BASE).

    python scripts/gen_windows_keylayouts.py [--dll-dir DIR] [--nls FILE]          # regenerates
    python scripts/gen_windows_keylayouts.py [--dll-dir DIR] [--nls FILE] --check  # 1 if stale

Do not hand-edit the output.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import pprint
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "invisible_core" / "juggler" / "keylayouts_windows.py"
DEFAULT_DLL_DIR = pathlib.Path(r"C:\Windows\System32")
DEFAULT_NLS = DEFAULT_DLL_DIR / "locale.nls"
#: SHA-256 of locale.nls of the reference build: anything else is refused.
NLS_PIN = "00d972d678db02525b731e1286e8daf53e87e5cbc58edcaf90de4222470a639a"

#: Physical keys that produce characters: scan code -> DOM `code`
#: (widget/NativeKeyToDOMCodeName.inc, CODE_MAP_WIN).
PRINTABLE_SCANCODES = {
    0x29: "Backquote", 0x02: "Digit1", 0x03: "Digit2", 0x04: "Digit3", 0x05: "Digit4",
    0x06: "Digit5", 0x07: "Digit6", 0x08: "Digit7", 0x09: "Digit8", 0x0A: "Digit9",
    0x0B: "Digit0", 0x0C: "Minus", 0x0D: "Equal",
    0x10: "KeyQ", 0x11: "KeyW", 0x12: "KeyE", 0x13: "KeyR", 0x14: "KeyT", 0x15: "KeyY",
    0x16: "KeyU", 0x17: "KeyI", 0x18: "KeyO", 0x19: "KeyP", 0x1A: "BracketLeft",
    0x1B: "BracketRight",
    0x1E: "KeyA", 0x1F: "KeyS", 0x20: "KeyD", 0x21: "KeyF", 0x22: "KeyG", 0x23: "KeyH",
    0x24: "KeyJ", 0x25: "KeyK", 0x26: "KeyL", 0x27: "Semicolon", 0x28: "Quote",
    0x2B: "Backslash",
    0x2C: "KeyZ", 0x2D: "KeyX", 0x2E: "KeyC", 0x2F: "KeyV", 0x30: "KeyB", 0x31: "KeyN",
    0x32: "KeyM", 0x33: "Comma", 0x34: "Period", 0x35: "Slash",
    0x56: "IntlBackslash", 0x73: "IntlRo", 0x7D: "IntlYen", 0x7E: "NumpadComma",
    0x39: "Space",
}

#: `WidgetUtils::ComputeKeyCodeFromChar` for the characters it names.
KEYCODE_OF_CHAR = {
    " ": 0x20, "\t": 0x09, ":": 0x3A, ";": 0x3B, "<": 0x3C, "=": 0x3D, ">": 0x3E,
    "?": 0x3F, "@": 0x40, "^": 0xA0, "!": 0xA1, '"': 0xA2, "#": 0xA3, "$": 0xA4,
    "%": 0xA5, "&": 0xA6, "_": 0xA7, "(": 0xA8, ")": 0xA9, "*": 0xAA, "+": 0xAB,
    "|": 0xAC, "-": 0xAD, "{": 0xAE, "}": 0xAF, "~": 0xB0, ",": 0xBC, ".": 0xBE,
    "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
VK_OEM_1, VK_OEM_PLUS, VK_OEM_COMMA, VK_OEM_MINUS, VK_OEM_PERIOD = 0xBA, 0xBB, 0xBC, 0xBD, 0xBE
VK_OEM_2, VK_OEM_3, VK_OEM_4, VK_OEM_5, VK_OEM_6, VK_OEM_7, VK_OEM_8 = 0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF
VK_OEM_102, VK_ABNT_C1, VK_ABNT_C2 = 0xE2, 0xC1, 0xC2
#: `GetFallbackKeyCodeOfPunctuationKey`, by virtual key.
FALLBACK_KEYCODE = {
    VK_OEM_1: 0x3B, VK_OEM_PLUS: 0x3D, VK_OEM_COMMA: 0xBC, VK_OEM_MINUS: 0xAD,
    VK_OEM_PERIOD: 0xBE, VK_OEM_2: 0xBF, VK_OEM_3: 0xC0, VK_OEM_4: 0xDB, VK_OEM_5: 0xDC,
    VK_OEM_6: 0xDD, VK_OEM_7: 0xDE, VK_OEM_8: 0xC0, VK_OEM_102: 0xDC, VK_ABNT_C1: 0xDC,
}
OEM_VKS = set(FALLBACK_KEYCODE) | {VK_ABNT_C2}
VK_SPACE = 0x20
WCH_NONE, WCH_DEAD, WCH_LGTR = 0xF000, 0xF001, 0xF002
SHIFT, CTRL, ALT = 1, 2, 4

#: Languages whose default profile is an IME: the keyboard Latin text is typed on.
IME_BASE = {"ja": "00000411", "ko": "00000412", "zh": "00000409", "vi": "00000409",
            "am": "00000409", "ti": "00000409", "yi": "00000409"}

#: KLID -> layout file name.
FILES = {'0000040D': 'KBDHEB.DLL', '0000042A': 'KBDVNTC.DLL', '00000401': 'KBDA1.DLL',
 '00000405': 'KBDCZ.DLL',
 '00000406': 'KBDDA.DLL',
 '00000407': 'KBDGR.DLL',
 '00000408': 'KBDHE.DLL',
 '00000409': 'KBDUS.DLL',
 '0000040A': 'KBDSP.DLL',
 '0000040B': 'KBDFI.DLL',
 '0000040C': 'KBDFR.DLL',
 '0000040E': 'KBDHU.DLL',
 '0000040F': 'KBDIC.DLL',
 '00000410': 'KBDIT.DLL',
 '00000411': 'KBDJPN.DLL',
 '00000412': 'KBDKOR.DLL',
 '00000414': 'KBDNO.DLL',
 '00000415': 'KBDPL1.DLL',
 '00000416': 'KBDBR.DLL',
 '00000419': 'KBDRU.DLL',
 '0000041A': 'KBDCR.DLL',
 '0000041B': 'KBDSL.DLL',
 '0000041C': 'KBDAL.DLL',
 '0000041D': 'KBDSW.DLL',
 '0000041E': 'KBDTH0.DLL',
 '0000041F': 'KBDTUQ.DLL',
 '00000420': 'KBDURDU.DLL',
 '00000423': 'KBDBLR.DLL',
 '00000424': 'KBDCR.DLL',
 '00000425': 'KBDEST.DLL',
 '00000428': 'KBDTAJIK.DLL',
 '00000429': 'KBDFA.DLL',
 '0000042C': 'KBDAZEL.DLL',
 '00000432': 'KBDNSO.DLL',
 '00000439': 'KBDINDEV.DLL',
 '0000043A': 'KBDMLT47.DLL',
 '0000043B': 'KBDNO1.DLL',
 '0000043F': 'KBDKAZ.DLL',
 '00000440': 'KBDKYR.DLL',
 '00000442': 'KBDTURME.DLL',
 '00000445': 'KBDINBEN.DLL',
 '00000446': 'KBDINPUN.DLL',
 '00000447': 'KBDINGUJ.DLL',
 '00000448': 'KBDINORI.DLL',
 '0000044A': 'KBDINTEL.DLL',
 '0000044B': 'KBDINKAN.DLL',
 '0000044C': 'KBDINMAL.DLL',
 '0000044D': 'KBDINASA.DLL',
 '0000044E': 'KBDINMAR.DLL',
 '00000450': 'KBDMON.DLL',
 '00000451': 'KBDTIPRC.DLL',
 '00000452': 'KBDUKX.DLL',
 '00000453': 'KBDKHMR.DLL',
 '00000454': 'KBDLAO.DLL',
 '0000045A': 'KBDSYR1.DLL',
 '0000045B': 'KBDSN1.DLL',
 '0000045C': 'KBDCHER.DLL',
 '00000461': 'KBDNEPR.DLL',
 '00000463': 'KBDPASH.DLL',
 '00000465': 'KBDDIV1.DLL',
 '00000468': 'KBDHAU.DLL',
 '0000046A': 'KBDYBA.DLL',
 '0000046C': 'KBDNSO.DLL',
 '0000046D': 'KBDBASH.DLL',
 '0000046E': 'KBDSF.DLL',
 '00000470': 'KBDIBO.DLL',
 '00000474': 'KBDGN.DLL',
 '00000475': 'KBDHAW.DLL',
 '00000481': 'KBDMAORI.DLL',
 '00000485': 'KBDYAK.DLL',
 '00000488': 'KBDWOL.DLL',
 '00000492': 'KBDKURD.DLL',
 '00000807': 'KBDSG.DLL',
 '00000809': 'KBDUK.DLL',
 '0000080A': 'KBDLA.DLL',
 '0000080C': 'KBDBE.DLL',
 '00000813': 'KBDBE.DLL',
 '00000816': 'KBDPO.DLL',
 '0000081A': 'KBDYCL.DLL',
 '0000082C': 'KBDAZE.DLL',
 '0000083B': 'KBDFI1.DLL',
 '00000843': 'KBDUZB.DLL',
 '0000085D': 'KBDIULAT.DLL',
 '0000085F': 'KBDTZM.DLL',
 '00000C1A': 'KBDYCC.DLL',
 '00000C51': 'KBDDZO.DLL',
 '00001009': 'KBDCA.DLL',
 '0000100C': 'KBDSF.DLL',
 '0000105F': 'KBDTIFI.DLL',
 '00001409': 'KBDMAORI.DLL',
 '00001809': 'KBDIR.DLL',
 '0000201A': 'KBDBHC.DLL',
 '00004009': 'KBDINEN.DLL',
 '00010418': 'KBDROST.DLL',
 '00010427': 'KBDLT1.DLL',
 '0001042F': 'KBDMACST.DLL',
 '00010437': 'kbdgeoqw.dll',
 '00010439': 'KBDINHIN.DLL',
 '00010444': 'KBDTT102.DLL',
 '00010451': 'KBDTIPRD.DLL',
 '0001045C': 'KBDCHERP.DLL',
 '0001045D': 'KBDINUK2.DLL',
 '00010480': 'KBDUGHR1.DLL',
 '0001083B': 'KBDFI1.DLL',
 '00010850': 'KBDMONST.DLL',
 '00011809': 'KBDGAE.DLL',
 '00020401': 'KBDA3.DLL',
 '00020409': 'KBDUSX.DLL',
 '0002040D': 'kbdhebl3.dll',
 '00020422': 'KBDUR1.DLL',
 '00020426': 'KBDLVST.DLL',
 '0002042B': 'kbdarmph.dll',
 '0002042E': 'KBDSORS1.DLL',
 '00020445': 'KBDINBE2.DLL',
 '00020449': 'KBDTAM99.DLL',
 '00030402': 'KBDBULG.DLL',
 '0003042B': 'kbdarmty.dll',
 '00050429': 'kbdfar.dll',
 '00090C00': 'kbdnko.dll',
 '00110C00': 'KBDJAV.DLL',
 '00130C00': 'KBDMYAN.DLL'}

#: KLID -> Windows' display name of the keyboard.
NAMES = {'0000040D': 'Hebrew', '0000042A': 'Vietnamese', '00000401': 'Arabic (101)',
 '00000405': 'Czech',
 '00000406': 'Danish',
 '00000407': 'German',
 '00000408': 'Greek',
 '00000409': 'US',
 '0000040A': 'Spanish',
 '0000040B': 'Finnish',
 '0000040C': 'French (Legacy, AZERTY)',
 '0000040E': 'Hungarian',
 '0000040F': 'Icelandic',
 '00000410': 'Italian',
 '00000411': 'Japanese',
 '00000412': 'Korean',
 '00000414': 'Norwegian',
 '00000415': 'Polish (Programmers)',
 '00000416': 'Portuguese (Brazilian ABNT)',
 '00000419': 'Russian',
 '0000041A': 'Standard',
 '0000041B': 'Slovak',
 '0000041C': 'Albanian',
 '0000041D': 'Swedish',
 '0000041E': 'Thai Kedmanee',
 '0000041F': 'Turkish Q',
 '00000420': 'Urdu',
 '00000423': 'Belarusian',
 '00000424': 'Slovenian',
 '00000425': 'Estonian',
 '00000428': 'Tajik',
 '00000429': 'Persian',
 '0000042C': 'Azeri Latin',
 '00000432': 'Setswana',
 '00000439': 'Devanagari - INSCRIPT',
 '0000043A': 'Maltese 47-Key',
 '0000043B': 'Norwegian with Sami',
 '0000043F': 'Kazakh',
 '00000440': 'Kyrgyz Cyrillic',
 '00000442': 'Turkmen',
 '00000445': 'Bengali',
 '00000446': 'Punjabi',
 '00000447': 'Gujarati',
 '00000448': 'Oriya',
 '0000044A': 'Telugu',
 '0000044B': 'Kannada',
 '0000044C': 'Malayalam',
 '0000044D': 'Assamese - INSCRIPT',
 '0000044E': 'Marathi',
 '00000450': 'Mongolian Cyrillic',
 '00000451': 'Tibetan (PRC)',
 '00000452': 'United Kingdom Extended',
 '00000453': 'Khmer',
 '00000454': 'Lao',
 '0000045A': 'Syriac',
 '0000045B': 'Sinhala',
 '0000045C': 'Cherokee Nation',
 '00000461': 'Nepali',
 '00000463': 'Pashto (Afghanistan)',
 '00000465': 'Divehi Phonetic',
 '00000468': 'Hausa',
 '0000046A': 'Yoruba',
 '0000046C': 'Sesotho sa Leboa',
 '0000046D': 'Bashkir',
 '0000046E': 'Luxembourgish',
 '00000470': 'Igbo',
 '00000474': 'Guarani',
 '00000475': 'Hawaiian',
 '00000481': 'Maori',
 '00000485': 'Sakha',
 '00000488': 'Wolof',
 '00000492': 'Central Kurdish',
 '00000807': 'Swiss German',
 '00000809': 'United Kingdom',
 '0000080A': 'Latin American',
 '0000080C': 'Belgian French',
 '00000813': 'Belgian (Period)',
 '00000816': 'Portuguese',
 '0000081A': 'Serbian (Latin)',
 '0000082C': 'Azeri Cyrillic',
 '0000083B': 'Swedish with Sami',
 '00000843': 'Uzbek Cyrillic',
 '0000085D': 'Inuktitut - Latin',
 '0000085F': 'Central Atlas Tamazight',
 '00000C1A': 'Serbian (Cyrillic)',
 '00000C51': 'Dzongkha',
 '00001009': 'Canadian French',
 '0000100C': 'Swiss French',
 '0000105F': 'Tifinagh (Basic)',
 '00001409': 'NZ Aotearoa',
 '00001809': 'Irish',
 '0000201A': 'Bosnian (Cyrillic)',
 '00004009': 'India',
 '00010418': 'Romanian (Standard)',
 '00010427': 'Lithuanian',
 '0001042F': 'Macedonian (North Macedonia) - Standard',
 '00010437': 'Georgian (QWERTY)',
 '00010439': 'Hindi Traditional',
 '00010444': 'Tatar',
 '00010451': 'Tibetan (PRC) - Updated',
 '0001045C': 'Cherokee Nation Phonetic',
 '0001045D': 'Inuktitut - Naqittaut',
 '00010480': 'Uyghur',
 '0001083B': 'Finnish with Sami',
 '00010850': 'Traditional Mongolian (Standard)',
 '00011809': 'Gaelic',
 '00020401': 'Arabic (102) AZERTY',
 '00020409': 'United States-International',
 '0002040D': 'Hebrew (Standard)',
 '00020422': 'Ukrainian (Enhanced)',
 '00020426': 'Latvian (Standard)',
 '0002042B': 'Armenian Phonetic',
 '0002042E': 'Sorbian Standard',
 '00020445': 'Bengali - INSCRIPT',
 '00020449': 'Tamil 99',
 '00030402': 'Bulgarian',
 '0003042B': 'Armenian Typewriter',
 '00050429': 'Persian (Standard)',
 '00090C00': 'N’Ko',
 '00110C00': 'Javanese',
 '00130C00': 'Myanmar (Visual Order)'}

#: Layout file -> SHA-256 of the reference build's file (see the top): anything else is refused.
PINS = {'kbdheb.dll': '9987348eb6893f42d221c8eaf78014d43af8f249fac9b3cac488972ad97f926c',
 'kbdvntc.dll': 'd39f4cb20a107642f3a2bea47a4eb8c4f7945ef89808b438f0acf0cf7695df92',
 'kbda1.dll': 'b34073feb06d17ea882fecf64abd9c2da922f16ab9f09327f40881d7391ef46f',
 'kbda3.dll': 'd9eb5282d68cbefec76e3738a3ae93a2ccc63cf1bc579b240694802c4e3a8902',
 'kbdal.dll': 'ed647420167fa89432c12bea555826e8256ddcfef0bd8df401451f0f8ad5007d',
 'kbdarmph.dll': 'f14b11253e4bc867d0c81f3ddefa451ee22b8c09b25a99d15841f85880ec2141',
 'kbdarmty.dll': 'afb63e7baec6f3f9e26f6cd762d10404f596b9461cf9ea26aa023780f9bc9c8c',
 'kbdaze.dll': 'c749c3023436d38bedda0332e50aceb6e3c45db1b60879edb177e809444444c3',
 'kbdazel.dll': '4c99a957787789ab06dc1d3e7b74c5d3a7af0a48cc6e559cb7e628b2927443cb',
 'kbdbash.dll': 'b4279edfd93b80faf5702780dcdb7820ee86cb262d1ebde06cb394a786e3e5d0',
 'kbdbe.dll': '20d99cdbd712e36986a23fbe59e26996c5c4ed9de8c626361e1f15c59dc3cb06',
 'kbdbhc.dll': '61bf4129226a388864f410a4210674e606b4982724a47fa5f0290943548f94a8',
 'kbdblr.dll': 'e1d3fef3ffdadd270af18d6a734295fa435357b004ad2f7f616c4dc71f207ef6',
 'kbdbr.dll': 'bdb062bb7b14fab64787ee43d98b77b89e50533d8388aa9a4eadcec22c1171c3',
 'kbdbulg.dll': 'eee639c6ad2bdbb3b68332b33e6de73e42f53db6618e52a21f9a01f1f3ae220b',
 'kbdca.dll': 'bb2f46d05b9b3538fc5cb7315bf5ef9bddab77b69a53416f6eb27edbed15e385',
 'kbdcher.dll': 'd1f2b6ac885081b661ef4834c2b47690e1ffe5a49ce37e2f9f8736812776381a',
 'kbdcherp.dll': '4e2f9c06f4c1e377f135add562921a78fabda28455120146306bd69d886d893e',
 'kbdcr.dll': '241fd7a96ba77eb2f097954ee4bdd051919edf1b56eb171d861cb58080697558',
 'kbdcz.dll': 'cbe7fed69e938268765a9619dbf56d375c58ad2a0deb99941a75cb243069d931',
 'kbdda.dll': '72f6faecc5c92e2bf527adf97c42d8172cdb3173351e15cdb4e48c92c2d4f477',
 'kbddiv1.dll': 'bfe08dd006ee7693146c972bdb2af6bd10cbfbe1d4b24079d431eb973c995441',
 'kbddzo.dll': 'c04bc2fc0544a115560f16c834e92eceb1899a4d946baa922edd924dfceece25',
 'kbdest.dll': 'b86190c80637e7ab22536cb33ecb77ab78bf39779758495bdd81f0166e87eaa2',
 'kbdfa.dll': '18e79d7a0c66256c06d16feba97a840d6bd460cb7df0d4f157dae57cc1a1378a',
 'kbdfar.dll': 'fe3724fff2b893adbaa1f9a950499643a195f02ea1a14ee3418e8c86194116dd',
 'kbdfi.dll': '3e34819b2342003e2c2ea287001851f633a4ccf9b5ef3f0546baff7738d3ad5e',
 'kbdfi1.dll': '094c206b6e146568b770e1c9cf13cec5d3772fa157a9e7ad2ea400d4b30a2853',
 'kbdfr.dll': '01043070ee3a81ba51b31c4c9c60cce69891e271503054abefb14b6e9fdb1b0e',
 'kbdgae.dll': '0c82f618583f640d2cb0cb20122aabb639a256e5b755c61ae4963bf0075555d5',
 'kbdgeoqw.dll': '3fc4eef2d8aa5db904dec6d8772020008c5cb8cd7fc92a5f27b37ee26f8762c5',
 'kbdgn.dll': '0acc2d0bdd75e44a98f7801f678ffe3e01de5e21d8ba3c09efc4736cf3e5f54c',
 'kbdgr.dll': 'b536a9426a3f7ca2e77edbfd3ffa34c900b3b5ea018bb0c107d7925a3389fcd7',
 'kbdhau.dll': '3273e563d4a8c71b65dd69233d3bc0907cc27919fdd458455ac8da6d0d47a8fd',
 'kbdhaw.dll': '3046874eefeca690fb2d7c7daf243d7cdf05063ce53efbeb4a15c7306c7c079c',
 'kbdhe.dll': '785a286babe596b78dc52db59e5b819bbe9e462eb243d803447db1ff8a957878',
 'kbdhebl3.dll': '17260353021b288462a81f0567f14c3073ee41d8accba97b8c12a101d1c144e7',
 'kbdhu.dll': 'aeaf735136fa9a6c9b71010f639c05b2f4a6edd21c35f9817d963705cbee054b',
 'kbdibo.dll': '6bc2e57188abc73d64fe6fc960434b580d58d26fbd89b585d135ddfc34fa1d47',
 'kbdic.dll': '84185d06a59d4fd7bed07de7f3e3c09a1a23ec4904d23e923c715f99f8f8d55e',
 'kbdinasa.dll': 'bc7c9489d6f6483a12ce8f2d0a05e0627525cc2cbee1aa449da4cb1fa000d1d9',
 'kbdinbe2.dll': 'af9ed6994e8e4311b6fa59e621ef9d66f520e8d7e95c3cd79b0bc9b8e33e7007',
 'kbdinben.dll': 'ce31c78aced2434c867b4ec014b7d9f171e5a1e5e1b3d5cd7a935b42d28ba978',
 'kbdindev.dll': 'bbfc32afe3823f25887dd74802085e96319814e3a1458e3270ddb33d08daa94b',
 'kbdinen.dll': '32a82e2bb8f822b3da7dc180a8be775cdf7630bc55942f4b85ee729bda7e4d10',
 'kbdinguj.dll': '3cf1ccf0d80f8d6559442e83dc2199e4454e5b8be96487768c2984eb95569cd8',
 'kbdinhin.dll': '91e4c77a833147be73c5da2baa9eb947fba5c55903c4ed274a27488fbc0f2f83',
 'kbdinkan.dll': 'ff6e84e8bf281491262485ed775f81362e6dbb24351e145f7e82d7e8a916c724',
 'kbdinmal.dll': '7d9b74a680b1fc6d6efc53fcb7d2fe7ade6385a576e98f0a1b612ad4996f35a7',
 'kbdinmar.dll': 'b063dd79c4e3232a7c668c60abb4fdb833c3b10124a9e728d140d90bf99f0b92',
 'kbdinori.dll': '49769ebaaac1f2a2f766bd79d78b66cfb0bf0c0e055f57eb378856b28d1c0e6c',
 'kbdinpun.dll': 'fdeb917cb389408f3a5a4174c588ee507f972c133becb4455c9079c86cea49b7',
 'kbdintel.dll': 'a588f22801b67fc9bf5f144aed53c329e1d676bd7e5289c5618be9e7ab15c8d4',
 'kbdinuk2.dll': '3a57d35b1bf1e088a212ad18cd40a715b3289b76e38b7f48fcdb818d5d9a381e',
 'kbdir.dll': '7e02611c92054388c69b5fc7ebb71bd853b97c27a2f769c207f68465ac6f9c90',
 'kbdit.dll': '9c37ffddd3ffd389a2e4564427f77c5d3ea51e4aebd2cfed67ff5afb2f2b0dfc',
 'kbdiulat.dll': '5ee36c4477a5977f2c1dba425f4a7e2a28ddf8da89474e68c5c4756a3b6c1688',
 'kbdjav.dll': '40f965e9b4c1f17af41c21cb9cfc2d54d5570f6ffd724cf6d1e1a5745d070b37',
 'kbdjpn.dll': '5293b7b0453858137b32a8cf5f294fc2ac9e3bb673fa050c47c2d1600176737a',
 'kbdkaz.dll': '860bc98eb60831cd559eb25ffce65d489065fb6afd52535e9fa4c2c8b76c05f9',
 'kbdkhmr.dll': 'c6ea8191bfcdadb4b63ef8fcca076e00287bd63d77ad6113a0bfd97cd0286892',
 'kbdkor.dll': '4f8e04942f6067a8f6def16a53e20eb0253c31a011fb677cf99e4a510e1d65c3',
 'kbdkurd.dll': 'dc0e80319e378480cfa14ca1f62332bde9b7f48d09987bdbc7f0a97c000e2e85',
 'kbdkyr.dll': '29ade97e6590586a819e7baa1e13295287c267268d9c3c66d09ac7db1f226e46',
 'kbdla.dll': '068f946726d57bd80be53c2f32dc2112db621124dd044bbb093b1542121b7411',
 'kbdlao.dll': '85fac9513c917fd1387ef8434480b9a9bb311bbbcbc6b82f9c88b91d21f7718d',
 'kbdlt1.dll': '7becbc28654bd5ad31519935f1c266604274a5e63085a2f3d84d9107b4b3895b',
 'kbdlvst.dll': '482fb041e951796622e28558b668ff66ec23a4969304083e9c35d2b83d4a67fa',
 'kbdmacst.dll': 'f5281c871642d65f35a64a0d14d68c848dcf339e5179daef75bdc2bc5d81fbac',
 'kbdmaori.dll': 'cbafb80d3ca1e07d096a23e5ff77e1e066b6fd59fc182dfeeedbc79efde76a79',
 'kbdmlt47.dll': '18063f5b1cf5df7b3cf30c638d08ec14d418fb10dee5cf2927722921a326e665',
 'kbdmon.dll': '60765f070fba2c93d84688f7a439e12382ee45df852c98bba69e3dad312c9b6a',
 'kbdmonst.dll': '9df45e517e6d4e293136c8f4f12e24104c766a73fb795bf4d12a6ecb01e90915',
 'kbdmyan.dll': 'f77bbe801fd49b0b172cf1cce61d3e58a60695278d98245b581e5061e1050337',
 'kbdnepr.dll': '07688c8b5c6a5884a027a847499e3f01949291b467296a2a09acf6754549b899',
 'kbdnko.dll': '293b8b5a827a8bbb172116f181357a30b3e012419f5664fdb15233d0ffdb8bab',
 'kbdno.dll': '4dbdca8fa0e2bccc6ff13f88cb2bf646c2f3214e6fe482b5182ed24a367da493',
 'kbdno1.dll': '17a269d220bdde447624515ea6e295486e541645962989e9e526335fdd602cfd',
 'kbdnso.dll': 'faadb483e3b5910518f2f4c87298aebe332b2ad93303b67c476fb799f8fc25b5',
 'kbdpash.dll': 'ef84d2d3142c1858d2abe595dfe7176a3c5108c772e0cbf233a4c6de9f00f320',
 'kbdpl1.dll': '0d718c5ed311fa34fa2f92b13cbc61e198c516d41cc3097517ea370832947da3',
 'kbdpo.dll': '331307df4d17e571bf4dab992886667caf73074bcbe5c78a0af645b77f37f223',
 'kbdrost.dll': 'dda16662b87446d649c6781a572bbcc0475bc44426a0b6aab2775e7df9f8ebcd',
 'kbdru.dll': '97355982efcdcae4cef7f95f32df1d963a93be93e23f23459615c3b19bf3f753',
 'kbdsf.dll': 'c923dfd9b6af67dcd82fc41065d0b0cec52524d79aace269844b0995e6be7098',
 'kbdsg.dll': '1ea7b18b1d1abd8a5f917b6bfc059931dd1bc99e5469a4240f2e9ed4e381eb73',
 'kbdsl.dll': '0f7fc9e8ae48ee21da19d2afa8a77aaf4f799c3f194b6b9622e4eb93a3592c54',
 'kbdsn1.dll': '0a900cab3b7f55806749b2873235382ec76796c408b21ad8e8938f316c42bc9c',
 'kbdsors1.dll': '363d26c749088ddb6195222503f707fcb008b67c9ca2038762a7a13ff583cdfc',
 'kbdsp.dll': '52f5ee55ee2160602bc79da6e1d4ab64937495000aebaf56e3a1d5ba8d8000c9',
 'kbdsw.dll': '57767aba5f4a43c38ff30b04205f3cf5eb5e1edbb407715da0d378336e56c190',
 'kbdsyr1.dll': '5fb93a65e8e2807a7146f3fca70d2608d0d5ae427b17829054698e21add82044',
 'kbdtajik.dll': '59ad6df73c366ffcbae0fb8b8fda9ad3cc13c9e3c685c6455a4a789529736424',
 'kbdtam99.dll': '0e69865a4d1ffd7b7d8ed0a3f5cf001a9fac17d5e827b078d8458f242b056b95',
 'kbdth0.dll': '679fad1adbe4e39ec1e2c6d4b2c525576bc18a7c74a34349cfd9a6bb948f2a1e',
 'kbdtifi.dll': '7d80d883c07b0be018b9e12b3ec9d05bc95d7d4190c37a8ee0bd6fa23778c40d',
 'kbdtiprc.dll': '8426e75075b993944f189bca263f98784e2777fbc93da1b7228a8c549baaa1ce',
 'kbdtiprd.dll': '56ce665492a598c66ba5b762abea8824d2fd6ca14114e55f667491f0fc5a679d',
 'kbdtt102.dll': '139a5ff4158b16c505259a41d3ac28cf9e464a0a740a06b381c2f6377a822fae',
 'kbdtuq.dll': '89df1018e908f17fa30b20517f34550838d90be3846954620c0bd19fa9efe17e',
 'kbdturme.dll': '5aa8256ef86af18e94c1a58e85e6f5b846050899f5f76a3acde7cce0a07f3694',
 'kbdtzm.dll': '326c45b79fdc01bb67384937d3d903d4826d7657a9794e4ff0d6c908fe623b82',
 'kbdughr1.dll': 'b89765f04710e213917914a31f8ea4b4e349ff93e892e30fc50ce02342a4005d',
 'kbduk.dll': '658c27d74cfd03e959196260a603b9e07fdd3e0b6e9fd0535c251ddb5a1b2a6b',
 'kbdukx.dll': '964dc102db5a402292f32042c22e4e7718cbcac11361545ebb643cabf7a8f140',
 'kbdur1.dll': '1f929ea08fa43c31fe7db4bb8ca5a7b2fe9958293b91ae61dae7e032cd676393',
 'kbdurdu.dll': '8c412c9d05b4a4bd03361a06309d60e250d3b0e39fea30b140cd3f4a684d4c59',
 'kbdus.dll': 'a1b3947113e615b73aa601be4625a8ad6eba2755c5fa09d2a65def0b90acd0a1',
 'kbdusx.dll': '15c2d605ffa55589be658db3a9abf7afe596a6c7c5ff90f68c89e36a8a0b75f5',
 'kbduzb.dll': '8cbb1895fc2c8ac6bc63550e505db7ebe29c1d6ac3a0f8febe18a342eb8ff25d',
 'kbdwol.dll': '0a42bd4c01908b13fa02449d3394e332ca82f95f39a77cb683bbf852614d3232',
 'kbdyak.dll': '3f7b71c780216205bf63e63c20f1d29487952af17f29a6181e6ebdc365d2a967',
 'kbdyba.dll': '568ba7adf5defe3d0373b84fd0d680543d58be492eb1381812d255a90e331e24',
 'kbdycc.dll': 'a94fc7d895976472d3994371ca6d3f48d029495660064ea12d20cfbdab8d7433',
 'kbdycl.dll': '2b87e3d711124f0940ebdbf09929dd59e5591a84ed3e80ad29a4798b0db367a5'}


# ── the layout file, read as bytes ──────────────────────────────────────────

class _PE:
    def __init__(self, data: bytes) -> None:
        self.d = data
        e = struct.unpack_from("<I", data, 0x3C)[0]
        if data[e:e + 4] != b"PE\0\0":
            raise ValueError("not a PE file")
        opt = e + 24
        if struct.unpack_from("<H", data, opt)[0] != 0x20B:
            raise ValueError("not an x64 image")
        self.base = struct.unpack_from("<Q", data, opt + 24)[0]
        self.exp_rva = struct.unpack_from("<I", data, opt + 112)[0]
        sh = opt + struct.unpack_from("<H", data, e + 20)[0]
        self.secs = []
        for i in range(struct.unpack_from("<H", data, e + 6)[0]):
            vsize, va, rsize, raw = struct.unpack_from("<IIII", data, sh + i * 40 + 8)
            self.secs.append((va, max(vsize, rsize), raw))

    def off(self, rva: int) -> int:
        for va, size, raw in self.secs:
            if va <= rva < va + size:
                return raw + rva - va
        raise ValueError("rva %#x outside every section" % rva)

    def va(self, ptr: int) -> int:
        return self.off(ptr - self.base)

    def u8(self, o): return self.d[o]
    def u16(self, o): return struct.unpack_from("<H", self.d, o)[0]
    def u64(self, o): return struct.unpack_from("<Q", self.d, o)[0]

    def export(self, name: str) -> int:
        o = self.off(self.exp_rva)
        n_names = struct.unpack_from("<I", self.d, o + 24)[0]
        f_rva, n_rva, o_rva = struct.unpack_from("<III", self.d, o + 28)
        for i in range(n_names):
            nr = self.off(struct.unpack_from("<I", self.d, self.off(n_rva) + 4 * i)[0])
            if self.d[nr:self.d.index(b"\0", nr)].decode() == name:
                ordi = struct.unpack_from("<H", self.d, self.off(o_rva) + 2 * i)[0]
                return struct.unpack_from("<I", self.d, self.off(f_rva) + 4 * ordi)[0]
        raise KeyError(name)


def read_kbdtables(data: bytes) -> dict:
    """KBDTABLES of one layout file: modifier columns, scan code -> VK, VK -> characters."""
    pe = _PE(data)
    f = pe.export("KbdLayerDescriptor")
    fo = pe.off(f)
    code = pe.d[fo:fo + 8]
    if code[:3] != b"\x48\x8d\x05" or code[7] != 0xC3:  # lea rax,[rip+d]; ret
        raise ValueError("unexpected KbdLayerDescriptor: %s" % code.hex())
    t = pe.off(f + 7 + struct.unpack_from("<i", code, 3)[0])
    m = pe.va(pe.u64(t))
    max_bits = pe.u16(m + 8)
    mod_number = [pe.u8(m + 10 + i) for i in range(max_bits + 1)]
    vsc, max_vsc = pe.va(pe.u64(t + 0x30)), pe.u8(t + 0x38)
    sc_to_vk = {sc: pe.u16(vsc + 2 * sc) & 0xFF for sc in range(max_vsc)}
    chars = {}
    o = pe.va(pe.u64(t + 8))
    while pe.u64(o):
        r, n, cb = pe.va(pe.u64(o)), pe.u8(o + 8), pe.u8(o + 9)
        while pe.u8(r):
            vk = pe.u8(r)
            if vk != 0xFF:  # 0xFF rows carry the dead characters of the row above
                chars[vk] = [pe.u16(r + 2 + 2 * i) for i in range(n)]
            r += cb
        o += 16
    return {"mod_number": mod_number, "sc_to_vk": sc_to_vk, "chars": chars}


def _column(mod_number, bits: int) -> int:
    return mod_number[bits] if bits < len(mod_number) else 0x0F


def _keycode_of_char(ch: str) -> int:
    if "A" <= ch <= "Z" or "a" <= ch <= "z":
        return ord(ch.upper())
    if "0" <= ch <= "9":
        return ord(ch)
    return KEYCODE_OF_CHAR.get(ch, 0)


def _keycode(vk: int, unshifted: str, shifted: str) -> int:
    """`KeyboardLayout::ConvertNativeKeyCodeToDOMKeyCode` for a printable key."""
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A or 0x60 <= vk <= 0x87 or vk == VK_SPACE:
        return vk
    if vk == VK_ABNT_C2:
        return 0x6C  # NS_VK_SEPARATOR
    if vk not in OEM_VKS:
        return 0
    for ch in (unshifted, shifted):
        if ch and " " <= ch <= "\x7f":
            return _keycode_of_char(ch)
    return FALLBACK_KEYCODE.get(vk, 0)


def layout_from_bytes(data: bytes) -> dict:
    kb = read_kbdtables(data)
    mn = kb["mod_number"]
    cols = {"key": _column(mn, 0), "shiftKey": _column(mn, SHIFT),
            "altGrKey": _column(mn, CTRL | ALT), "shiftAltGrKey": _column(mn, SHIFT | CTRL | ALT)}
    keys, has_altgr = {}, False
    for sc, code in sorted(PRINTABLE_SCANCODES.items(), key=lambda kv: kv[1]):
        vk = kb["sc_to_vk"].get(sc)
        if not vk or vk not in kb["chars"]:
            continue
        row = kb["chars"][vk]
        levels, dead = {}, False
        for name, c in cols.items():
            w = row[c] if c != 0x0F and c < len(row) else WCH_NONE
            if w == WCH_DEAD:
                dead, levels[name] = True, None
            elif w in (WCH_NONE, WCH_LGTR) or w < 0x20 or w == 0x7F:
                levels[name] = ""
            else:
                levels[name] = chr(w)
        if not dead and not any(levels.values()):
            continue
        e = {"keyCode": _keycode(vk, levels["key"] or "", levels["shiftKey"] or "")}
        for name, ch in levels.items():
            if ch:
                e[name] = ch
        if "altGrKey" in e or "shiftAltGrKey" in e:
            has_altgr = True
        if dead:
            e["dead"] = True
        keys[code] = e
    return {"hasAltGr": has_altgr, "keys": keys}


# ── which keyboard for which language and region ───────────────────────────

def locale_profiles(data: bytes) -> dict:
    """locale name -> [[LANGID, KLID], ...]: the keyboards-to-install of every
    locale in locale.nls, in Windows' order, IME profiles left out. A locale
    whose first profile is an IME starts with the keyboard under it (IME_BASE)."""
    import struct
    h = struct.unpack_from("<I", data, 4 * 4)[0]          # the file's header: char types, 3 unknown, locales
    if data[h + 0x0C:h + 0x10] != b"NSDS":
        raise SystemExit("locale.nls: no NSDS locale table where its header points")
    locale_size, locales_offset = struct.unpack_from("<HI", data, h + 0x22)
    nb_lcnames = struct.unpack_from("<H", data, h + 0x28)[0]
    lcnames_offset = struct.unpack_from("<I", data, h + 0x30)[0]
    strings = h + struct.unpack_from("<I", data, h + 0x40)[0]

    def string(pos):
        n = struct.unpack_from("<H", data, strings + 2 * pos)[0]
        return data[strings + 2 * pos + 2:strings + 2 * pos + 2 + 2 * n].decode("utf-16le")

    out = {}
    for i in range(nb_lcnames):
        name_pos, idx, _lcid = struct.unpack_from("<HHI", data, h + lcnames_offset + 8 * i)
        record = h + locales_offset + idx * locale_size
        field = struct.unpack_from("<I", data, record + 0x128)[0]   # skeyboardstoinstall
        profiles, ime_lang = [], None
        for item in string(field).split(";"):
            if ":" not in item:
                continue
            lang, rest = item.split(":", 1)
            if rest.startswith("{"):
                ime_lang = ime_lang or lang.lower()
            else:
                profiles.append([lang.lower(), rest.upper()])
        name = string(name_pos)
        # An IME first stays first as the keyboard under it, the one Latin text
        # is typed on (IME_BASE), as before: IMEs are not modelled here.
        first_is_ime = string(field).split(";")[0].split(":", 1)[-1].startswith("{")
        base = IME_BASE.get(name.split("-")[0].lower())
        if first_is_ime and base:
            profiles = [[ime_lang, base]] + [p for p in profiles if p != [ime_lang, base]]
        if profiles:
            out[name] = profiles
    return out


def build(dll_dir: pathlib.Path, nls: pathlib.Path) -> dict:
    nls_data = nls.read_bytes()
    if hashlib.sha256(nls_data).hexdigest() != NLS_PIN:
        raise SystemExit("refused, not the pinned Windows locale.nls: %s" % nls)
    profiles = locale_profiles(nls_data)
    klids = sorted({klid for ps in profiles.values() for _lang, klid in ps})
    layouts, refused = {}, []
    for klid in klids:
        name = FILES[klid].lower()
        data = (dll_dir / FILES[klid]).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != PINS[name]:
            refused.append("%s: sha256 %s, pinned %s" % (name, digest, PINS[name]))
            continue
        layouts[klid] = layout_from_bytes(data)
    if refused:
        raise SystemExit("refused, not the pinned Windows files:\n  " + "\n  ".join(refused))
    return {"layouts": layouts, "profiles": profiles,
            "first": {name: ps[0][1] for name, ps in profiles.items()},
            "names": {k: NAMES[k] for k in klids}}


def render(b: dict) -> str:
    def lit(o):
        return pprint.pformat(o, width=100, sort_dicts=True)
    return (
        '"""Every keyboard a Windows persona can type on. GENERATED by\n'
        "`python scripts/gen_windows_keylayouts.py` from the bytes of the pinned\n"
        "layout files and locale.nls of Windows 11 26H2, build 26300.9711 (26100 base);\n"
        "do not hand-edit.\n\n"
        "LAYOUTS: KLID -> {hasAltGr, keys: DOM code -> keyCode, key, shiftKey, altGrKey,\n"
        "shiftAltGrKey, dead}. LOCALE_KEYBOARDS: locale name -> [[LANGID, KLID], ...], the\n"
        "keyboards Windows installs with it, in its order. LOCALE_KLID: locale name -> the\n"
        "first of them.\n"
        '"""\n'
        "from __future__ import annotations\n\n"
        "#: %d layouts.\nLAYOUT_NAMES = %s\n\nLOCALE_KLID = %s\n\nLOCALE_KEYBOARDS = %s\n\n"
        "LAYOUTS = %s\n"
        % (len(b["layouts"]), lit(b["names"]), lit(b["first"]), lit(b["profiles"]),
           lit(b["layouts"])))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dll-dir", type=pathlib.Path, default=DEFAULT_DLL_DIR)
    ap.add_argument("--nls", type=pathlib.Path, default=DEFAULT_NLS)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = render(build(a.dll_dir, a.nls))
    if a.check:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print(("fresh: " if same else "stale: ") + str(OUT))
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

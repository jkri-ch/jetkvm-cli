"""Translate text and key combos into USB HID keyboard reports.

A report is a (keycode, modifier) pair: ``keycode`` is a HID usage ID from the
Keyboard/Keypad page and ``modifier`` is the HID modifier bitmask.

The host OS interprets keycodes through *its own* keymap, so the layout chosen
here must match the keyboard layout configured on the machine behind the KVM.
"""

from __future__ import annotations

SHIFT = 0x02

MODIFIERS = {
    "ctrl": 0x01, "control": 0x01, "lctrl": 0x01,
    "shift": 0x02, "lshift": 0x02,
    "alt": 0x04, "lalt": 0x04, "option": 0x04,
    "meta": 0x08, "win": 0x08, "super": 0x08, "cmd": 0x08, "lmeta": 0x08,
    "rctrl": 0x10,
    "rshift": 0x20,
    "ralt": 0x40, "altgr": 0x40,
    "rmeta": 0x80,
}

NAMED_KEYS = {
    "enter": 0x28, "return": 0x28,
    "esc": 0x29, "escape": 0x29,
    "backspace": 0x2A,
    "tab": 0x2B,
    "space": 0x2C,
    "capslock": 0x39,
    "printscreen": 0x46, "prtsc": 0x46, "sysrq": 0x46,
    "scrolllock": 0x47,
    "pause": 0x48, "break": 0x48,
    "insert": 0x49, "ins": 0x49,
    "home": 0x4A,
    "pageup": 0x4B, "pgup": 0x4B,
    "delete": 0x4C, "del": 0x4C,
    "end": 0x4D,
    "pagedown": 0x4E, "pgdn": 0x4E,
    "right": 0x4F,
    "left": 0x50,
    "down": 0x51,
    "up": 0x52,
    "numlock": 0x53,
    "menu": 0x65, "application": 0x65,
}
NAMED_KEYS.update({f"f{n}": 0x3A + n - 1 for n in range(1, 13)})


def _us() -> dict[str, tuple[int, int]]:
    m: dict[str, tuple[int, int]] = {}
    for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
        m[c] = (0x04 + i, 0)
        m[c.upper()] = (0x04 + i, SHIFT)
    for i, c in enumerate("1234567890"):
        m[c] = (0x1E + i, 0)
    for i, c in enumerate("!@#$%^&*()"):
        m[c] = (0x1E + i, SHIFT)
    plain = {"\n": 0x28, "\t": 0x2B, " ": 0x2C, "-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30,
             "\\": 0x31, ";": 0x33, "'": 0x34, "`": 0x35, ",": 0x36, ".": 0x37, "/": 0x38}
    shifted = {"_": 0x2D, "+": 0x2E, "{": 0x2F, "}": 0x30, "|": 0x31, ":": 0x33, '"': 0x34,
               "~": 0x35, "<": 0x36, ">": 0x37, "?": 0x38}
    m.update({c: (k, 0) for c, k in plain.items()})
    m.update({c: (k, SHIFT) for c, k in shifted.items()})
    return m


def _uk() -> dict[str, tuple[int, int]]:
    m = _us()
    m.update({
        '"': (0x1F, SHIFT),   # shift+2
        "£": (0x20, SHIFT),   # shift+3
        "@": (0x34, SHIFT),   # shift+'
        "#": (0x32, 0),       # non-US #~ key
        "~": (0x32, SHIFT),
        "\\": (0x64, 0),      # non-US \| key
        "|": (0x64, SHIFT),
        "¬": (0x35, SHIFT),   # shift+`
    })
    return m


LAYOUTS: dict[str, dict[str, tuple[int, int]]] = {"us": _us(), "uk": _uk()}


def _layout(name: str) -> dict[str, tuple[int, int]]:
    try:
        return LAYOUTS[name]
    except KeyError:
        raise ValueError(f"unknown layout {name!r} (available: {', '.join(LAYOUTS)})") from None


def text_to_strokes(text: str, layout: str = "us") -> list[tuple[int, int]]:
    """Return the (keycode, modifier) strokes needed to type ``text``.

    ``\\n`` becomes Enter; ``\\r`` is ignored so CRLF input types cleanly.
    """
    km = _layout(layout)
    strokes = []
    for ch in text:
        if ch == "\r":
            continue
        if ch not in km:
            raise ValueError(f"cannot type {ch!r} with the {layout!r} layout")
        strokes.append(km[ch])
    return strokes


def parse_combo(combo: str, layout: str = "us") -> tuple[int | None, int]:
    """Parse a combo such as ``ctrl+alt+f2``, ``enter`` or ``shift+tab``.

    Returns ``(keycode, modifier)``; keycode is ``None`` for a modifier-only
    combo. Matching is case-insensitive, so add ``shift`` explicitly for
    upper case. Use ``plus`` for the ``+`` key.
    """
    km = _layout(layout)
    code: int | None = None
    mod = 0
    tokens = combo.strip().lower().split("+")
    if not combo.strip() or any(t == "" for t in tokens):
        raise ValueError(f"invalid key combo {combo!r} (use 'plus' for the + key)")
    for token in tokens:
        if token in MODIFIERS:
            mod |= MODIFIERS[token]
            continue
        if code is not None:
            raise ValueError(f"key combo {combo!r} has more than one non-modifier key")
        if token in NAMED_KEYS:
            code = NAMED_KEYS[token]
        elif token == "plus" or (len(token) == 1 and token in km):
            code, extra = km["+" if token == "plus" else token]
            mod |= extra
        else:
            raise ValueError(f"unknown key {token!r} in combo {combo!r}")
    return code, mod

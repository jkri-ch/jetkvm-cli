import pytest

from jetkvm_cli.keymap import MODIFIERS, NAMED_KEYS, SHIFT, parse_combo, text_to_strokes

CTRL, ALT = MODIFIERS["ctrl"], MODIFIERS["alt"]


def test_letters_and_case():
    assert text_to_strokes("aZ") == [(0x04, 0), (0x1D, SHIFT)]


def test_digits_and_shifted_symbols():
    assert text_to_strokes("1!0)") == [(0x1E, 0), (0x1E, SHIFT), (0x27, 0), (0x27, SHIFT)]


def test_newline_is_enter_and_cr_is_dropped():
    assert text_to_strokes("a\r\n") == [(0x04, 0), (0x28, 0)]


@pytest.mark.parametrize("ch, us, uk", [
    ('"', (0x34, SHIFT), (0x1F, SHIFT)),
    ("@", (0x1F, SHIFT), (0x34, SHIFT)),
    ("#", (0x20, SHIFT), (0x32, 0)),
    ("\\", (0x31, 0), (0x64, 0)),
    ("|", (0x31, SHIFT), (0x64, SHIFT)),
    ("~", (0x35, SHIFT), (0x32, SHIFT)),
])
def test_us_vs_uk(ch, us, uk):
    assert text_to_strokes(ch, "us") == [us]
    assert text_to_strokes(ch, "uk") == [uk]


def test_pound_only_on_uk():
    assert text_to_strokes("£", "uk") == [(0x20, SHIFT)]
    with pytest.raises(ValueError, match="cannot type"):
        text_to_strokes("£", "us")


def test_unknown_layout():
    with pytest.raises(ValueError, match="unknown layout"):
        text_to_strokes("a", "dvorak")


def test_untypeable_text_fails_before_anything_is_returned():
    with pytest.raises(ValueError):
        text_to_strokes("ok é", "us")


@pytest.mark.parametrize("combo, expected", [
    ("enter", (0x28, 0)),
    ("ctrl+c", (0x06, CTRL)),
    ("Ctrl+Alt+F2", (0x3B, CTRL | ALT)),
    ("ctrl+alt+delete", (NAMED_KEYS["delete"], CTRL | ALT)),
    ("shift+tab", (0x2B, SHIFT)),
    ("ctrl+plus", (0x2E, CTRL | SHIFT)),
    ("?", (0x38, SHIFT)),
    ("shift", (None, SHIFT)),
])
def test_parse_combo(combo, expected):
    assert parse_combo(combo) == expected


@pytest.mark.parametrize("combo", ["", "ctrl+", "ctrl++", "ctrl+bogus", "a+b"])
def test_parse_combo_rejects(combo):
    with pytest.raises(ValueError):
        parse_combo(combo)

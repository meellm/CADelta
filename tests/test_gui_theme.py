"""Theme/QSS smoke tests: pure-Python, no Qt event loop required.

They pin down that the token table is self-consistent and the QSS builder
references the right selectors, so a typo in a token name fails here rather
than rendering an invisible widget.
"""
from __future__ import annotations

import re

from cadelta.gui.theme import TOKENS, build_qss, color


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_color_tokens_are_valid_hex():
    """Every color-ish token resolves to a #rrggbb string; metric tokens are
    bare numbers. Guards against an accidental empty/garbled value."""
    metric_keys = {"radius", "radius_sm", "pad"}
    for name, value in TOKENS.items():
        if name in metric_keys:
            assert value.isdigit(), f"{name} should be a numeric metric, got {value!r}"
        else:
            assert _HEX_RE.match(value), f"{name} is not valid hex: {value!r}"


def test_color_lookup_raises_on_unknown_token():
    """color() must fail loudly on a typo rather than silently returning a
    falsy value that would paint transparent."""
    assert color("primary") == TOKENS["primary"]
    try:
        color("does_not_exist")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for unknown token")


def test_build_qss_is_nonempty_and_uses_tokens():
    """The generated style sheet should embed the actual token values and
    style the key custom objectName selectors the widgets rely on."""
    qss = build_qss()
    assert isinstance(qss, str) and len(qss) > 500
    # Primary accent + a diff color must appear literally.
    assert TOKENS["primary"] in qss
    assert TOKENS["diff_added"] in qss
    # Selectors the widgets depend on by objectName.
    for selector in ("QPushButton#primary", "QFrame#dropZone", "QProgressBar"):
        assert selector in qss, f"missing selector {selector}"


def test_build_qss_accepts_custom_tokens():
    """build_qss is a pure function of its tokens: swapping a token changes
    the output, so a future light theme can reuse the same builder."""
    custom = dict(TOKENS)
    custom["primary"] = "#abcdef"
    qss = build_qss(custom)
    assert "#abcdef" in qss

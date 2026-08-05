"""
bal.core.input_rules
====================

Pure, GUI-free rules for the plugin's text-input widgets: locktime bounds and
acceptance, the RAW locktime sanitisation ("30d"/"1y"), and the percentage-or-
amount field normalisation.

These used to live inside the Qt widget classes in ``bal.gui.qt.widgets``.
Keeping them here makes them testable without Qt and lets any GUI front-end
reuse the exact same parsing rules.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Tuple, Union

from electrum.bitcoin import NLOCKTIME_BLOCKHEIGHT_MAX, NLOCKTIME_MAX, NLOCKTIME_MIN

__all__ = [
    "NLOCKTIME_BLOCKHEIGHT_MAX",
    "NLOCKTIME_MAX",
    "NLOCKTIME_MIN",
    "LockTimeEditor",
    "normalize_locktime_raw_text",
    "normalize_perc_amount_text",
    "parse_perc_amount",
    "replace_dy_suffixes",
]


class LockTimeEditor:
    """Acceptance bounds shared by the RAW and Date locktime editors.

    A Qt widget mixin historically; the pure parts (bounds and acceptance test)
    live here so they can be tested and reused without Qt. Widget subclasses
    override ``min_allowed_value``/``max_allowed_value`` to tighten the bounds.
    """

    min_allowed_value = NLOCKTIME_MIN
    max_allowed_value = NLOCKTIME_MAX
    alarm = None

    def get_value(self) -> Optional[int]:
        raise NotImplementedError()

    def set_value(self, x: Any, force=True) -> None:
        raise NotImplementedError()

    @classmethod
    def is_acceptable_locktime(cls, x: Any) -> bool:
        """True when ``x`` (string or int) is within the allowed locktime bounds.

        An empty/falsy value is accepted (the field is not yet filled in).
        """
        if not x:  # e.g. empty string
            return True
        try:
            x = int(x)
        except Exception as _e:
            return False
        return cls.min_allowed_value <= x <= cls.max_allowed_value

    @staticmethod
    def get_max_allowed_timestamp() -> int:
        """Highest locktime timestamp accepted on this platform.

        On 32-bit ``time_t`` Windows builds ``datetime.fromtimestamp`` overflows
        past 2038, so the ceiling is clamped to INT32_MAX (see #6170).
        """
        ts = NLOCKTIME_MAX
        # Test if this value is within the valid timestamp limits (which is
        # platform-dependent).  see #6170
        try:
            datetime.fromtimestamp(ts)
        except (OSError, OverflowError):
            ts = 2**31 - 1  # INT32_MAX
            datetime.fromtimestamp(ts)  # test if raises
        return ts


def replace_dy_suffixes(text: str) -> str:
    """Strip the relative-time suffixes (d/y) from ``text``.

    Only days ("d") and years ("y") are supported. The block-height suffix
    ("b") was removed (A1): locktimes are always timestamps now.
    """
    return str(text).replace("d", "").replace("y", "")


def _checkbdy(s: str, pos: int, appendix: str) -> Tuple[int, str]:
    """Keep a ``d``/``y`` suffix typed right after an existing suffix.

    When the character just before ``pos`` equals ``appendix``, the text is
    re-normalised so only one suffix remains.
    """
    try:
        charpos = pos - 1
        charpos = max(0, charpos)
        charpos = min(len(s) - 1, charpos)
        if appendix == s[charpos]:
            s = replace_dy_suffixes(s) + appendix
            pos = charpos
    except Exception:
        pass
    return pos, s


def normalize_locktime_raw_text(
    text: str, pos: int
) -> Tuple[str, bool, bool, int]:
    """Sanitise the RAW locktime field text.

    Only digits plus the day ("d") and year ("y") suffixes are kept; the block
    suffix ("b") is removed (A1). Exactly one ``d``/``y`` suffix survives.

    Args:
        text: the raw field text.
        pos: the cursor position within ``text``.

    Returns:
        ``(clean, isdays, isyears, new_pos)`` where ``clean`` is the sanitised
        text and ``new_pos`` the adjusted cursor position.
    """
    text = text.strip()
    chars = "0123456789dy"
    pos = len("".join([i for i in text[:pos] if i in chars]))
    s = "".join([i for i in text if i in chars])
    isdays = False
    isyears = False

    pos, s = _checkbdy(s, pos, "d")
    pos, s = _checkbdy(s, pos, "y")

    if "d" in s:
        isdays = True
    if "y" in s:
        isyears = True

    if isdays:
        s = replace_dy_suffixes(s) + "d"
    if isyears:
        s = replace_dy_suffixes(s) + "y"
    return s, isdays, isyears, pos


def normalize_perc_amount_text(text: str, decimal_point: str) -> Tuple[str, bool]:
    """Sanitise the amount-or-percentage field text.

    Keeps digits, ``%`` and the decimal point; a trailing ``%`` marks the value
    as a percentage (``is_perc``). At most 8 decimal digits after the point.

    Args:
        text: the raw field text.
        decimal_point: the decimal separator character (``electrum`` uses
            ``DECIMAL_POINT``, locale dependent).

    Returns:
        ``(clean, is_perc)``.
    """
    text = text.strip()
    chars = "0123456789%"
    chars += decimal_point

    s = "".join([i for i in text if i in chars])

    if "%" in s:
        is_perc = True
        s = s.replace("%", "")
    else:
        is_perc = False

    if decimal_point in s:
        p = s.find(decimal_point)
        s = s.replace(decimal_point, "")
        s = s[:p] + decimal_point + s[p : p + 8]
    if is_perc:
        s += "%"

    return s, is_perc


def parse_perc_amount(text: str, decimal_point: str) -> Union[None, Decimal, int]:
    """Parse an amount-or-percentage field text into a numeric value.

    Returns ``None`` when the text cannot be parsed.
    """
    try:
        text = text.replace(decimal_point, ".")
        text = text.replace("%", "")
        return Decimal(text)
    except Exception:
        return None

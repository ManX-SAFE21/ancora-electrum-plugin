"""Private notes on the heirs, kept only in this wallet (CHANGELOG #138).

Owner: the "New heir" / "Edit heir" window gets an optional "Notes" field
(who the heir is, how to reach them, where their wallet is...). The notes
are the owner's own memo:

* stored in the wallet file under their OWN key ``heir_notes``
  (``{heir name: text}``), registered like ``will_settings``
  (``plugin_base._register_will_dict``); the heirs entries stay
  ``[address, amount, locktime]``, nothing else changes;
* never sent to the will-executors, never written on the blockchain, not
  in the PDF report, the summary or the heirs export file;
* they follow their heir: a rename moves the note, a delete removes it,
  and a note whose heir no longer exists is dropped (:func:`clean`), so
  there are no orphans.

Qt-free (``bal/core`` rule). Every function takes the wallet and works on
``wallet.db.get_dict(WALLET_KEY)`` item by item (the wallet DB rule of
#123: the stored dict is changed, never replaced).
"""

from electrum.logging import get_logger

_logger = get_logger(__name__)

#: The wallet key of the notes (not a ``bal_*`` key: those are Electrum's
#: global config; the wallet keys of the plugin have no prefix).
WALLET_KEY = "heir_notes"

#: The longest note kept, in characters (owner: "a sensible cap").
MAX_LENGTH = 1000


def _notes(wallet):
    """The wallet's stored notes dict (created empty the first time)."""
    return wallet.db.get_dict(WALLET_KEY)


def normalize(text):
    """``text`` as stored: spaces around removed; "" for nothing."""
    return str(text or "").strip()


def too_long(text):
    """True when ``text`` is longer than :data:`MAX_LENGTH` characters."""
    return len(normalize(text)) > MAX_LENGTH


def get(wallet, name):
    """The note of the heir ``name`` ("" when there is none)."""
    try:
        return str(_notes(wallet).get(name) or "")
    except Exception as e:
        _logger.error(f"heir note not read: {e!r}")
        return ""


def all_notes(wallet):
    """``{name: text}`` of every note (a plain copy)."""
    try:
        return {str(k): str(v) for k, v in _notes(wallet).items() if v}
    except Exception as e:
        _logger.error(f"heir notes not read: {e!r}")
        return {}


def set_note(wallet, name, text):
    """Store the note of ``name``; an empty note removes it. A note longer
    than :data:`MAX_LENGTH` is cut there (the window refuses it first).
    Returns the text stored ("" when removed)."""
    text = normalize(text)[:MAX_LENGTH]
    notes = _notes(wallet)
    if text:
        if notes.get(name) != text:
            notes[name] = text
    elif name in notes:
        notes.pop(name)
    return text


def rename(wallet, old, new):
    """Move the note of ``old`` to ``new`` (a rename of the heir)."""
    notes = _notes(wallet)
    if old == new or old not in notes:
        return
    text = notes.pop(old)
    if text:
        notes[new] = text


def delete(wallet, names):
    """Remove the notes of the heirs ``names`` (deleted heirs)."""
    notes = _notes(wallet)
    for name in names or []:
        if name in notes:
            notes.pop(name)


def clean(wallet, heir_names):
    """Drop the notes whose heir is not in ``heir_names`` (no orphans: an
    import, a deletion made elsewhere). Returns the names dropped."""
    notes = _notes(wallet)
    keep = set(heir_names or [])
    gone = [name for name in list(notes.keys()) if name not in keep]
    for name in gone:
        notes.pop(name)
    return gone


def restore(wallet, saved):
    """Put back the notes exactly as ``saved`` (``{name: text}``, the
    BASIC wizard's Cancel). True when something was written."""
    notes = _notes(wallet)
    saved = dict(saved or {})
    if {k: v for k, v in notes.items()} == saved:
        return False
    for name in list(notes.keys()):
        if name not in saved:
            notes.pop(name)
    for name, text in saved.items():
        if notes.get(name) != text:
            notes[name] = text
    return True

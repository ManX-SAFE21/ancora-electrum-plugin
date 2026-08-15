"""
bal.cli.plugin
==============

The headless (command-line) entry point of the plugin.

:class:`Plugin` subclasses :class:`bal.core.plugin_base.BalPlugin` without
adding any Qt hooks or per-window state.  Electrum instantiates this class when
the plugin runs with ``gui_name='cmdline'`` (the daemon loads
``bal/cmdline.py``, which re-exports it), and it is the object injected as
``plugin`` into every ``bal_*`` command by ``electrum.commands.plugin_command``.
"""

from ..core.plugin_base import BalPlugin


class Plugin(BalPlugin):
    """Minimal ``BasePlugin`` subclass for the command-line front-end."""

    def __init__(self, parent, config, name):
        BalPlugin.__init__(self, parent, config, name)

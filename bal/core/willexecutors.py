"""
bal.core.willexecutors
=======================

Client logic for talking to *will-executor* servers.

A will-executor is an optional third-party service that, for a small fee,
stores the signed inheritance transactions off-line and broadcasts them once
their locktime expires (acting as a dead-man's switch backup).

This module only contains the networking / data-shaping logic (downloading the
server list, pinging servers for their fee and address, pushing transactions,
checking whether a tx is already stored).  It is GUI-free: all user
interaction is handled by the Qt layer.
"""

import copy
import json
import time
from datetime import datetime
from typing import Any

from aiohttp import ClientResponse
from electrum import bitcoin, constants
from electrum.bitcoin import COIN, TOTAL_COIN_SUPPLY_LIMIT_IN_BTC
from electrum.logging import get_logger
from electrum.network import Network

from ..i18n import _
from . import trace
from .plugin_base import BalPlugin, get_version

# Per-request timeout (seconds) for interactive operations (ping / info /
# list download).  These fail fast (no retries) so a dead server does not
# block the UI.
DEFAULT_TIMEOUT = 5

# Single, shared wall-clock deadline (seconds) for ALL network waits the user
# can watch in the GUI: the parallel broadcast (pushtxs), the parallel check
# (searchtx), the will-executor ping and the will-executor list download.
# Having ONE constant (instead of several scattered 30s/45s values) keeps the
# experience consistent and makes it trivial to tune. Requested by the user
# (reduced from 30s/45s to 20s, unified into one variable).
NETWORK_DEADLINE = 20

# Broadcast (pushtxs) timeouts.  Broadcasting a will is important, so we keep a
# couple of quick retries to survive a transient hiccup -- but far from the old
# 10s x 10 retries + 30s sleeps (~140s) that froze the wizard on a dead server.
# Worst case per server is now ~ PUSH_TIMEOUT * (1 + PUSH_MAX_RETRIES)
# + PUSH_RETRY_SLEEP * PUSH_MAX_RETRIES = 8 * 3 + 1 * 2 = ~26s, and the wizard
# also enforces a global deadline on top of this (see push_transactions_parallel).
PUSH_TIMEOUT = 8
PUSH_MAX_RETRIES = 2
PUSH_RETRY_SLEEP = 1

# Global wall-clock deadline (seconds) for the whole parallel broadcast.  Once
# it elapses we stop waiting for the still-pending servers, mark them as
# "Timeout" and let the wizard proceed instead of appearing stuck.
# Derived from the single shared NETWORK_DEADLINE constant above.
PUSH_GLOBAL_DEADLINE = NETWORK_DEADLINE

# Check (searchtx) timeouts.  Used when the user presses "Check" to verify that
# each will-executor still holds the transaction.  Like the broadcast path, the
# old defaults (10s x 10 retries + 30s sleeps ~= 140s per server) froze the
# "checking transaction" dialog on a single dead server.  Cap the whole batch
# with a global deadline.  #147: ONE attempt of 15 s instead of two of 8 s: a
# slow server (divinaly answered in 5 s and more) was cut at 8 s and the
# retry started the slow query again, so it never answered in time.
CHECK_TIMEOUT = 15
CHECK_MAX_RETRIES = 0
CHECK_RETRY_SLEEP = 1
# Derived from the single shared NETWORK_DEADLINE constant above.
CHECK_GLOBAL_DEADLINE = NETWORK_DEADLINE

# #172: the Check of a will in several deliveries asks one searchtx per
# transaction (21 per will-executor with 4 will-executors and 3 deliveries).
# Sent all at once, the servers answered 429 Too Many Requests. So: at most
# CHECK_PER_SERVER requests at a time to one will-executor (owner: 3), the
# will-executors in parallel, at most CHECK_MAX_WORKERS requests in all.
CHECK_PER_SERVER = 3
CHECK_MAX_WORKERS = 16
# A 429 means "busy, ask later", not "I do not hold it": a pause (the
# server's Retry-After, else 2 s, 4 s, 8 s, never more than
# CHECK_BUSY_MAX_SLEEP) and a new try, at most CHECK_BUSY_RETRIES times;
# still busy: NO_ANSWER (the copy stays sent, #147), never CHECK_FAIL.
CHECK_BUSY_RETRIES = 3
CHECK_BUSY_SLEEP = 2
CHECK_BUSY_MAX_SLEEP = 10
# The deadline of a Check grows with the requests of the busiest
# will-executor: CHECK_SECONDS_PER_ROUND for each round of CHECK_PER_SERVER
# requests, never less than CHECK_GLOBAL_DEADLINE, never more than
# CHECK_MAX_DEADLINE (owner: 2 minutes). See Willexecutors.check_deadline.
CHECK_SECONDS_PER_ROUND = 5
CHECK_MAX_DEADLINE = 120

_logger = get_logger(__name__)


class _NoAnswer:
    """The type of :data:`NO_ANSWER` (a single, named sentinel)."""

    def __repr__(self):
        """``NO_ANSWER`` in the logs."""
        return "NO_ANSWER"

    def __bool__(self):
        """False: like the ``None`` it replaces, it is not an answer."""
        return False


#: #147: what a check gives when the will-executor did not answer in time
#: (every attempt timed out, or the global deadline elapsed). It is NOT "the
#: executor does not hold the will": ``WillItem.set_check_willexecutor``
#: leaves the item as it was (still sent, to be verified) instead of marking
#: it not received.
NO_ANSWER = _NoAnswer()


# --------------------------------------------------------------------------- #
# Tor / .onion helpers
# --------------------------------------------------------------------------- #
# A .onion will-executor is only reachable when Electrum routes traffic through
# Tor. There is no point downloading .onion servers from welist when Electrum is
# not on Tor: they would only waste time (unreachable). We therefore drop them
# from the downloaded list unless Tor is confirmed active. Electrum itself gates
# .onion the same way (`if host.endswith('.onion') and not self.is_proxy_tor`).


def is_onion_url(url):
    """True if the URL points to a Tor hidden service (.onion host)."""
    if not url:
        return False
    try:
        u = url.lower()
        if "://" in u:
            u = u.split("://", 1)[1]
        host = u.split("/", 1)[0].split(":", 1)[0]
        return host.endswith(".onion")
    except Exception:
        return False


def is_tor_active():
    """True only if Electrum is CONFIRMED connected through Tor.

    Reads Network.is_proxy_tor defensively (network may be None in offline
    mode). It is TRI-STATE (True / False / None); we require True, so False and
    None (undetermined) both mean "Tor not active". Available in Electrum 4.7.2
    and 4.8.0.
    """
    try:
        network = Network.get_instance()
        if network is None:
            return False
        return network.is_proxy_tor is True
    except Exception:
        return False



def _seconds(value):
    """*value* seconds with one decimal: ``"0.8"`` (#173)."""
    return "{:.1f}".format(value)


def _trace_is_detail(url):
    """True when the requests to *url* are a "Full" detail of the trace.

    #173: the copies' checks (``searchtx``) are many, one per transaction:
    the window shows them only at the "Full" level; the sending
    (``pushtxs``), the will-executors' information and the list always.
    """
    return "/searchtx" in str(url)


def _trace_request(method, url, data, full):
    """Write one request to a will-executor in the command trace (#173).

    What is sent is never shown: for ``pushtxs`` only how many transactions
    (one per line), for ``searchtx`` the shortened txid.
    """
    what = ""
    try:
        if "/pushtxs" in url and data is not None:
            text = data.decode("ascii") if isinstance(data, bytes) else str(data)
            count = len([line for line in text.splitlines() if line.strip()])
            what = "  " + _("{} transactions").format(count)
        elif "/searchtx" in url and data is not None:
            text = data.decode("ascii") if isinstance(data, bytes) else str(data)
            what = "  " + trace.short_txid(text.strip())
    except Exception:
        what = ""
    trace.add(trace.REQUEST, "{} {}{}".format(
        str(method).upper(), trace.short_url(url), what), full=full)


def _trace_answer(response, seconds, full):
    """Write the answer of a will-executor in the command trace (#173).

    A short text answer ("thx", "already present") is shown as it is; a
    transaction is never shown, only that the answer has one.
    """
    if response is None:
        text = _("empty answer")
    elif isinstance(response, str) and len(response) <= 40 and response.strip():
        text = response.strip()
    elif isinstance(response, dict) and response.get("tx"):
        text = _("answer with a transaction")
    else:
        text = _("answer received")
    trace.add(trace.ANSWER, "{}  {} s".format(text, _seconds(seconds)), full=full)


def _trace_error(error, seconds, full):
    """Write a failed request in the command trace (#173).

    An HTTP error shows its code (429: busy); any other error its text.
    """
    status = getattr(error, "status", None)
    if isinstance(status, int):
        if status == 429:
            text = _("busy (429)")
        else:
            text = _("error {}").format(status)
    else:
        text = str(error) or type(error).__name__
    trace.add(trace.ERROR, "{}  {} s".format(text, _seconds(seconds)), full=full)


class Willexecutors:

    class NoServersForChainError(Exception):
        """Raised when the welist server responds but returns no data for the
        requested chain, indicating no active servers for that network."""

        def __init__(self, chain, url=None, reason=None):
            self.chain = chain
            self.url = url
            self.reason = reason
            super().__init__(
                f"NoServersForChainError: chain={chain} url={url} reason={reason}"
            )

    # Expose the networking constants as class attributes so the GUI layer can
    # reference them (e.g. to show the "Xs / DEADLINEs" countdown) without
    # importing module-level names.  Single source of truth: the module
    # constants defined above.
    DEFAULT_TIMEOUT = DEFAULT_TIMEOUT
    NETWORK_DEADLINE = NETWORK_DEADLINE
    PUSH_TIMEOUT = PUSH_TIMEOUT
    PUSH_MAX_RETRIES = PUSH_MAX_RETRIES
    PUSH_RETRY_SLEEP = PUSH_RETRY_SLEEP
    PUSH_GLOBAL_DEADLINE = PUSH_GLOBAL_DEADLINE
    CHECK_TIMEOUT = CHECK_TIMEOUT
    CHECK_MAX_RETRIES = CHECK_MAX_RETRIES
    NO_ANSWER = NO_ANSWER
    CHECK_RETRY_SLEEP = CHECK_RETRY_SLEEP
    CHECK_GLOBAL_DEADLINE = CHECK_GLOBAL_DEADLINE
    CHECK_PER_SERVER = CHECK_PER_SERVER
    CHECK_MAX_WORKERS = CHECK_MAX_WORKERS
    CHECK_BUSY_RETRIES = CHECK_BUSY_RETRIES
    CHECK_BUSY_SLEEP = CHECK_BUSY_SLEEP
    CHECK_BUSY_MAX_SLEEP = CHECK_BUSY_MAX_SLEEP
    CHECK_SECONDS_PER_ROUND = CHECK_SECONDS_PER_ROUND
    CHECK_MAX_DEADLINE = CHECK_MAX_DEADLINE

    @staticmethod
    def _stored_or_copy_of_default(bal_plugin):
        """The stored will-executor lists, or a COPY of the default ones.

        ``WILLEXECUTORS.get()`` returns the default dict ITSELF while nothing
        is saved yet. Changing it (adding, deleting, selecting) then changed
        the plugin's built-in defaults in memory until Electrum was
        restarted: a will-executor added on a fresh install became a
        "default", and a deleted default vanished from them (CHANGELOG #108).
        """
        stored = bal_plugin.WILLEXECUTORS.get()
        if stored is bal_plugin.WILLEXECUTORS.default:
            stored = copy.deepcopy(stored)
        return stored

    @staticmethod
    def save(bal_plugin, willexecutors):
        """Store ``willexecutors`` as the list of the current network."""
        _logger.debug(f"save {willexecutors},{BalPlugin.chainname}")
        aw = Willexecutors._stored_or_copy_of_default(bal_plugin)
        aw[BalPlugin.chainname] = willexecutors
        bal_plugin.WILLEXECUTORS.set(aw)
        _logger.debug(f"saved: {aw}")
        # bal_plugin.WILLEXECUTORS.set(willexecutors)

    @staticmethod
    def get_willexecutors(
        bal_plugin, update=False, bal_window: Any = None, force=False, task=True
    ):
        """The will-executor list of the current network, sorted.

        The default ones are added back when missing; the built-in defaults
        themselves are never handed out, only copies (CHANGELOG #108).
        """
        willexecutors = Willexecutors._stored_or_copy_of_default(bal_plugin)
        willexecutors = willexecutors.get(BalPlugin.chainname, {})
        to_del = []
        for w in willexecutors:
            if not isinstance(willexecutors[w], dict):
                to_del.append(w)
                continue
            Willexecutors.initialize_willexecutor(willexecutors[w], w)
        for w in to_del:
            _logger.error(
                "error Willexecutor to delete type:{} {}".format(
                    type(willexecutors[w]), w
                )
            )
            del willexecutors[w]
        bal = bal_plugin.WILLEXECUTORS.default.get(BalPlugin.chainname, {})
        for bal_url, bal_executor in bal.items():
            if bal_url not in willexecutors:
                _logger.debug(f"force add {bal_url} willexecutor")
                # A copy: the record is changed later (selected, status...),
                # and the default must stay as built in (CHANGELOG #108).
                willexecutors[bal_url] = copy.deepcopy(bal_executor)
        # if update:
        #    found = False
        #    for url, we in willexecutors.items():
        #        if Willexecutors.is_selected(we):
        #            found = True
        #    if found or force:
        #        if bal_plugin.PING_WILLEXECUTORS.get() or force:
        #            ping_willexecutors = True
        #            if bal_plugin.ASK_PING_WILLEXECUTORS.get() and not force:
        #                if bal_window:
        #                    ping_willexecutors = bal_window.window.question(
        #                        _(
        #                            "Contact willexecutors servers to update payment informations?"
        #                        )
        #                    )

        #            if ping_willexecutors:
        #                if task:
        #                    bal_window.ping_willexecutors(willexecutors, task)
        #                else:
        #                    bal_window.ping_willexecutors_task(willexecutors)
        w_sorted = dict(
            sorted(
                willexecutors.items(), key=lambda w: w[1].get("sort", 0), reverse=True
            )
        )
        return w_sorted

    @staticmethod
    def is_protected(bal_plugin, url):
        """True for the Bitcoin After Life will-executor, which cannot be
        deleted or renamed, only deselected (owner decision, CHANGELOG #108).

        It is recognised by its URL: any URL of the plugin's default
        will-executor list (``WILLEXECUTORS.default``, every network), so the
        rule holds on mainnet too, where it arrives from the welist download
        and not from the default list. Before, deleting it "worked" but on
        testnet it came back selected at the next start
        (``get_willexecutors`` adds the defaults back).
        """
        if not url:
            return False
        wanted = str(url).rstrip("/").lower()
        for chain in bal_plugin.WILLEXECUTORS.default.values():
            for default_url in chain:
                if default_url.rstrip("/").lower() == wanted:
                    return True
        return False

    @staticmethod
    def user_deselected(bal_plugin, chain=None):
        """The URLs the USER deselected on purpose on ``chain`` (#137).

        Stored in ``bal_willexecutors_user_deselected`` (``{chain: [url]}``,
        global like the list). Owner decision A: the will-executors already
        deselected when this version first looks at a network have no
        recorded reason, so they count as deselected by the user. That
        network's entry is seeded ONCE from the saved list (its URLs with
        ``selected`` False) and then only the user's actions change it
        (:meth:`set_user_choice`): idempotent, and a will-executor the
        plugin deselects later (no answer, Exclude) is never added. The
        callers ask it BEFORE any automatic deselection, so the seed sees
        the list as it was. Returns a set (empty when the setting is not
        available, e.g. an old plugin object).
        """
        chain = chain or BalPlugin.chainname
        setting = getattr(bal_plugin, "WILLEXECUTORS_USER_DESELECTED", None)
        if setting is None:
            return set()
        stored = setting.get()
        stored = copy.deepcopy(stored) if isinstance(stored, dict) else {}
        if chain not in stored:
            # The list as the plugin sees it on this network (the saved one,
            # the built-in default added back); another network: as saved.
            if chain == BalPlugin.chainname:
                saved = Willexecutors.get_willexecutors(bal_plugin)
            else:
                saved = Willexecutors._stored_or_copy_of_default(bal_plugin).get(chain, {})
            stored[chain] = sorted(
                url for url, we in saved.items()
                if isinstance(we, dict) and not we.get("selected"))
            setting.set(stored)
            _logger.info(f"user deselections seeded on {chain}: {stored[chain]}")
        return set(stored.get(chain) or [])

    @staticmethod
    def set_user_choice(bal_plugin, urls, selected, chain=None):
        """Record that the USER selected (``selected`` True) or deselected
        the will-executors ``urls`` on purpose (#137): the window's
        select / deselect, Select all / Deselect all / only valid / invalid
        (owner decision B), the command line. A selection removes the URL
        from the user's deselections, a deselection adds it. Seeds first
        (:meth:`user_deselected`). Saved only when it changes.
        """
        chain = chain or BalPlugin.chainname
        setting = getattr(bal_plugin, "WILLEXECUTORS_USER_DESELECTED", None)
        if setting is None:
            return
        current = Willexecutors.user_deselected(bal_plugin, chain)
        urls = set(urls or [])
        wanted = (current - urls) if selected else (current | urls)
        if wanted == current:
            return
        stored = setting.get()
        stored = copy.deepcopy(stored) if isinstance(stored, dict) else {}
        stored[chain] = sorted(wanted)
        setting.set(stored)

    @staticmethod
    def select_after_ping(willexecutors, user_deselected, known):
        """Select who answered, except the user's deselections (#137).

        ``willexecutors``: ``{url: record}`` after the ping (changed in
        place); ``user_deselected``: :meth:`user_deselected`; ``known``: the
        URLs of the saved list BEFORE this download. The three cases of the
        owner:

        1. new on the network (not in ``known``) and answering: selected;
        2. deselected by the user: stays deselected, even when it answers;
        3. otherwise (known; deselected before by the plugin because it did
           not answer, or by Exclude): selected when it answers, deselected
           when it does not.

        Returns the URLs of case 1 (the wizard's summary says "new in the
        network").
        """
        new = set()
        for url, we in (willexecutors or {}).items():
            if not isinstance(we, dict):
                continue
            if url in user_deselected:
                we["selected"] = False
                continue
            responded = str(we.get("status", "")) == "200"
            we["selected"] = responded
            if responded and url not in known:
                new.add(url)
        return new

    @staticmethod
    def is_selected(willexecutor, value=None):
        if not willexecutor:
            return False
        if value is not None:
            willexecutor["selected"] = value
        try:
            return willexecutor["selected"]
        except Exception:
            willexecutor["selected"] = False
            return False

    @staticmethod
    def is_valid(willexecutor, max_fee=None, dust=None):
        if not willexecutor:
            return False
        address = willexecutor.get("address", "")
        if not address or not bitcoin.is_address(address, net=constants.net):
            return False
        base_fee = int(willexecutor.get("base_fee", 0))
        if dust is not None and base_fee < dust:
            return False
        if max_fee is not None and base_fee > max_fee:
            return False
        return True

    @staticmethod
    def get_willexecutor_transactions(will, force=False, current_willexecutors=None):
        """Group the valid, signed, not-yet-pushed will txs by will-executor.

        Returns ``{url: executor_record}`` where each record gets ``txs`` (the
        raw txs, one per line) and ``txsids``.

        ``current_willexecutors`` is the CURRENT executor list from the config
        (``{url: record}``). When given, an executor that is no longer in it or
        no longer selected is skipped. WHY: every will item keeps its own copy
        of the executor record taken at build time (``selected`` = True), so
        without this check a deselected executor would still receive the will.
        ``None`` keeps the previous behaviour (only the item's own copy).

        The returned records are COPIES: the will items are never modified.
        """
        willexecutors = {}
        for wid, willitem in will.items():
            if willitem.get_status("VALID"):
                if willitem.get_status("COMPLETE"):
                    if not willitem.get_status("PUSHED") or force:
                        if willexecutor := willitem.we:
                            url = willexecutor["url"]
                            if current_willexecutors is not None and not (
                                Willexecutors.is_selected(
                                    current_willexecutors.get(url)
                                )
                            ):
                                continue
                            if willexecutor and Willexecutors.is_selected(willexecutor):
                                if url not in willexecutors:
                                    # Work on a COPY of the record: the item's
                                    # own ``we`` dict is saved in the wallet, so
                                    # the transient push fields below (raw txs,
                                    # txids and the - translated - broadcast
                                    # status shown in the push dialog) must
                                    # never end up in it (stored data must not
                                    # depend on the UI language, see bal/i18n.py).
                                    record = dict(willexecutor)
                                    record["txs"] = ""
                                    record["txsids"] = []
                                    record["broadcast_status"] = _("Waiting...")
                                    willexecutors[url] = record
                                willexecutors[url]["txs"] += str(willitem.tx) + "\n"
                                willexecutors[url]["txsids"].append(wid)

        return willexecutors

    # def only_selected_list(willexecutors):
    #    out = {}
    #    for url, v in willexecutors.items():
    #        if Willexecutors.is_selected(url):
    #            out[url] = v

    # def push_transactions_to_willexecutors(will):
    #    willexecutors = Willexecutors.get_transactions_to_be_pushed()
    #    for url in willexecutors:
    #        willexecutor = willexecutors[url]
    #        if Willexecutors.is_selected(willexecutor):
    #            if "txs" in willexecutor:
    #                Willexecutors.push_transactions_to_willexecutor(
    #                    willexecutors[url]["txs"], url
    #                )

    @staticmethod
    def send_request(
        method, url, data=None, *, timeout=10, handle_response=None, count_reply=0,
        max_retries=10, retry_sleep=3,
    ):
        """Send an HTTP request to a will-executor server.

        ``max_retries`` / ``retry_sleep`` control the timeout-retry behaviour:

        * For *critical* operations (pushing inheritance transactions) the
          historical default of up to 10 retries with a 3s back-off is kept, so
          a transient network hiccup does not lose a transaction.
        * For *interactive* operations (ping / info / list download) callers
          should pass ``max_retries=0`` so a dead server fails fast (one short
          timeout) instead of blocking the UI for minutes.  See
          :meth:`ping_servers_parallel`.

        #173: every attempt and its answer (or error) go to the command
        trace, with the seconds (:func:`_trace_request`,
        :func:`_trace_answer`, :func:`_trace_error`); never the
        transactions themselves.
        """
        network = Network.get_instance()
        if not network:
            raise Exception("You are offline.")
        _logger.debug(f"<-- {method} {url} {data}")
        headers = {}
        headers["user-agent"] = f"BalPlugin v:{get_version()}"
        headers["Content-Type"] = "text/plain"
        if not handle_response:
            handle_response = Willexecutors.handle_response
        attempts = max_retries + 1
        full = _trace_is_detail(url)
        for attempt in range(attempts):
            _trace_request(method, url, data, full)
            started = time.monotonic()
            try:
                if method == "get":
                    response = Network.send_http_on_proxy(
                        method,
                        url,
                        params=data,
                        headers=headers,
                        on_finish=handle_response,
                        timeout=timeout,
                    )
                elif method == "post":
                    response = Network.send_http_on_proxy(
                        method,
                        url,
                        body=data,
                        headers=headers,
                        on_finish=handle_response,
                        timeout=timeout,
                    )
                else:
                    raise Exception(f"unexpected {method=!r}")
                _logger.debug(f"--> {response}")
                _trace_answer(response, time.monotonic() - started, full)
                return response
            except TimeoutError:
                seconds = time.monotonic() - started
                if attempt < max_retries:
                    _logger.debug(
                        f"timeout({attempt}) error: "
                        f"retry in {retry_sleep} sec..."
                    )
                    trace.add(trace.WARNING, _(
                        "no answer in {seconds} s, new try in {pause} s"
                    ).format(seconds=_seconds(seconds), pause=retry_sleep), full=True)
                    if retry_sleep:
                        time.sleep(retry_sleep)
                else:
                    _logger.debug(f"Too many timeouts: {attempt}")
                    trace.add(trace.ERROR, _("no answer in {} s").format(
                        _seconds(seconds)), full=full)
            except Exception as e:
                _trace_error(e, time.monotonic() - started, full)
                raise e
        return None

    @staticmethod
    def get_we_url_from_response(resp):
        url_slices = str(resp.url).split("/")
        if len(url_slices) > 2:
            url_slices = url_slices[:-2]
        return "/".join(url_slices)

    @staticmethod
    async def handle_response(resp: ClientResponse):
        resp.raise_for_status()
        r = await resp.text()
        try:
            r = json.loads(r)
        except json.JSONDecodeError:
            pass
        return r

    @staticmethod
    class AlreadyPresentException(Exception):
        pass

    @staticmethod
    def push_transactions_to_willexecutor(
        willexecutor, *, timeout=PUSH_TIMEOUT, max_retries=PUSH_MAX_RETRIES,
        retry_sleep=PUSH_RETRY_SLEEP,
    ):
        # ``timeout`` / ``max_retries`` / ``retry_sleep`` are forwarded to
        # send_request so the broadcast fails fast on a dead/slow server instead
        # of hanging for ~140s (the old default was 10s timeout x 10 retries +
        # 30s of sleeps).  A small number of quick retries still protects
        # against a transient hiccup without freezing the wizard.
        out = True
        try:
            _logger.debug(f"{willexecutor['url']}: {willexecutor['txs']}")
            if w := Willexecutors.send_request(
                "post",
                willexecutor["url"] + "/" + BalPlugin.chainname + "/pushtxs",
                data=willexecutor["txs"].encode("ascii"),
                timeout=timeout,
                max_retries=max_retries,
                retry_sleep=retry_sleep,
            ):
                _logger.debug(f"pushed: {w}")
                if w != "thx":
                    _logger.debug(f"error: {w}")
                    raise Exception(w)
                willexecutor["broadcast_status"] = _("Success")
            else:
                raise Exception(
                    f"empty reply from:{willexecutor['url']}"
                )
        except Exception as e:
            _logger.debug(f"error:{e}")
            if str(e) == "already present":
                raise Willexecutors.AlreadyPresentException() from None
            out = False
            willexecutor["broadcast_status"] = _("Failed")
            # Keep the reason ("You are offline.", the server's answer...):
            # the command line reports it (CHANGELOG #105). ``willexecutor`` is
            # the push copy of the record, never saved in the wallet.
            willexecutor["broadcast_error"] = str(e)

        return out

    @staticmethod
    def ping_servers(willexecutors):
        for url, we in willexecutors.items():
            Willexecutors.get_info_task(url, we)

    @staticmethod
    def get_info_task(url, willexecutor, *, timeout=DEFAULT_TIMEOUT,
                      max_retries=0, retry_sleep=0):
        w = None
        try:
            _logger.info("GETINFO_WILLEXECUTOR")
            _logger.debug(url)
            # Fast-fail by default (max_retries=0): a dead server returns after a
            # single short timeout instead of retrying 10x with sleeps, which
            # used to freeze the UI for minutes per unreachable server.
            w = Willexecutors.send_request(
                "get", url + "/" + BalPlugin.chainname + "/info",
                timeout=timeout, max_retries=max_retries, retry_sleep=retry_sleep,
            )
            if isinstance(w, dict):
                address = w.get("address")
                if not isinstance(address, str) or not bitcoin.is_address(
                    address, net=constants.net
                ):
                    _logger.warning(
                        f"invalid address from {url}: {address!r}"
                    )
                    willexecutor["status"] = "KO"
                else:
                    base_fee = w.get("base_fee")
                    try:
                        base_fee = int(base_fee or 0)
                        if base_fee < 0:
                            raise ValueError("negative fee")
                        if base_fee > TOTAL_COIN_SUPPLY_LIMIT_IN_BTC * COIN:
                            raise ValueError("fee exceeds total coin supply")
                    except (TypeError, ValueError) as e:
                        _logger.warning(
                            f"invalid base_fee from {url}: "
                            f"{w.get('base_fee')!r} ({e})"
                        )
                        willexecutor["status"] = "KO"
                    else:
                        willexecutor["url"] = url
                        willexecutor["status"] = 200
                        willexecutor["base_fee"] = base_fee
                        willexecutor["address"] = address
                        willexecutor["info"] = w.get("info", "")
            else:
                # No dict reply (timeout / empty) -> mark as unreachable.
                willexecutor["status"] = "KO"
            _logger.debug(f"response_data {w}")
        except Exception as e:
            _logger.error(f"error {e} contacting {url}: {w}")
            willexecutor["status"] = "KO"

        willexecutor["last_update"] = datetime.now().timestamp()
        return willexecutor

    @staticmethod
    def ping_servers_parallel(willexecutors, *, on_each=None, max_workers=8,
                              timeout=DEFAULT_TIMEOUT, on_tick=None,
                              tick_interval=1.0):
        """Ping every will-executor concurrently and report results as they
        arrive.

        Network requests run in a thread pool: each ``send_http_on_proxy`` call
        schedules its coroutine on Electrum's shared asyncio loop and blocks
        only its *own* worker thread, so the total wall-clock time is roughly
        that of the slowest server rather than the *sum* of all of them.  A
        single dead server can no longer stall the whole batch.

        Args:
            willexecutors: ``{url: we_dict}`` mapping (mutated in place with the
                ping result, exactly like the old sequential ``ping_servers``).
            on_each: optional ``callback(url, we_dict, ok: bool)`` invoked from a
                worker thread each time a server answers (or fails), so the GUI
                can update its list live.  Must be thread-safe / marshalled to
                the GUI thread by the caller.
            max_workers: maximum number of concurrent pings.
            timeout: per-request timeout in seconds (fast-fail, no retries).

            on_tick: optional ``callback()`` invoked periodically (every
                ``tick_interval`` seconds) **from the calling thread** while
                waiting for servers, so a Qt caller can refresh an elapsed-time
                counter from the same thread that drives ``on_each``.

        Returns:
            The same ``willexecutors`` mapping, updated in place.

        #173: the command trace gets ``bal_willexecutors_ping`` with how
        many will-executors, then how many answered.
        """
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        items = list(willexecutors.items())
        if not items:
            return willexecutors
        trace.command("bal_willexecutors_ping",
                      _("{} will-executors").format(len(items)))
        answered = []

        def _ping_one(url, we):
            we = Willexecutors.get_info_task(
                url, we, timeout=timeout, max_retries=0, retry_sleep=0
            )
            ok = we.get("status") == 200
            return url, we, ok

        def _fire_tick():
            if on_tick is not None:
                try:
                    on_tick()
                except Exception as cb_err:
                    _logger.error(f"ping on_tick callback error: {cb_err}")

        workers = max(1, min(max_workers, len(items)))
        # Manual pool (no ``with``) so we can poll futures in short slices and
        # drive ``on_tick`` from THIS thread between waits (reliable Qt repaint).
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bal-ping")
        futures = {pool.submit(_ping_one, url, we) for url, we in items}
        try:
            pending = set(futures)
            while pending:
                done, pending = wait(
                    pending, timeout=tick_interval, return_when=FIRST_COMPLETED
                )
                for fut in done:
                    try:
                        url, we, ok = fut.result()
                    except Exception as e:  # defensive: one server never crashes all
                        _logger.error(f"ping_servers_parallel worker error: {e}")
                        continue
                    willexecutors[url] = we
                    if ok:
                        answered.append(url)
                    if on_each is not None:
                        try:
                            on_each(url, we, ok)
                        except Exception as cb_err:
                            _logger.error(f"ping on_each callback error: {cb_err}")
                # Drive the elapsed-time counter from the calling thread.
                _fire_tick()
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)
        trace.add(trace.OK if len(answered) == len(items) else trace.WARNING,
                  _("{answered} of {total} will-executors answered").format(
                      answered=len(answered), total=len(items)))
        return willexecutors

    @staticmethod
    def push_transactions_parallel(willexecutors, *, on_each=None, max_workers=8,
                                   deadline=PUSH_GLOBAL_DEADLINE, on_timeout=None,
                                   on_tick=None, tick_interval=1.0):
        """Push transactions to multiple will-executors concurrently.

        Like :meth:`ping_servers_parallel` but for the ``pushtxs`` operation.
        Each server keeps a short retry behaviour
        (:meth:`push_transactions_to_willexecutor`) so a real transaction is not
        lost to a transient hiccup, but servers are contacted in parallel and
        results are reported via ``on_each(url, we_dict, ok, exc)`` as they
        complete.

        A global wall-clock ``deadline`` (seconds) caps the whole operation: if
        some servers are still pending when it elapses, we stop waiting, mark
        them via ``on_timeout(url, we_dict)`` and return, so the caller (the
        wizard) is never stuck behind one unresponsive server.  Pass
        ``deadline=None`` to wait indefinitely (old behaviour).

        ``on_tick()`` is invoked periodically (every ``tick_interval`` seconds)
        **from the calling thread** while waiting for workers.  This lets a Qt
        caller refresh an elapsed-time counter from the same thread that drives
        ``on_each`` (so its pyqtSignal repaints reliably), instead of relying on
        a separate heartbeat thread whose signal emissions are not marshalled.

        Returns ``{url: (ok, exception_or_None)}`` for the servers that
        answered in time (timed-out servers are reported via ``on_timeout``).
        """
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        targets = [(url, we) for url, we in willexecutors.items() if "txs" in we]
        results = {}
        if not targets:
            return results

        def _push_one(url, we):
            try:
                ok = Willexecutors.push_transactions_to_willexecutor(we)
                return url, we, ok, None
            except Willexecutors.AlreadyPresentException as ape:
                return url, we, False, ape
            except Exception as e:
                return url, we, False, e

        def _fire_tick():
            if on_tick is not None:
                try:
                    on_tick()
                except Exception as cb_err:
                    _logger.error(f"push on_tick callback error: {cb_err}")

        workers = max(1, min(max_workers, len(targets)))
        # NOTE: we do not use ``with ThreadPoolExecutor(...)`` here because its
        # __exit__ calls shutdown(wait=True), which would block on a hung worker
        # and defeat the whole point of the global deadline.  We shut the pool
        # down without waiting once the deadline elapses; the daemon worker(s)
        # stuck on a dead socket will be torn down when their request finally
        # times out (PUSH_TIMEOUT), without holding up the wizard.
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bal-push")
        fut_to_url = {pool.submit(_push_one, url, we): (url, we)
                      for url, we in targets}
        start = time.time()
        try:
            # Poll the futures in short slices so we can call ``on_tick`` from
            # THIS thread between waits.  ``wait(..., timeout=tick_interval)``
            # returns as soon as a future completes OR the slice elapses,
            # whichever comes first, so the counter advances ~once per second
            # while the parallel push runs.
            pending = set(fut_to_url.keys())
            while pending:
                if deadline is not None and (time.time() - start) >= deadline:
                    break
                slice_timeout = tick_interval
                if deadline is not None:
                    remaining = deadline - (time.time() - start)
                    slice_timeout = max(0.0, min(tick_interval, remaining))
                done, pending = wait(
                    pending, timeout=slice_timeout, return_when=FIRST_COMPLETED
                )
                for fut in done:
                    try:
                        url, we, ok, exc = fut.result()
                    except Exception as e:
                        _logger.error(
                            f"push_transactions_parallel worker error: {e}"
                        )
                        continue
                    results[url] = (ok, exc)
                    if on_each is not None:
                        try:
                            on_each(url, we, ok, exc)
                        except Exception as cb_err:
                            _logger.error(f"push on_each callback error: {cb_err}")
                # Drive the elapsed-time counter from the calling thread.
                _fire_tick()
            # Any server still pending here hit the global deadline.
            if pending:
                elapsed = time.time() - start
                _logger.warning(
                    f"push global deadline ({deadline}s) reached after "
                    f"{elapsed:.1f}s; {len(pending)} server(s) "
                    f"did not answer in time"
                )
                for fut in pending:
                    url, we = fut_to_url[fut]
                    if url in results:
                        continue
                    if on_timeout is not None:
                        try:
                            on_timeout(url, we)
                        except Exception as cb_err:
                            _logger.error(
                                f"push on_timeout callback error: {cb_err}"
                            )
        finally:
            # Do not block on still-running workers (Python 3.9+: cancel queued).
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)
        return results

    @staticmethod
    def check_deadline(items, *, per_server=CHECK_PER_SERVER,
                       max_workers=CHECK_MAX_WORKERS):
        """The global deadline (seconds) of a Check of ``items`` (#172).

        ``items``: the ``(wid, url)`` pairs to ask. CHECK_SECONDS_PER_ROUND
        for each round of requests; the rounds are the larger of the two
        limits of :meth:`check_transactions_parallel`: the will-executor
        with the most requests (asked ``per_server`` at a time) and all the
        requests (``max_workers`` at a time; owner's question: 10
        will-executors with 30 transactions each need 19 rounds, not 10).
        Never less than CHECK_GLOBAL_DEADLINE (a small will keeps its 20 s)
        and never more than CHECK_MAX_DEADLINE (owner: 2 minutes). The 84
        transactions of 4 will-executors and 3 deliveries: 21 for one
        will-executor, 7 rounds, 35 s.
        """
        counts = {}
        for _wid, url in items:
            if url:
                counts[url] = counts.get(url, 0) + 1
        if not counts:
            return CHECK_GLOBAL_DEADLINE
        rounds = max(
            -(-max(counts.values()) // max(1, per_server)),
            -(-sum(counts.values()) // max(1, max_workers)),
        )
        return max(
            CHECK_GLOBAL_DEADLINE,
            min(CHECK_MAX_DEADLINE, rounds * CHECK_SECONDS_PER_ROUND),
        )

    @staticmethod
    def check_transactions_parallel(items, *, on_each=None,
                                    max_workers=CHECK_MAX_WORKERS,
                                    per_server=CHECK_PER_SERVER,
                                    deadline=CHECK_GLOBAL_DEADLINE,
                                    on_timeout=None, on_tick=None,
                                    on_busy=None, should_stop=None,
                                    tick_interval=1.0):
        """Check (searchtx) several will-executors concurrently.

        Same design as :meth:`push_transactions_parallel`, but for the "Check"
        operation: it verifies that each will-executor still holds its
        transaction.  ``items`` is an iterable of ``(wid, url)`` pairs (one per
        will-item that has a will-executor).

        #172: at most ``per_server`` requests at a time go to one
        will-executor (the next one leaves when one answers), the
        will-executors are asked in parallel, at most ``max_workers``
        requests in all. Before, every request left at once and the servers
        answered 429 Too Many Requests.

        Each request is :meth:`check_transaction` (one attempt, a pause and
        a new try after a 429). Results are reported via
        ``on_each(wid, url, result_or_None, exc)`` as they arrive,
        ``on_busy(url, seconds)`` when a will-executor answered 429 and is
        asked again in ``seconds``, ``on_tick()`` periodically; all three
        from the calling thread. A global ``deadline`` guarantees the
        dialog never freezes behind one unresponsive server: the requests
        still running and the ones not sent yet are reported via
        ``on_timeout(wid, url)``. ``should_stop()`` True (the owner closed
        the window) stops the waiting at the next tick; then nothing more is
        reported (the copies not answered stay as they were).

        Returns ``{wid: (result_or_None, exception_or_None)}`` for the servers
        that answered in time.
        """
        import queue
        from collections import deque
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        targets = [(wid, url) for wid, url in items if url]
        results = {}
        if not targets:
            return results
        per_server = max(1, per_server)
        waiting = {}  # url -> the wids not asked yet, in the given order
        for wid, url in targets:
            waiting.setdefault(url, deque()).append(wid)
        running = {url: 0 for url in waiting}
        # The workers only put the 429 notices here; the calling thread
        # passes them on (callbacks never run on a worker thread).
        busy_notices = queue.SimpleQueue()

        def _busy(url, seconds):
            busy_notices.put((url, seconds))

        def _check_one(wid, url):
            try:
                res = Willexecutors.check_transaction(wid, url, on_busy=_busy)
                return wid, url, res, None
            except Exception as e:
                return wid, url, None, e

        def _fire_tick():
            if on_tick is not None:
                try:
                    on_tick()
                except Exception as cb_err:
                    _logger.error(f"check on_tick callback error: {cb_err}")

        def _pass_busy():
            while True:
                try:
                    url, seconds = busy_notices.get_nowait()
                except queue.Empty:
                    return
                if on_busy is not None:
                    try:
                        on_busy(url, seconds)
                    except Exception as cb_err:
                        _logger.error(f"check on_busy callback error: {cb_err}")

        workers = max(1, min(
            max_workers, sum(min(per_server, len(w)) for w in waiting.values())
        ))
        # Manual pool (no ``with``): we must not block on a hung worker when the
        # global deadline elapses (see push_transactions_parallel for details).
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bal-check")
        fut_to_target = {}
        pending = set()

        def _send_more():
            """Send the next requests, one will-executor after the other.

            WHY in turn: a will-executor with many transactions must not take
            every worker while the others wait.
            """
            sent = True
            while sent:
                sent = False
                for url, wids in waiting.items():
                    if wids and running[url] < per_server:
                        wid = wids.popleft()
                        fut = pool.submit(_check_one, wid, url)
                        fut_to_target[fut] = (wid, url)
                        pending.add(fut)
                        running[url] += 1
                        sent = True

        start = time.time()
        stopped = False
        try:
            _send_more()
            while pending:
                if deadline is not None and (time.time() - start) >= deadline:
                    break
                if should_stop is not None and should_stop():
                    stopped = True
                    break
                slice_timeout = tick_interval
                if deadline is not None:
                    remaining = deadline - (time.time() - start)
                    slice_timeout = max(0.0, min(tick_interval, remaining))
                done, pending_left = wait(
                    pending, timeout=slice_timeout, return_when=FIRST_COMPLETED
                )
                pending.intersection_update(pending_left)
                for fut in done:
                    running[fut_to_target[fut][1]] -= 1
                    try:
                        wid, url, res, exc = fut.result()
                    except Exception as e:
                        _logger.error(
                            f"check_transactions_parallel worker error: {e}"
                        )
                        continue
                    results[wid] = (res, exc)
                    if on_each is not None:
                        try:
                            on_each(wid, url, res, exc)
                        except Exception as cb_err:
                            _logger.error(f"check on_each callback error: {cb_err}")
                _send_more()
                _pass_busy()
                # Drive the elapsed-time counter from the calling thread.
                _fire_tick()
            # Any request still running, or not sent, hit the global deadline.
            late = [fut_to_target[fut] for fut in pending]
            late += [(wid, url) for url, wids in waiting.items() for wid in wids]
            if late and not stopped:
                elapsed = time.time() - start
                _logger.warning(
                    f"check global deadline ({deadline}s) reached after "
                    f"{elapsed:.1f}s; {len(late)} request(s) "
                    f"did not get an answer in time"
                )
                for wid, url in late:
                    if wid in results:
                        continue
                    if on_timeout is not None:
                        try:
                            on_timeout(wid, url)
                        except Exception as cb_err:
                            _logger.error(
                                f"check on_timeout callback error: {cb_err}"
                            )
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)
        return results

    @staticmethod
    def initialize_willexecutor(willexecutor, url, status=None, old_willexecutor=None):
        old_willexecutor=old_willexecutor if old_willexecutor is not None else {}
        willexecutor["url"] = url
        if status is not None:
            willexecutor["status"] = status
        else:
            willexecutor["status"] = old_willexecutor.get("status",willexecutor.get("status","Ko"))
        willexecutor["selected"]=Willexecutors.is_selected(old_willexecutor) or willexecutor.get("selected",False)
        address = old_willexecutor.get("address", willexecutor.get("address", ""))
        if address and not bitcoin.is_address(address, net=constants.net):
            _logger.warning(
                f"invalid address {address!r} for executor {url}, "
                f"falling back to empty"
            )
            address = ""
        willexecutor["address"] = address
        willexecutor["promo_code"]=old_willexecutor.get("promo_code",willexecutor.get("promo_code"))



    @staticmethod
    def download_list(old_willexecutors,welist_server):
        try:
            welist_server = welist_server if welist_server[-1] == '/' else welist_server+'/'
            willexecutors = Willexecutors.send_request(
                "get",
                f"{welist_server}data/{BalPlugin.chainname}?page=0&limit=100",
            )
            if not isinstance(willexecutors, dict):
                _logger.warning(
                    f"unexpected download_list response type: "
                    f"{type(willexecutors).__name__}"
                )
                return {}
            for w in willexecutors:
                if w not in ("status", "url"):
                    if not isinstance(willexecutors.get(w), dict):
                        _logger.warning(
                            f"malformed entry {w!r} in executor list, "
                            f"type={type(willexecutors.get(w)).__name__}"
                        )
                        continue
                    Willexecutors.initialize_willexecutor(
                        willexecutors[w], w, None, old_willexecutors.get(w,None)
                    )
            return willexecutors

        except Exception as e:
            _logger.error(f"Failed to download willexecutors list: {e}")
            return {}

    @staticmethod
    def get_willexecutors_list_from_json():
        try:
            with open("willexecutors.json") as f:
                willexecutors = json.load(f)
                for w in willexecutors:
                    willexecutor = willexecutors[w]
                    Willexecutors.initialize_willexecutor(willexecutor, w, "New", False)
                # bal_plugin.WILLEXECUTORS.set(willexecutors)
                return willexecutors
        except Exception as e:
            _logger.error(f"error opening willexecutors json: {e}")

            return {}

    @staticmethod
    def busy_pause(error, attempt):
        """The pause (seconds) before asking again after ``error`` (#172).

        ``None`` when ``error`` is not a 429 Too Many Requests (aiohttp's
        ``ClientResponseError`` carries ``status`` and ``headers``). For a
        429: the server's ``Retry-After`` seconds when it gives them, else
        CHECK_BUSY_SLEEP doubled at each ``attempt`` (0, 1, 2: 2 s, 4 s,
        8 s); never more than CHECK_BUSY_MAX_SLEEP, so one busy server
        cannot hold the Check for minutes.
        """
        if getattr(error, "status", None) != 429:
            return None
        pause = CHECK_BUSY_SLEEP * (2 ** attempt)
        headers = getattr(error, "headers", None)
        try:
            given = headers.get("Retry-After") if headers else None
            if given is not None:
                pause = max(0, int(float(given)))
        except (TypeError, ValueError, AttributeError):
            pass  # a date or junk: keep our own pause
        return min(pause, CHECK_BUSY_MAX_SLEEP)

    @staticmethod
    def check_transaction(txid, url, *, timeout=CHECK_TIMEOUT,
                          max_retries=CHECK_MAX_RETRIES,
                          retry_sleep=CHECK_RETRY_SLEEP,
                          busy_retries=CHECK_BUSY_RETRIES, on_busy=None):
        """Ask the will-executor at ``url`` for the transaction ``txid``.

        Returns its answer (a dict, ``{"tx": ...}`` when it holds it), or
        ``None`` for an answer that is not a dict. #147: when every attempt
        timed out it returns :data:`NO_ANSWER`, so a slow will-executor is
        not taken for one that does not hold the will.

        #172: a 429 Too Many Requests means the will-executor is busy, not
        that it lacks the transaction: after a pause (:meth:`busy_pause`,
        told to ``on_busy(url, seconds)`` first) it is asked again, at most
        ``busy_retries`` times; still busy, the answer is :data:`NO_ANSWER`
        (the copy stays sent). Before, the 429 was an error and the copy
        was marked failed and sent again. Other errors are raised, as before.
        """
        _logger.debug(f"{url}:{txid}")
        attempt = 0
        while True:
            try:
                w = Willexecutors.send_request(
                    "post", url + "/searchtx", data=txid.encode("ascii"),
                    timeout=timeout, max_retries=max_retries,
                    retry_sleep=retry_sleep,
                )
                break
            except Exception as e:
                pause = Willexecutors.busy_pause(e, attempt)
                if pause is None:
                    _logger.error(f"error contacting {url} for checking txs {e}")
                    raise e
                if attempt >= busy_retries:
                    _logger.info(
                        f"{url} still busy (429) for {txid}: no answer this time"
                    )
                    trace.add(trace.WARNING, _(
                        "{url} still busy (429): no answer this time"
                    ).format(url=trace.short_url(url)), full=True)
                    return NO_ANSWER
                _logger.info(f"{url} busy (429) for {txid}: new try in {pause} s")
                trace.add(trace.WARNING, _(
                    "{url} busy (429), new try in {pause} s"
                ).format(url=trace.short_url(url), pause=pause), full=True)
                if on_busy is not None:
                    on_busy(url, pause)
                if pause:
                    time.sleep(pause)
                attempt += 1
        try:
            if w is None:
                # send_request gives None only when every attempt timed out.
                _logger.info(f"no answer in time from {url} for {txid}")
                return NO_ANSWER
            if not isinstance(w, dict):
                _logger.warning(
                    f"unexpected check_transaction response type "
                    f"from {url}: {type(w).__name__}"
                )
                return None
            return w
        except Exception as e:
            _logger.error(f"error contacting {url} for checking txs {e}")
            raise e

    @staticmethod
    def compute_id(willexecutor):
        return "{}-{}".format(willexecutor.get("url"), willexecutor.get("chain"))


#class WillExecutor:
#    def __init__(
#        self,
#        url,
#        base_fee,
#        chain,
#        info,
#        version,
#        status,
#        is_selected=False,
#        promo_code="",
#    ):
#        self.url = url
#        self.base_fee = base_fee
#        self.chain = chain
#        self.info = info
#        self.version = version
#        self.status = status
#        self.promo_code = promo_code
#        self.is_selected = is_selected
#        self.id = self.compute_id()
#
#    def from_dict(d):
#        return WillExecutor(
#            url=d.get("url", "http://localhost:8000"),
#            base_fee=d.get("base_fee", 1000),
#            chain=d.get("chain", chainname),
#            info=d.get("info", ""),
#            version=d.get("version", 0),
#            status=d.get("status", "Ko"),
#            is_selected=d.get("is_selected", "False"),
#            promo_code=d.get("promo_code", ""),
#        )
#
#    def to_dict(self):
#        return {
#            "url": self.url,
#            "base_fee": self.base_fee,
#            "chain": self.chain,
#            "info": self.info,
#            "version": self.version,
#            "promo_code": self.promo_code,
#        }
#
#    def compute_id(self):
#        return f"{self.url}-{self.chain}"

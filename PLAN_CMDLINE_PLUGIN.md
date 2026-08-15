# Piano: supporto da riga di comando (CLI) per il plugin BAL

> **Stato**: solo piano. Nessun codice viene modificato finché il piano non viene approvato.
>
> **Versione di riferimento**: commit `2221389` (`core: anchor relative locktime/threshold recipes...`), working tree pulito.

---

## 1. Obiettivo

Rendere il plugin **Bitcoin After Life** utilizzabile da riga di comando / daemon
di Electrum, senza GUI Qt, esponendo comandi per:

1. **Willexecutors** — elenco, aggiunta, modifica, selezione, eliminazione, import/export, ping, download lista.
2. **Heirs** — elenco, aggiunta, modifica, eliminazione, import/export.
3. **Impostazioni** — lettura e modifica (`settings set chiave=valore`), reset a default.
4. **Will** — ciclo di vita completo: visualizza stato, check di coerenza, prepara/ricostruisci, firma, import/merge, esporta, invalida, trasmette ai will-executor, verifica lato will-executor (searchtx).

Il tutto riusando **esclusivamente la logica già presente in `bal/core/`** (che è
già GUI-free) e senza importare mai PyQt.

---

## 2. Stato attuale (verificato sul codice)

### 2.1 Meccanica di Electrum (4.8.0, checkout `electrum/`)

Ho verificato sul codice reale (`electrum/commands.py`, `electrum/plugin.py`,
`electrum/daemon.py`, `run_electrum`) i punti che governano i comandi dei plugin:

- **Registrazione comandi**: `@plugin_command(s, plugin_name)` in
  `electrum/commands.py:2317`. Un comando plugin:
  - è **sempre** un `async def`;
  - viene registrato come `bal_<nome_funzione>` su `Commands` (quindi anche nel parser CLI);
  - **forza il flag `'n'`** (richiede rete/daemon): *tutti* i comandi plugin richiedono un daemon in esecuzione e NON funzionano con `--offline`;
  - alla chiamata inietta `plugin = daemon._plugins.get_plugin('bal')` (riga 2337).
- **Pre-parse CLI** (`run_electrum` riga 425): `Plugins(tmp_config, cmd_only=True)` importa solo l'`__init__.py` di ogni plugin abilitato per registrare i comandi nel parser. In modalità `cmd_only` il filtro `available_for` viene **saltato** (`plugin.py:128`), ma serve `config['plugins.bal.enabled'] is True` (`plugin.py:117`).
- **Daemon** (`daemon.py:626`): `Plugins(self.config, 'cmdline')`. Qui il filtro `available_for` **vale**: il plugin deve dichiarare `"cmdline"`.
- **Caricamento entry-point** (`plugin.py:622`): il daemon importa `electrum.plugins.bal.<gui_name>` con `gui_name='cmdline'`, quindi serve un modulo `bal/cmdline.py` con una classe `Plugin`.
- **Iniezione wallet**: il decorator `@command` (righe 170-194) gestisce i flag:
  - `'w'` → risolve e inietta `wallet` da `daemon.get_wallet(wallet_path)` (il wallet deve essere già caricato con `electrum load_wallet`);
  - `'p'` → richiede `--password` (o wallet già sbloccato) per le operazioni di firma.
- **Output**: il valore di ritorno del comando viene stampato come JSON da `run_electrum` (righe 626-630); in modalità daemon gli errori `UserFacingException` vengono stampati con exit code 1.

### 2.2 Il plugin (bal v0.6.1)

- `bal/core/` è già GUI-free e contiene tutta la logica riutilizzabile:
  - `heirs.py` — `Heirs` (dict persistito in wallet DB, chiave `"heirs"`), validazione (`validate_heir`, `_validate`), `import_file`/`export_file`, `get_transactions`/`buildTransactions`.
  - `willexecutors.py` — `Willexecutors` (config `bal_willexecutors`, chiave per `chainname`), `get_willexecutors`, `save`, `initialize_willexecutor`, `is_selected`, `is_valid`, `ping_servers_parallel`, `push_transactions_parallel`, `check_transactions_parallel`, `check_transaction`, `download_list`, `get_willexecutors_list_from_json`.
  - `will.py` — `Will` (statiche) e `WillItem` (stato per-tx: `VALID/COMPLETE/PUSHED/CHECKED/...`), `is_will_valid`, `check_will`, `check_willexecutors_and_heirs`, `invalidate_will`, `normalize_will`, `get_min_locktime`, `get_tx_from_any`, `set_check_willexecutor`, `save_valid_transactions_to_history`.
  - `plugin_base.py` — `BalPlugin` (tutte le `BalConfig`: chiavi `bal_*`), `BalTimestamp`, `get_version`, registrazione dei dict `heirs`/`will`/`will_settings` nel wallet DB.
  - `checkalive.py` — `resolve_date_to_check`, `check_alive_expired` (riferimento temporale unico per ogni check).
  - `util.py` — `Util` (locktime, quantità, confronto tx/heirs, `get_available_utxos`, `fix_will_settings_tx_fees`).
- `bal/gui/qt/window.py` — `BalWindow` contiene i flussi da **replicare in headless** (non riusabile direttamente perché legato a Qt):
  - `init_will` (riga 151), `load_willitems`/`save_willitems` (120/129),
  - `init_class_variables` (618) e `build_will` (397),
  - `build_inheritance_transaction` (678) → il flusso completo "prepara will",
  - `sign_transactions` (952), `ask_password_and_sign_transactions` (1084),
  - `push_transactions_to_willexecutors` (1164), `broadcast_transactions` (1127),
  - `check_transactions_task`/`check_transactions` (1414/1464),
  - `export_json_file` (1246), `merge_will` (1264), `merge_will_from_file` (1348), `_load_will_file` (1406),
  - `invalidate_will` (917).
- `bal/manifest.json`: `"available_for": ["qt"]`, `"version": "0.6.1"`.
- `build_zip.py`: cammina ricorsivamente su `bal/` (esclude `__pycache__`, `.pyc`), quindi **includerà automaticamente** i nuovi file di `bal/cli/` e `bal/cmdline.py`.

---

## 3. Architettura proposta

```
bal/
  __init__.py        # MODIFICATO: importa ``from .cli import commands`` (registra i comandi)
  cmdline.py         # NUOVO: shim zip-safe (come qt.py) che ri-espone Plugin da bal.cli.plugin
  cli/
    __init__.py      # NUOVO
    commands.py      # NUOVO: tutti i @plugin_command (async), sottili, delegano al controller
    controller.py    # NUOVO: BalController — facciata headless per-wallet (replica di BalWindow senza Qt)
    plugin.py        # NUOVO: class Plugin(BalPlugin) — entry-point per il daemon (gui_name='cmdline')
  manifest.json      # MODIFICATO: available_for = ["qt", "cmdline"]
```

Principi:

- **`bal/cli/` non importa mai Qt** (stessa regola di `bal/core/`). Può importare solo `bal.core`, `electrum.*` e stdlib.
- **`commands.py` = livello di trasporto**: firma `async def bal_x(self, wallet=None, plugin=None, ...)`, valida/parsa argomenti, chiama il controller, ritorna strutture JSON-serializzabili. Zero logica di business.
- **`controller.py` = il cuore**: replica i passi GUI-free di `BalWindow`, ma con errori espressi come eccezioni (i messaggi GUI `show_message`/`show_error` diventano raise/ritorni), e persiste esplicitamente su wallet DB.
- **`plugin.py`** è quasi vuoto: eredita `BalPlugin.__init__` e basta (serve solo perché Electrum istanzi `module.Plugin(self, config, name)`).
- **Nessuna dipendenza nuova** richiesta: `aiohttp`, `dns` e il resto sono già usati da `bal/core`.

### 3.1 Perché i comandi richiedono il daemon

`plugin_command` forza il flag `'n'` in `commands.py:2321-2322`. Conseguenza
architetturale da documentare chiaramente:

```
electrum daemon -d                 # avvia il daemon (rete + plugin cmdline)
electrum load_wallet               # carica/sblocca il wallet
electrum bal_heirs_list            # i comandi BAL girano contro il daemon
```

Questa è la stessa limitazione di tutti gli altri plugin con comandi CLI
(es. `swapserver`, `nwc`). Non è aggirabile senza hackare `plugin_command`, che
escludiamo dal piano.

---

## 4. Modifiche ai file esistenti

### 4.1 `bal/manifest.json`
- `"available_for": ["qt", "cmdline"]`.

Nessun cambio di versione necessario per lo sviluppo; la versione si alzerà in
`make-release.sh` come già avviene.

### 4.2 `bal/__init__.py`
- Aggiungere in fondo:
  ```python
  # Registra i comandi CLI (bal_*) appena Electrum importa il pacchetto,
  # sia in modalità cmd_only (pre-parse) sia nel daemon.
  from . import cli  # noqa: F401  (importa bal.cli.commands, che registra i @plugin_command)
  ```
  (oppure `from .cli import commands` esplicito).
- Accortezza: `bal/cli/commands.py` deve essere importabile **senza Qt** e senza
  effetti collaterali pesanti, perché viene importato anche nel pre-parse CLI e
  all'avvio della GUI.

### 4.3 `build_zip.py`
- Nessuna modifica obbligatoria: il walker include già `cli/` e `cmdline.py`.
- **Opzionale (consigliato)**: aggiungere una stampa di avviso quando l'archivio
  contiene sia `cmdline.py` che `qt.py`, e verificare che `manifest.json` abbia
  entrambi i valori in `available_for`.

---

## 5. Nuovi file

### 5.1 `bal/cmdline.py` (shim, ~stesso schema di `qt.py`)

Riproduce il pattern zip-safe di `qt.py` (creazione dei package intermedi in
`sys.modules`, import via `importlib.import_module`), ma punta a
`bal.cli.plugin`:

```python
Plugin = _plugin_module.Plugin
```

### 5.2 `bal/cli/plugin.py`

```python
class Plugin(BalPlugin):
    def __init__(self, parent, config, name):
        BalPlugin.__init__(self, parent, config, name)
```

Niente hook Qt, niente `bal_windows`. Il daemon lo istanzia quando
`get_plugin('bal')` viene chiamato dal wrapper di `plugin_command`.

### 5.3 `bal/cli/controller.py` — `BalController`

Facciata per-wallet che incapsula lo stato e i flussi. Attributi (speculari a
`BalWindow`):
- `plugin` (il `BalPlugin`/`Plugin` iniettato),
- `wallet` (iniettato da Electrum),
- `will_settings` (da `plugin.WILL_SETTINGS.get()` + `Util.fix_will_settings_tx_fees`),
- `heirs` (`Heirs(wallet)` validati),
- `willexecutors` (`Willexecutors.get_willexecutors(plugin)`),
- `willitems` (da `wallet.db.get_dict("will")` → `WillItem(w, wallet=wallet)`),
- `date_to_check` (via `resolve_date_to_check`).

Metodi principali (replicano le funzioni Qt, senza dialoghi):

| Metodo | Replica di (`window.py`) | Note |
|---|---|---|
| `load_willitems()` | 120 | Costruisce i `WillItem` dal dict `will` del wallet DB. |
| `save_willitems()` | 129 | `to_dict()` con `tx` serializzato a stringa, `json.dumps` di prova, scrittura su `wallet.db` + `wallet.save_db()`. |
| `init_class_variables()` | 618 | `date_to_check`, `no_willexecutor`, `willexecutors`, check `check_alive_expired`. |
| `check_will()` | 473 | `Will.is_will_valid(...)`; le eccezioni di dominio vengono propagate al comando. |
| `build_inheritance_transaction()` | 678 | Flusso 1/7→2/7 replicato: `Will.check_amounts`, guardie locktime/willexecutor, `check_will()` e rebuild su `NotCompleteWillException`. Le `show_message/show_error` diventano raise (`UserFacingException` con testo chiaro) oppure ritorni `{"status": "postponed", "invalidation": tx}`. |
| `sign_transactions(password)` | 952 | Firma i `VALID` non completi: fixup input dai willitems padre, `wallet.sign_transaction(tx, password, ignore_warnings=True)`, `set_status("COMPLETE")`, `check_signatures`. |
| `push_transactions_to_willexecutors(force)` | 1164 | `get_willexecutor_transactions` + `push_transactions_parallel` + gestione "already present" con `check_transaction`. Aggiorna `PUSHED/PUSH_FAIL`. |
| `check_transactions()` | 1414 | `check_transactions_parallel` + `set_check_willexecutor(res)` per item. |
| `export_json_file(path)` | 1246 | `write_json_file(path, {wid: wi.to_dict()...})` con `tx` come stringa (formato identico a `_load_will_file`). |
| `merge_will_from_file(path)` | 1348 | `_load_will_file` + `merge_will` (stessa semantica di `window.py:1264`). |
| `_load_will_file(path)` | 1406 | `read_json_file` + `tx_from_any` + `WillItem`. |
| `invalidate_will()` | 917 | `Will.invalidate_will(...)` con `history_label` e `will_locktime`. |
| `fetch_will_executors_list()` / `ping()` | 1491/1771 | `download_list(old, welist_server)` + `ping_servers_parallel`, poi `Willexecutors.save(plugin, ...)`. |
| `apply_settings(cfg_name, value)` | — | Mappa il nome chiave all'attributo `BalConfig` del plugin e fa `set(...)`. |

Regole di persistenza (fondamentali):
- **heirs** → `heirs.save()` (via `__setitem__`/`pop` già implementati) + `wallet.save_db()`.
- **will** → `save_willitems()` + `wallet.save_db()`.
- **willexecutors** → `Willexecutors.save(plugin, willexecutors)` (config, non wallet DB).
- **settings** → `BalConfig.set(...)` (config).

### 5.4 `bal/cli/commands.py` — comandi (tutti `async def` + `@plugin_command`)

Firma standard: `async def bal_x(self, wallet=None, plugin=None, ...)`. Flag:
- `'n'` — imposto automaticamente da `plugin_command` (rete/daemon).
- `'w'` — wallet richiesto e iniettato da Electrum.
- `'p'` — solo per i comandi che firmano (richiede `--password`).

Tutti i comandi costruiscono `controller = BalController(plugin, wallet)` e
ritornano strutture JSON-serializzabili. Elenco completo al §6.

---

## 6. Tabella comandi

Convenzioni:
- `<WALLET>`: wallet caricato nel daemon (non serve passarlo; Electrum usa quello
  configurato o `--wallet`).
- Output: `list`/`dict` stampati come JSON; exit 0 su successo, 1 su errore.
- `*` = richiede password (`--password`) se il wallet è cifrato.

### 6.1 Willexecutors

| Comando | Flag | Argomenti | Descrizione / output |
|---|---|---|---|
| `bal_willexecutors_list` | `nw` | — | Elenco `{url: {address, base_fee, status, info, selected, last_update, sort}}` per la chain corrente. |
| `bal_willexecutors_show` | `nw` | `url` | Dettaglio di un singolo will-executor. |
| `bal_willexecutors_add` | `nw` | `url` `address` `base_fee` | Aggiunge/aggiorna un will-executor (via `initialize_willexecutor`), `selected=false` di default. Ritorna il record. |
| `bal_willexecutors_update` | `nw` | `url` `[address]` `[base_fee]` `[info]` `[promo_code]` | Modifica i campi indicati e salva. |
| `bal_willexecutors_select` | `nw` | `url` `value` | `is_selected(we, eval_bool(value))` + salva. |
| `bal_willexecutors_delete` | `nw` | `url` | Rimuove dalla lista e salva. |
| `bal_willexecutors_ping` | `nw` | `[url]` | `ping_servers_parallel` (tutti o uno); aggiorna `status/base_fee/address`; salva. Output: risultati per url. |
| `bal_willexecutors_download` | `nw` | — | `download_list(old, plugin.WELIST_SERVER.get())`; unisce e salva. Output: n. record. |
| `bal_willexecutors_import` | `nw` | `path` | Legge un JSON `{url: record}` (stesso formato di export), `initialize_willexecutor` per record, salva. |
| `bal_willexecutors_export` | `nw` | `path` | Scrive `{url: record}` su file JSON. |

### 6.2 Heirs

| Comando | Flag | Argomenti | Descrizione / output |
|---|---|---|---|
| `bal_heirs_list` | `nw` | — | `{name: [address, amount, locktime, ...]}` (tutte le colonne `HEIR_*`). |
| `bal_heirs_show` | `nw` | `name` | Dettaglio di un singolo heir. |
| `bal_heirs_add` | `nw` | `name` `address` `amount` `locktime` | Valida con `Heirs.validate_heir` (OP_RETURN incluso) e salva. `amount` può essere satoshi o `"50%"`. `locktime` può essere timestamp assoluto o relativo `"30d"`/`"1y"`. |
| `bal_heirs_update` | `nw` | `name` `[address]` `[amount]` `[locktime]` | Modifica i campi indicati (ri-validazione) e salva. |
| `bal_heirs_delete` | `nw` | `name` | `heirs.pop(name)` + `save_db()`. |
| `bal_heirs_import` | `nw` | `path` | `Heirs.import_file(path)` (validazione + merge). |
| `bal_heirs_export` | `nw` | `path` | `Heirs.export_file(path)`. |

### 6.3 Impostazioni

| Comando | Flag | Argomenti | Descrizione / output |
|---|---|---|---|
| `bal_settings_list` | `n` | — | Elenco di tutte le `BalConfig` del plugin: `{chiave: {value, default, name}}` (nome leggibile). |
| `bal_settings_get` | `n` | `key` | Valore corrente di una chiave (`bal_*`). |
| `bal_settings_set` | `n` | `key=value` | Scrive il valore (conversione di tipo: bool/int/str/JSON) via `BalConfig.set(...)`. `bal_will_settings` accetta JSON. |
| `bal_settings_reset` | `n` | `key` | `BalConfig.set(cfg.default)`. |

### 6.4 Will

| Comando | Flag | Argomenti | Descrizione / output |
|---|---|---|---|
| `bal_will_status` | `nw` | — | Per ogni `wid` (txid): locktime, `heirsvalue`, executor, flag di stato (`VALID/COMPLETE/PUSHED/CHECKED/CHECK_FAIL/...`), `sigs_have/sigs_required`, `tx_fees`, executor URL. |
| `bal_will_check` | `nw` | — | `check_will()` (coerenza heirs+executor+fees+locktime, in locale). Ritorna `{"valid": true}` o un errore esplicito (es. `HeirNotFound`, `WillPostponed`, `WillExpired`, `NoHeirs`). |
| `bal_will_prepare` | `nw` | — | Flusso completo `build_inheritance_transaction`: check → rebuild se non coerente → persiste. Output: riepilogo tx nuova/aggiornata per wid. |
| `bal_will_sign` | `nwp` | `[txid]` | Firma i `VALID` non completi (o solo `txid`). Aggiorna `COMPLETE` e `sigs_*`; persiste. Output per txid. |
| `bal_will_broadcast` | `nw` | `[txid]` `force` | `push_transactions_to_willexecutors(force, txids)` parallelo; aggiorna `PUSHED/PUSH_FAIL`. Output: `{url: status}`. |
| `bal_will_export` | `nw` | `path` | `export_json_file(path)`. |
| `bal_will_import_merge` | `nw` | `path` | `merge_will_from_file(path)` (stessa semantica GUI: merge psbt/stati, mai perdere una tx viva). |
| `bal_will_invalidate` | `nw` | — | `Will.invalidate_will(...)`; ritorna la tx di invalidazione (da firmare+trasmettere con i comandi sopra). |
| `bal_will_check_executor` | `nw` | `[txid]` | Verifica lato will-executor: `check_transactions_parallel` (searchtx) per i `VALID+PUSHED` non `CHECKED`; applica `set_check_willexecutor`. Output: `{wid: {url, checked, ok}}`. |

---

## 7. Flusso dati e persistenza

```
CLI (electrum bal_*)                    Daemon (Electrum 4.8.0)
┌───────────────────────┐               ┌──────────────────────────────────────┐
│ run_electrum          │  RPC           │ Daemon.run_cmdline                   │
│  pre-parse cmd_only   │ ─────────────► │  plugin_command wrapper              │
│   -> importa bal      │  jsonrpc       │    inietta plugin + wallet           │
│  (registra bal_*)     │               │  bal/cli/commands.py                 │
└───────────────────────┘               │    -> BalController(plugin, wallet)  │
                                        │      -> bal.core.*                   │
                                        │      -> wallet.db / config (persist) │
                                        └──────────────────────────────────────┘
```

- **Lettura**: `wallet.db.get_dict("will")` (wills), `Heirs(wallet)` (heirs),
  `plugin.WILLEXECUTORS.get()`/`plugin.WILL_SETTINGS.get()` (config).
- **Scrittura**: `save_willitems()` → `wallet.db` + `wallet.save_db()`;
  `heirs.save()`; `Willexecutors.save(...)`; `BalConfig.set(...)`.
- **Firma**: `wallet.sign_transaction(tx, password, ignore_warnings=True)` —
  idem GUI, quindi compatibile con multisig e wallet cifrati (password via `--password`).
- **Rete**: `Network.get_instance()` già usato da `bal/core/willexecutors.py`
  (i comandi `'n'` garantiscono rete attiva).

---

## 8. Errori, exit code, output

- Ritorno `None` → nessun output; `str` → stampato; `dict`/`list` → `json_encode`.
- Errori utente: sollevare `electrum.util.UserFacingException(msg)` → in modalità
  daemon viene stampato `msg` con exit 1.
- Errori di dominio BAL (`WillExpiredException`, `WillPostponedException`,
  `HeirNotFoundException`, `NoWillExecutorNotPresent`, `CheckAliveError`,
  `AmountException`, ...): il controller le converte in `UserFacingException`
  con testo in chiaro (riuso dei messaggi già presenti, senza HTML/Qt).
- Convenzione consigliata per comandi che producono più di un risultato:
  ritornare un `dict` con chiave `"result"`/`"warnings"` quando servono avvisi
  (es. dopo `prepare` con heirs scartati per dust).

---

## 9. Compatibilità Electrum 4.7.2 / 4.8.0

- `plugin_command`, il wrapper `@command` e `daemon._plugins.get_plugin` esistono
  in entrambe le versioni (verificati su 4.8.0; usati identici da `swapserver`).
- Il `BalPlugin` già gestisce il cambio API di registrazione dict
  (`json_db.register_dict` vs `stored_dict.register_name`): nessun intervento.
- `available_for: ["cmdline"]` è lo stesso meccanismo di `trustedcoin`
  (che ha già `cmdline.py` in 4.8.0).
- **Nessun nuovo import Qt** in `bal/cli/`: verificabile in CI con un check
  statico su `bal/cli/*.py` e `bal/cmdline.py`.

---

## 10. Build / release

- `python3 build_zip.py` produce `bal-electrum-plugin.zip` con `cli/`, `cmdline.py`
  e il manifest aggiornato. Lo zip serve sia per la GUI che per il daemon.
- Il test `external_zip_test.py` andrà esteso (vedi §11) per verificare che il
  zip, caricato da Electrum, registri anche i comandi `bal_*`.
- Nessun cambiamento a `make-release.sh` (la versione resta nel manifest).

---

## 11. Piano di test e verifica

### 11.1 Nuovi test standalone (stile repo: `tests/test_*.py` con `if __name__ == "__main__"`)

- `tests/test_cli_commands_registered.py` (runtime env):
  - importa `electrum.plugins.bal` con `Plugins(config, cmd_only=True)`;
  - asserisce che `known_commands` contenga tutti i nomi `bal_*` della tabella;
  - asserisce che ogni funzione sia coroutine e abbia il flag `n`.
- `tests/test_cli_controller.py` (runtime env, offline, senza rete):
  - wallet "fake"/temporaneo (pattern di `test_core_heirs.py`);
  - CRUD heirs e willexecutors, settings get/set/reset, export/import will
    (merge), build will con fixtures note.
- `tests/test_cli_zip.py` (o estensione di `external_zip_test.py`):
  - costruisce lo zip, lo carica come `electrum_external_plugins.bal` con
    `Plugins(config, 'cmdline')`, asserisce `available_for` include `"cmdline"`
    e che `get_plugin('bal')` restituisca il `Plugin` di `bal.cli.plugin`
    (nessun import Qt eseguito).
- `tests/test_cli_will_flows.py` (offline, dove possibile):
  - prepare → sign → export → merge su un wallet di test con heirs fissi;
  - verifica che `wallet.db.get_dict("will")` rifletta COMPLETE/PUSHED dopo
    le operazioni che non toccano rete.

### 11.2 Verifica manuale (da documentare nel README/HANDOFF)

```bash
source "$BAL_HOME/electrum/env/bin/activate"
electrum daemon -d
electrum load_wallet
electrum bal_heirs_list
electrum bal_settings_list
electrum bal_will_status
electrum bal_will_prepare
electrum bal_will_sign --password '...'          # se wallet cifrato
electrum bal_will_broadcast
electrum bal_will_check_executor
electrum bal_willexecutors_ping
electrum stop
```

### 11.3 Regressione

- `QT_QPA_PLATFORM=offscreen python3 tests/smoke_test.py electrum.plugins.bal`
  deve continuare a passare (prova che `bal/__init__` + Qt convivono con il
  nuovo import di `bal.cli.commands`).
- Eseguire i `test_core_*.py` esistenti (nessuna logica core toccata).
- Ruff: evitare nuove violazioni in `bal/cli/`.

---

## 12. Rischi e decisioni aperte

1. **Daemon obbligatorio** (non `--offline`): imposto da `plugin_command`.
   → Accettato; documentato al §3.1.
2. **Wallet pre-caricato**: i comandi `w` falliscono con "wallet not loaded" se
   non si lancia prima `electrum load_wallet`. → Documentare.
3. **`bal/__init__.py` che importa `bal.cli.commands`**: viene eseguito anche
   all'avvio della GUI. `commands.py` deve restare leggero (solo definizioni +
   import di `electrum.commands` e `bal.core`). Da verificare con `smoke_test.py`.
4. **Doppio caricamento**: se un install è contemporaneamente interno E zip
   esterno, la seconda importazione di `commands.py` potrebbe sollevare
   "Command name bal_... already exists". Pratica corrente: un solo install;
   si può mitigare con un guard `if not getattr(module, '_registered')`.
5. **OP_RETURN heirs** in CLI: gestiti come in GUI (`validate_op_return_hex`,
   colonne quantità `"0"`). Da testare.
6. **Persistenza `will_settings`**: oggi letta dalla config globale
   (`bal_will_settings`) in `BalWindow.__init__`, non dal wallet DB. Il
   controller deve replicare esattamente questo (config), non introdurre una
   seconda sorgente.
7. **Multisig**: la firma usa `wallet.sign_transaction` → supportata; il flusso
   "merge PSBT" copre la firma parziale. Test dedicato con wallet multisig in
   fase di implementazione.

---

## 13. Fasi di implementazione (ordine proposto)

1. `bal/cli/__init__.py`, `bal/cli/plugin.py`, `bal/cmdline.py`, update
   `bal/manifest.json` + `bal/__init__.py`.
2. `tests/test_cli_commands_registered.py` + verifica `smoke_test.py`.
3. `bal/cli/controller.py` (read-only: status/list/show) → `commands.py` per
   willexecutors/heirs/settings (senza rete).
4. Comandi will: `prepare`, `sign`, `export`, `import_merge`, `invalidate`.
5. Comandi di rete: `ping`, `download`, `broadcast`, `check_executor`.
6. Test zip (`test_cli_zip.py`), estensione `external_zip_test.py`, prova
   manuale col daemon, aggiornamento README/HANDOFF.

<p align="center">
<img src="https://github.com/iterativ/openopc2/raw/develop/doc/assets/open-opc.png" alt="OpenOPC" width="700"/>
</p>

# opcda2ua — OPC DA → OPC UA Bridge

This project is a **fork of [iterativ/openopc2](https://github.com/iterativ/openopc2)**
(itself based on the original [OpenOPC](https://github.com/joseamaita/openopc) library
by Barry Barnreiter). It reuses the OPC DA client core from those projects, but from a
user's point of view it works **completely differently**: instead of a Python library or
a Pyro gateway, this fork is a **bridge** that connects to a legacy *OPC Classic (DA)*
server and republishes every tag as **OPC UA**, so any standard OPC UA client can read
(and optionally write) the data — no DCOM, no Pyro, no 32-bit Python on the client side.

The bridge and its tooling were developed with
[**Claude Code**](https://claude.com/claude-code) (Anthropic's agentic CLI) and
**validated against real OPC DA servers** (Matrikon simulation, GE iFIX /
`Intellution.OPCiFIX`, and WinCC-based systems) running on Windows 10/11, Windows 7
and even Windows XP / Server 2003.

> Full credit for the underlying OPC DA core goes to the original OpenOPC and OpenOPC 2
> authors — see [Credits](#-credits). This fork only adds the OPC UA bridging layer on
> top of their work.

## ✨ What this fork does

- **OPC DA → OPC UA bridge** (`openopc2.ua_server`): connects to an OPC DA server and
  exposes every tag as an OPC UA node.
- **Bidirectional writes** (opt-in with `--enable-writes`): OPC UA clients can write
  back to the underlying OPC DA tags.
- **iFIX support**: optimized tag discovery for GE iFIX (`Intellution.OPCiFIX`),
  including `.F_CV` / `.A_CV` handling and container-based browsing
  (see [`docs/ifix_specification.md`](docs/ifix_specification.md)).
- **Periodic tag refresh** so newly added / removed DA tags appear automatically.
- **Legacy edition for Windows XP / Server 2003** ([`winxp/`](winxp/README.md)): a
  standalone Python 2.7 read-only bridge for machines that cannot run Python 3.8+.
- **Diagnostic tooling**: `--list-servers`, `--test-connection`, `--read-tag`,
  `--discover` for quick troubleshooting against real servers.

## 📋 Requirements

The bridge runs on the **Windows machine that hosts (or can reach) the OPC DA server**:

- **32-bit Python 3.8+** — OPC Classic DLLs are 32-bit, so the bridge process must be
  32-bit too. (The legacy WinXP edition uses Python 2.7; see [`winxp/`](winxp/README.md).)
- The **Graybox OPC automation wrapper** `gbda_aut.dll` (included in [`lib/`](lib/)),
  registered on the system — see below.
- `asyncua` for the OPC UA server:

  ```console
  pip install asyncua
  ```

### Register the OPC wrapper DLL

The bridge talks to OPC DA through `gbda_aut.dll` (the GrayboxOpcDa wrapper,
<http://gray-box.net/daawrapper.php?lang=en>). Register it once, from an
**administrator** command prompt:

```shell
C:\opcda2ua\lib> regsvr32 gbda_aut.dll
```

To unregister later:

```shell
C:\opcda2ua\lib> regsvr32 gbda_aut.dll -u
```

## 🚀 Usage

Start the bridge against a local OPC DA server:

```console
python -m openopc2.ua_server --opc-server "Matrikon.OPC.Simulation.1"
```

Connect a remote OPC DA server and expose it on a custom OPC UA endpoint:

```console
python -m openopc2.ua_server --opc-server "MyServer.1" --opc-host 192.168.1.100 \
    --ua-endpoint "opc.tcp://0.0.0.0:4841/mybridge/"
```

Any OPC UA client can then connect to `opc.tcp://<bridge-host>:4840/openopc2/`.
Tags appear under `Objects`.

### Main options

| Option | Default | Description |
|--------|---------|-------------|
| `--opc-server` | (from config) | OPC DA server name (e.g. `Matrikon.OPC.Simulation.1`) |
| `--opc-host` | `localhost` | Host running the OPC DA server |
| `--ua-endpoint` | `opc.tcp://0.0.0.0:4840/openopc2/` | OPC UA endpoint to publish |
| `--enable-writes` | off | Allow OPC UA clients to write back to OPC DA (use with care) |
| `--ifix-optimized` | off | Fast iFIX discovery (enumerate containers, assume `.F_CV`) |
| `--ifix-only-fcv` | off | Only expose tags ending in `.F_CV` |
| `--no-descriptions` | off | Skip reading tag descriptions (faster startup) |
| `--tag-refresh-interval` | `600` | Seconds between tag re-discovery (`0` to disable) |
| `--update-rate` | `1000` | Subscription update rate in ms |
| `--list-servers` | — | List available OPC DA servers and exit |
| `--test-connection` | — | Test the OPC DA connection and exit |
| `--read-tag TAG` | — | Read a single tag (diagnostics) and exit |
| `--discover [N]` | — | List the first N discovered tags and exit |
| `--log-level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

The same bridge is also available through the CLI as `openopc2 serve-ua`
(see [CLI.md](CLI.md)).

> ⚠️ **Security:** the OPC UA server listens on all interfaces (`0.0.0.0`) and exposes
> no authentication by default. Restrict access at the network/firewall level, and only
> enable `--enable-writes` when you really need write-back.

### Legacy bridge (Windows XP / Server 2003)

For machines that cannot run Python 3.8+, a standalone Python 2.7 read-only bridge is
provided under [`winxp/`](winxp/README.md), including a pre-built executable.

## 🙏 Credits

This is a fork. The OPC UA bridge added here builds on top of **OpenOPC 2** and the
original **OpenOPC** library — all credit for the OPC DA core goes to their authors.
Without the great work of all the contributors, this would not be possible.

| Years     |     | Name              | User                          |
| --------- | --- | ----------------- | ----------------------------- |
| 2008-2012 | 🇺🇸  | Barry Barnreiter  | barry_b@users.sourceforge.net |
| 2014      | 🇷🇺  | Anton D. Kachalov | https://github.com/ya-mouse   |
| 2017      | 🇻🇪  | José A. Maita     | https://github.com/joseamaita |
| 2022      | 🇨🇭  | Lorenz Padberg    | https://github.com/renzop     |
| 2022      | 🇨🇭  | Elia Bieri        | https://github.com/eliabieri  |

The OPC UA bridge, iFIX support and legacy WinXP edition in this fork were developed
with [Claude Code](https://claude.com/claude-code) and tested against real OPC DA
systems.

## 📜 License

This software is licensed under the terms of the GNU GPL v2 license plus
a special linking exception for portions of the package. This license is
available in the `LICENSE.txt` file.

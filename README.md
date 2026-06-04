<p align="center">
<img src="https://github.com/iterativ/openopc2/raw/develop/doc/assets/open-opc.png" alt="OpenOPC" width="700"/>
</p>

# opcda2ua — OPC DA → OPC UA Bridge

This project is a **fork of [iterativ/openopc2](https://github.com/iterativ/openopc2)**
(itself based on the original [OpenOPC](https://github.com/joseamaita/openopc) library
by Barry Barnreiter). It keeps everything the upstream library offers and adds an
**OPC DA → OPC UA bridge** so that legacy *OPC Classic (DA)* servers can be exposed
to any modern *OPC UA* client.

The bridge and the supporting tooling in this fork were developed with
[**Claude Code**](https://claude.com/claude-code) (Anthropic's agentic CLI) and
**validated against real OPC DA servers** (Matrikon simulation, GE iFIX /
`Intellution.OPCiFIX`, and WinCC-based systems) running on Windows 10/11, Windows 7
and even Windows XP / Server 2003.

> Full credit for the underlying OPC DA library and Gateway goes to the original
> OpenOPC and OpenOPC 2 authors — see [Credits](#-credits). This fork only adds the
> OPC UA bridging layer on top of their work.

## ✨ What this fork adds

- **OPC DA → OPC UA bridge** (`openopc2.ua_server`): connects to an OPC DA server and
  republishes every tag as an OPC UA node, so any OPC UA client can read the values
  without DCOM, Pyro or a 32-bit Python on the client side.
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

---

## 🚀 OPC UA Bridge — quick start

The bridge requires the Graybox OPC automation wrapper (`gbda_aut.dll`, see
[Configuration](#️-configuration)) and `asyncua`:

```console
pip install openopc2[ua]   # or: pip install asyncua
```

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

### Main options (`python -m openopc2.ua_server`)

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

> ⚠️ The OPC UA server listens on all interfaces (`0.0.0.0`) and exposes no
> authentication by default. Restrict access at the network/firewall level, and only
> enable `--enable-writes` when you really need write-back.

### Legacy bridge (Windows XP / Server 2003)

For machines that cannot run Python 3.8+, a standalone Python 2.7 read-only bridge is
provided under [`winxp/`](winxp/README.md), including a pre-built executable.

---

**OpenOPC 2** is a Python Library for OPC DA. It is Open source and free for everyone. It allows you to use
[OPC Classic](https://opcfoundation.org/about/opc-technologies/opc-classic/) (OPC Data Access) in
modern Python environments. OPC Classic is a pure Windows technology by design, but this library includes a Gateway Server
that lets you use OPC Classic on any architecture (Linux, MacOS, Windows, Docker). So this Library creates a gateway
between 2022 and the late 90ties. Like cruising into the sunset with Marty McFly in a Tesla.

OpenOPC 2 is based on the OpenOPC Library that was initially created by Barry Barnleitner and hosted on Source Forge, but
It was completely refactorerd and migrated to Python 3.8+

# 🔥 Features

- An OpenOPC Gateway Service (a Windows service providing remote access
  to the OpenOPC library, which is useful to avoid DCOM issues).
- Command Line Interface (CLI)
- Enables you to use OPC Classic with any Platform
- CLI and Gateway are independent Executables that do not require Python
- A system check module (allows you to check the health of your system)
- A free OPC automation wrapper (required DLL file).
- General documentation with updated procedures (this file).

# 🐍 OpenOPC vs OpenOPC 2

Open OPC 2 is based on OpenOPC and should be seen as a successor. If you already have an application that is based on
OpenOPC, you can migrate with a minimal effort. Our main motivation to build this new version was to improve the developer
experience and create a base for other developers that is easier to maintain, test and work with...

- Simpler installation
- Mostly the same api (but we take the freedom to not be compatible)
- No memory leak in the OpenOpcService 🎉
- Python 3.8+ (tested with 3.10)
- Typings
- Pyro5, increased security
- We added tests 😎
- Refactoring for increased readablity
- Nicer CLI
- Pipy Package

# 🚀 Getting started

For an indepth Tutorial in Spanish click here... Ándale
[Spanish Tutorial ](https://joseamaita.com/blog/openopc-con-python-3/)



## Windows local installation

The quickest way to start is the cli application. Start your OPC server and use the openopc2.exe cli application for test (no python
installation required).

Now you know that your OPC server is talking to OpenOPC 2. Then lets get started with python. If you use OpenOPC 2 with
Python in windows directly you are **limited to a 32bit Python** installation. This is because the dlls of OPC are 32bit.
If you prefer working with a 64bit Python version you can simply use the With OpenOPC Gateway.

<img src="https://github.com/iterativ/openopc2/raw/develop/doc/assets/WindowsSetup.png" alt="WindowsSetup" width="400"/>

You must install the gbda_aut.dll (in /lib) which is the GrayboxOpcDa wrapper.

http://gray-box.net/daawrapper.php?lang=en

```console
python -m openopc2 list-servers
```

## Multi platform installation

One of the main benefits of OpenOPC 2 is the OpenOPC gateway. This enables you to use any modern platform for
developing your application. Start the OpenOPC service in the Windows environment where the OPC server is running.
The Service starts a server (Pyro5) that lets you use the OpenOPC2 OpcDaClient on another machine. Due to the magic of
Pyro (Python Remote Objects) the developer experience and usage of the Library remains the same as if you work in the
local Windows setup.

([Download the executables here](https://github.com/iterativ/openopc2/releases/latest))


<img src="https://github.com/iterativ/openopc2/raw/develop/doc/assets/LinuxSetup.png" alt="LinuxSetup" width="700"/>

On the Windows Machine open the console as administrator.

```shell
openopcservice install
openopcservice start
```

On your Linux machine

```shell
pip install openopc2
```

python

```python
from openopc2.da_client import OpcDaClient
```

# ⚙️ Configuration

The configuration of the OpenOpc 2 library and the OpenOpcGateway is done via environment variables.

```
OPC_CLASS=Graybox.OPC.DAWrapper
OPC_CLIENT=OpenOPC
OPC_GATE_HOST=192.168.1.96    # IMPORTANT: Replace with your IP address
OPC_GATE_PORT=7766
OPC_HOST=localhost
OPC_MODE=dcom
OPC_SERVER=Matrikon.OPC.Simulation
```

- If they are not set, open a command prompt window (`cmd`) and type:

```
C:\>set ENV_VAR=VALUE
C:\>set OPC_GATE_HOST=172.16.4.22    # this is an example
```

- Alternately, Windows OS system or user environment variables work.
  Note that user environment variables take precedent over system environment
  variables.

- Make sure the firewall is allowed to keep the port 7766 open. If in
  doubt, and you're doing a quick test, just turn off your firewall
  completely.

- For easy testing, make sure an OPC server is installed in your Windows
  box (i.e. Matrikon OPC Simulation Server).

- The work environment for testing these changes was a remote MacOs with Window10 64bit host and the Matrikon simulation
  server.

- Register the OPC automation wrapper ( `gbda_aut.dll` ) by typing this
  in the command line:

```shell
C:\openopc2\lib>regsvr32 gbda_aut.dll
```

- If, for any reason, you want to uninstall this file and remove it from
  your system registry later, type this in the command line:

```shell
C:\openopc2\lib>regsvr32 gbda_aut.dll -u
```

# CLI

The CLI (Command Line Interface) lets you use OpenOPC2 in the shell and offers you a quick way to explore your opc server
and the OpenOPC DA client without the need of writing Python code.

The documentation of the CLI can be found [here](CLI.md)

<p>
<img src="https://github.com/iterativ/openopc2/raw/develop/doc/assets/cli_server-info.png" alt="WindowsSetup" width="400"/>
</p>

<p>
<img src="https://github.com/iterativ/openopc2/raw/develop/doc/assets/cli_read.png" alt="WindowsSetup" width="400"/>
</p>

<p>
<img src="https://github.com/iterativ/openopc2/raw/develop/doc/assets/cli_write.png" alt="WindowsSetup" width="400"/>
</p>

<p>
<img src="https://github.com/iterativ/openopc2/raw/develop/doc/assets/cli_properties.png" alt="WindowsSetup" width="400"/>
</p>

# OpenOPC Gateway

This task can be completed from one of two ways (make sure to have it
installed first):

- By clicking the `Start` link on the "OpenOPC Gateway Service" from the
  "Services" window (Start -> Control Panel -> System and Security ->
  Administrative Tools).
- By running the `net start SERVICE` command like this:

```shell
C:\openopc2\bin> zzzOpenOPCService
```

- If you have problems starting the service, you can also try to start
  this in "debug" mode:

```shell
C:\openopc2\src>python OpenOPCService.py debug
```

```shell
C:\openopc2\>net stop zzzOpenOPCService
```

### Configure the way the OpenOPC Gateway Service starts

If you are going to use this service frequently, it would be better to
configure it to start in "automatic" mode. To do this:

- Select the "OpenOPC Gateway Service" from the "Services" window
  (Start -> Control Panel -> System and Security -> Administrative Tools).
- Right-click and choose "Properties".
- Change the startup mode to "Automatic". Click "Apply" and "OK"
  buttons.
- Start the service (if not already started).

## 🙏 Credits

This is a fork. The OPC UA bridge added here builds on top of **OpenOPC 2** and the
original **OpenOPC** library — all credit for the OPC DA core and Gateway goes to
their authors.

OpenOPC 2 is based on the OpenOPC python library that was originally created by Barry Barnleitner and its many Forks on
Github. Without the great work of all the contributors, this would not be possible. Contribution is open for everyone.

The authors of the package are (among others):

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

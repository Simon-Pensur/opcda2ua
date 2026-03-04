# OPC DA to OPC UA Bridge - Legacy Edition (WinXP/Server 2003)

Bridge for systems running Windows XP or Windows Server 2003 that cannot
run the modern `openopc2` bridge (which requires Python 3.8+).

## Pre-built Executable

A pre-built executable is available in `dist/opcda2ua_winxp.zip`.
Extract the zip and run:

```
opcda2ua_winxp.exe -s "YourOpcServer.Name"
```

No Python installation required on the target machine.

## Building from Source

### Option A: Build on XP via SSH (recommended)

If you have SSH access to the XP machine:

```
python winxp/build_on_xp.py
```

This script connects via SSH, transfers all dependencies offline,
installs everything, and compiles using PyInstaller 3.6 on the XP machine.
The output is `dist/opcda2ua_winxp.zip`.

### Option B: Build manually on XP

Requirements:
- Python 2.7.x (32-bit) installed in `C:\Python27`
- pywin32-221 for Python 2.7

```batch
cd C:\opcda2ua
C:\Python27\python.exe -m PyInstaller --clean --noconfirm opcda2ua_legacy.spec
```

**Note:** Must use `--onedir` mode (the spec file is configured for this).
Single-file (`--onefile`) mode has a known VC90 CRT manifest extraction bug on XP.

## Requirements (for running from source)

- Windows XP SP3 / Windows Server 2003 or later
- Python 2.7.x (32-bit)
- OpenOPC 1.3.1
- python-opcua 0.98.13
- pywin32-221
- An OPC DA server installed on the machine

## Usage

```
opcda2ua_winxp.exe -s "YourOpcServer.Name"
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `-s` / `--server` | (required) | OPC DA server name |
| `-H` / `--host` | localhost | OPC DA server host |
| `-p` / `--port` | 4840 | OPC UA server port |
| `-i` / `--interval` | 2.0 | Polling interval (seconds) |
| `-b` / `--bind` | 0.0.0.0 | OPC UA bind address |
| `-v` / `--verbose` | off | Enable debug logging |

### Examples

```
# Connect to Matrikon simulation server
opcda2ua_winxp.exe -s "Matrikon.OPC.Simulation"

# Custom port and faster polling
opcda2ua_winxp.exe -s "MyServer" -p 48400 -i 0.5

# Connect to remote OPC DA server
opcda2ua_winxp.exe -s "MyServer" -H 192.168.1.100
```

## Connecting from a client

From any machine with OPC UA support:

```
opc.tcp://<winxp-machine-ip>:4840/opcda2ua/
```

Tags appear under: Objects > OpcDaTags

## Limitations

- Read-only (no write-back to OPC DA)
- No async support (Python 2.7)
- Polling-based updates (not subscription-based on DA side)

# WinXP Legacy OPC DA to OPC UA Bridge - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a lightweight OPC DA to OPC UA bridge compatible with Windows XP / Server 2003 using Python 2.7, OpenOPC (sourceforge) and python-opcua.

**Architecture:** Single script with threading. Main thread runs the OPC UA server (python-opcua). A background thread polls OPC DA tags via OpenOPC and updates UA node values. Communication via thread-safe Queue.

**Tech Stack:** Python 2.7, OpenOPC 1.3.1, python-opcua 0.98.13, threading, argparse

---

### Task 1: Create requirements.txt

**Files:**
- Create: `winxp/requirements.txt`

**Step 1: Create the requirements file**

```text
# Python 2.7 compatible dependencies for WinXP/Server 2003
# Install with: pip install -r requirements.txt

opcua==0.98.13
enum34
trollius
futures
# OpenOPC 1.3.1 must be installed separately from:
# https://sourceforge.net/projects/openopc/files/openopc/1.3.1/
# Also requires pywin32 (included in OpenOPC installer)
```

**Step 2: Commit**

```bash
git add winxp/requirements.txt
git commit -m "feat(winxp): add requirements.txt for legacy Python 2.7 bridge"
```

---

### Task 2: Create the main bridge script - imports, constants, and argument parsing

**Files:**
- Create: `winxp/opcda2ua_legacy.py`

**Step 1: Write the script skeleton with imports and argparse**

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OPC DA to OPC UA Bridge - Legacy Edition

Compatible with Windows XP / Server 2003 using Python 2.7.
Uses OpenOPC (sourceforge) for OPC DA and python-opcua for OPC UA.

Read-only bridge: reads OPC DA tags and exposes them as OPC UA nodes.

Usage:
    python opcda2ua_legacy.py -s "Matrikon.OPC.Simulation"
    python opcda2ua_legacy.py -s "MyServer" -p 4840 -i 2
"""
from __future__ import print_function

import sys
import time
import argparse
import logging
import threading

try:
    import OpenOPC
except ImportError:
    print("ERROR: OpenOPC is required. Install from https://openopc.sourceforge.net/")
    sys.exit(1)

try:
    from opcua import Server, ua
except ImportError:
    print("ERROR: python-opcua is required. Install with: pip install opcua==0.98.13")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger("opcda2ua")

# OPC DA quality string to OPC UA StatusCode mapping
DA_QUALITY_TO_UA = {
    'Good': ua.StatusCodes.Good,
    'Uncertain': ua.StatusCodes.Uncertain,
    'Bad': ua.StatusCodes.Bad,
    'Unknown': ua.StatusCodes.Bad,
    'Error': ua.StatusCodes.Bad,
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="OPC DA to OPC UA Bridge (Legacy - Python 2.7)"
    )
    parser.add_argument(
        "-s", "--server",
        required=True,
        help="OPC DA server name (e.g. 'Matrikon.OPC.Simulation')"
    )
    parser.add_argument(
        "-H", "--host",
        default="localhost",
        help="OPC DA server host (default: localhost)"
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=4840,
        help="OPC UA server port (default: 4840)"
    )
    parser.add_argument(
        "-i", "--interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds (default: 2.0)"
    )
    parser.add_argument(
        "-b", "--bind",
        default="0.0.0.0",
        help="OPC UA server bind address (default: 0.0.0.0)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging"
    )
    return parser.parse_args()
```

**Step 2: Commit**

```bash
git add winxp/opcda2ua_legacy.py
git commit -m "feat(winxp): add script skeleton with imports and argparse"
```

---

### Task 3: Implement the LegacyBridge class - initialization and OPC DA discovery

**Files:**
- Modify: `winxp/opcda2ua_legacy.py`

**Step 1: Add the LegacyBridge class with DA connection and tag discovery**

Append after the `parse_args` function:

```python
class LegacyBridge(object):
    """
    OPC DA to OPC UA bridge for legacy systems.

    Main thread: OPC UA server (python-opcua)
    Background thread: OPC DA polling (OpenOPC)
    """

    def __init__(self, da_server, da_host="localhost", ua_port=4840,
                 ua_bind="0.0.0.0", poll_interval=2.0):
        self.da_server = da_server
        self.da_host = da_host
        self.ua_port = ua_port
        self.ua_bind = ua_bind
        self.poll_interval = poll_interval

        self.opc = None          # OpenOPC client
        self.ua_server = None    # python-opcua server
        self.ua_nodes = {}       # tag_name -> UA Node
        self.ua_idx = 0          # UA namespace index
        self._running = False
        self._lock = threading.Lock()

    def connect_da(self):
        """Connect to OPC DA server via OpenOPC"""
        log.info("Connecting to OPC DA server '%s' on %s...",
                 self.da_server, self.da_host)
        self.opc = OpenOPC.client()
        self.opc.connect(self.da_server, self.da_host)
        log.info("Connected to OPC DA server '%s'", self.da_server)

    def discover_tags(self):
        """Discover all available tags from OPC DA server"""
        log.info("Discovering tags...")
        try:
            tags = self.opc.list('*', recursive=True, flat=True)
        except Exception:
            log.warning("Recursive flat listing failed, trying simple list...")
            tags = []
            try:
                branches = self.opc.list()
                for branch in branches:
                    try:
                        items = self.opc.list(branch, flat=True)
                        tags.extend(items)
                    except Exception as e:
                        log.warning("Could not list branch '%s': %s", branch, e)
            except Exception as e:
                log.error("Could not list tags: %s", e)

        log.info("Discovered %d tags", len(tags))
        return tags

    def read_tags(self, tags):
        """Read current values for a list of tags.
        Returns list of (tag, value, quality, timestamp) tuples."""
        if not tags:
            return []
        try:
            results = self.opc.read(tags)
            # Single tag returns (value, quality, time), multi returns list of (tag, value, quality, time)
            if len(tags) == 1:
                val, qual, ts = results
                return [(tags[0], val, qual, ts)]
            return results
        except Exception as e:
            log.error("Error reading tags: %s", e)
            return []
```

**Step 2: Commit**

```bash
git add winxp/opcda2ua_legacy.py
git commit -m "feat(winxp): add LegacyBridge class with DA connection and discovery"
```

---

### Task 4: Implement OPC UA server setup and node creation

**Files:**
- Modify: `winxp/opcda2ua_legacy.py`

**Step 1: Add UA server setup and node creation methods to LegacyBridge**

Add these methods to the `LegacyBridge` class:

```python
    def setup_ua_server(self):
        """Initialize and configure the OPC UA server"""
        endpoint = "opc.tcp://%s:%d/opcda2ua/" % (self.ua_bind, self.ua_port)
        log.info("Setting up OPC UA server at %s", endpoint)

        self.ua_server = Server()
        self.ua_server.set_endpoint(endpoint)
        self.ua_server.set_server_name("OpenOPC DA-UA Bridge (Legacy)")

        uri = "http://openopc.bridge.legacy"
        self.ua_idx = self.ua_server.register_namespace(uri)

    def create_ua_nodes(self, tag_values):
        """Create OPC UA nodes from OPC DA tag readings.

        Args:
            tag_values: list of (tag, value, quality, timestamp) tuples
        """
        objects = self.ua_server.get_objects_node()
        da_folder = objects.add_folder(self.ua_idx, "OpcDaTags")

        created = 0
        for tag, value, quality, _ts in tag_values:
            try:
                if value is None:
                    value = 0

                node = da_folder.add_variable(self.ua_idx, tag, value)
                self.ua_nodes[tag] = node
                created += 1
            except Exception as e:
                log.warning("Could not create UA node for '%s': %s", tag, e)

        log.info("Created %d OPC UA nodes", created)
```

**Step 2: Commit**

```bash
git add winxp/opcda2ua_legacy.py
git commit -m "feat(winxp): add OPC UA server setup and node creation"
```

---

### Task 5: Implement the polling loop and main entry point

**Files:**
- Modify: `winxp/opcda2ua_legacy.py`

**Step 1: Add the polling loop and run method**

Add these methods to the `LegacyBridge` class:

```python
    def _poll_loop(self):
        """Background thread: polls OPC DA and updates UA nodes"""
        tag_list = list(self.ua_nodes.keys())
        log.info("Polling %d tags every %.1f seconds...", len(tag_list), self.poll_interval)

        # Read tags in batches to avoid timeouts on large tag sets
        BATCH_SIZE = 100

        while self._running:
            try:
                for i in range(0, len(tag_list), BATCH_SIZE):
                    if not self._running:
                        break
                    batch = tag_list[i:i + BATCH_SIZE]
                    results = self.read_tags(batch)

                    with self._lock:
                        for item in results:
                            tag, value, quality, _ts = item
                            if tag in self.ua_nodes and value is not None:
                                try:
                                    status = DA_QUALITY_TO_UA.get(quality, ua.StatusCodes.Bad)
                                    dv = ua.DataValue(ua.Variant(value))
                                    dv.StatusCode = ua.StatusCode(status)
                                    self.ua_nodes[tag].set_data_value(dv)
                                except Exception as e:
                                    log.debug("Could not update '%s': %s", tag, e)
            except Exception as e:
                log.error("Poll error: %s", e)

            time.sleep(self.poll_interval)

    def run(self):
        """Start the bridge: connect DA, setup UA, poll forever"""
        # 1. Connect to OPC DA
        self.connect_da()

        # 2. Discover tags
        tags = self.discover_tags()
        if not tags:
            log.error("No tags found. Check the OPC DA server.")
            return

        # 3. Read initial values
        log.info("Reading initial values for %d tags...", len(tags))
        tag_values = self.read_tags(tags)
        if not tag_values:
            log.error("Could not read any tag values.")
            return
        log.info("Read %d tag values successfully", len(tag_values))

        # 4. Setup OPC UA server and create nodes
        self.setup_ua_server()
        self.create_ua_nodes(tag_values)

        # 5. Start OPC UA server
        self.ua_server.start()
        endpoint = "opc.tcp://%s:%d/opcda2ua/" % (self.ua_bind, self.ua_port)
        log.info("=" * 60)
        log.info("OPC UA server running at %s", endpoint)
        log.info("%d tags available", len(self.ua_nodes))
        log.info("Press Ctrl+C to stop")
        log.info("=" * 60)

        # 6. Start polling in background thread
        self._running = True
        poll_thread = threading.Thread(target=self._poll_loop)
        poll_thread.daemon = True
        poll_thread.start()

        # 7. Keep main thread alive
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            self.stop()

    def stop(self):
        """Stop the bridge gracefully"""
        self._running = False
        if self.ua_server:
            try:
                self.ua_server.stop()
                log.info("OPC UA server stopped")
            except Exception:
                pass
        if self.opc:
            try:
                self.opc.close()
                log.info("OPC DA connection closed")
            except Exception:
                pass
```

**Step 2: Add the main entry point at the bottom of the file**

```python
def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    bridge = LegacyBridge(
        da_server=args.server,
        da_host=args.host,
        ua_port=args.port,
        ua_bind=args.bind,
        poll_interval=args.interval,
    )

    try:
        bridge.run()
    except Exception as e:
        log.error("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Step 3: Commit**

```bash
git add winxp/opcda2ua_legacy.py
git commit -m "feat(winxp): add polling loop and main entry point"
```

---

### Task 6: Create README for the winxp directory

**Files:**
- Create: `winxp/README.md`

**Step 1: Write the README with installation and usage instructions**

```markdown
# OPC DA to OPC UA Bridge - Legacy Edition (WinXP/Server 2003)

Bridge for systems running Windows XP or Windows Server 2003 that cannot
run the modern `openopc2` bridge (which requires Python 3.8+).

## Requirements

- Windows XP SP3 / Windows Server 2003 or later
- Python 2.7.x (32-bit) - https://www.python.org/downloads/release/python-2717/
- OpenOPC 1.3.1 - https://sourceforge.net/projects/openopc/files/openopc/1.3.1/
- An OPC DA server installed on the machine

## Installation

1. Install Python 2.7.17 (32-bit)
2. Install OpenOPC 1.3.1 (use the .exe installer for Python 2.7)
3. Install python-opcua:

```
pip install -r requirements.txt
```

## Usage

```
python opcda2ua_legacy.py -s "YourOpcServer.Name"
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
python opcda2ua_legacy.py -s "Matrikon.OPC.Simulation"

# Custom port and faster polling
python opcda2ua_legacy.py -s "MyServer" -p 48400 -i 0.5

# Connect to remote OPC DA server
python opcda2ua_legacy.py -s "MyServer" -H 192.168.1.100

# Verbose logging for troubleshooting
python opcda2ua_legacy.py -s "MyServer" -v
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
```

**Step 2: Commit**

```bash
git add winxp/README.md
git commit -m "docs(winxp): add README with installation and usage instructions"
```

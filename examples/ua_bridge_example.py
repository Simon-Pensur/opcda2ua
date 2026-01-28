"""
Example: OPC DA to OPC UA Bridge Server

This script starts an OPC UA server that exposes all variables
from an OPC DA server. Remote clients can connect using any
standard OPC UA client.

Requirements:
    - Windows with OPC DA server installed (e.g., Matrikon OPC Simulation)
    - asyncua package: pip install asyncua

Usage:
    python ua_bridge_example.py

From remote client (Linux/Mac/Windows):
    from asyncua import Client

    async def main():
        client = Client("opc.tcp://WINDOWS_IP:4840/openopc2/")
        await client.connect()
        # ... use client ...

Or use any OPC UA client like UaExpert, Prosys OPC UA Browser, etc.
"""

import asyncio
import sys

try:
    from openopc2.ua_server import OpcDaUaBridge
    from openopc2.config import OpenOpcConfig
except ImportError as e:
    print(f"Import error: {e}")
    print("\nMake sure openopc2 is installed and asyncua is available:")
    print("  pip install openopc2[ua]")
    print("  # or")
    print("  pip install asyncua")
    sys.exit(1)


def main():
    # Configure - change these values for your setup
    OPC_DA_SERVER = "Matrikon.OPC.Simulation"  # Your OPC DA server name
    OPC_DA_HOST = "localhost"                   # OPC DA host (usually localhost)
    UA_ENDPOINT = "opc.tcp://0.0.0.0:4840/openopc2/"  # OPC UA endpoint

    # Create configuration
    config = OpenOpcConfig()
    config.OPC_SERVER = OPC_DA_SERVER
    config.OPC_HOST = OPC_DA_HOST

    # Create bridge
    bridge = OpcDaUaBridge(config)
    bridge.set_endpoint(UA_ENDPOINT)

    print("=" * 60)
    print("  OPC DA to OPC UA Bridge")
    print("=" * 60)
    print(f"  OPC DA Server: {OPC_DA_SERVER}")
    print(f"  OPC DA Host:   {OPC_DA_HOST}")
    print(f"  OPC UA Endpoint: {UA_ENDPOINT}")
    print("=" * 60)
    print("\nStarting bridge...")
    print("Remote clients can connect to: opc.tcp://<YOUR_IP>:4840/openopc2/")
    print("Press Ctrl+C to stop\n")

    try:
        asyncio.run(bridge.start())
    except KeyboardInterrupt:
        bridge.stop()
        print("\nBridge stopped.")
    except Exception as e:
        print(f"\nError: {e}")
        print("\nTroubleshooting:")
        print("  1. Make sure the OPC DA server is running")
        print("  2. Check if the server name is correct")
        print("  3. Verify you have admin rights if needed")
        sys.exit(1)


if __name__ == "__main__":
    main()

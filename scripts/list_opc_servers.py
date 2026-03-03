"""
List available OPC DA servers
"""
import sys
sys.path.insert(0, r'C:\opcda2ua')

from openopc2.da_client import OpcDaClient
from openopc2.config import OpenOpcConfig

def main():
    config = OpenOpcConfig()
    client = OpcDaClient(config)

    print("Listing available OPC DA servers on localhost...")
    try:
        servers = client.servers()
        print(f"\nFound {len(servers)} servers:")
        for server in servers:
            print(f"  - {server}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()

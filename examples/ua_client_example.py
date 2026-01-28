"""
Example: Remote OPC UA Client

This script demonstrates how to connect to the OPC DA-UA bridge
from any operating system using the asyncua library.

Requirements:
    - asyncua package: pip install asyncua

Usage:
    python ua_client_example.py --server opc.tcp://192.168.1.100:4840/openopc2/

This client can run on Linux, macOS, or Windows.
"""

import asyncio
import argparse
import sys

try:
    from asyncua import Client
except ImportError:
    print("asyncua is required. Install with: pip install asyncua")
    sys.exit(1)


class SubscriptionHandler:
    """Handler for data change notifications"""

    def datachange_notification(self, node, val, data):
        """Called when a subscribed node value changes"""
        print(f"  Data change: {node.nodeid.Identifier} = {val}")


async def browse_nodes(client, show_all=False):
    """Browse and display available nodes"""
    root = client.nodes.root
    objects = await root.get_child(["0:Objects"])

    # Find OpcDaTags folder
    children = await objects.get_children()

    opc_da_folder = None
    for child in children:
        name = await child.read_browse_name()
        if name.Name == "OpcDaTags":
            opc_da_folder = child
            break

    if opc_da_folder:
        tags = await opc_da_folder.get_children()
        print(f"\nAvailable tags ({len(tags)}):")

        display_count = len(tags) if show_all else min(10, len(tags))
        for tag in tags[:display_count]:
            name = await tag.read_browse_name()
            try:
                value = await tag.read_value()
                print(f"  - {name.Name}: {value}")
            except Exception as e:
                print(f"  - {name.Name}: <error reading value>")

        if len(tags) > display_count:
            print(f"  ... and {len(tags) - display_count} more")

        return tags
    else:
        print("OpcDaTags folder not found. Showing root objects:")
        for child in children:
            name = await child.read_browse_name()
            print(f"  - {name.Name}")
        return children


async def subscribe_to_nodes(client, nodes, duration=30):
    """Subscribe to data changes on specified nodes"""
    print(f"\nSubscribing to {len(nodes)} nodes for {duration} seconds...")

    handler = SubscriptionHandler()
    subscription = await client.create_subscription(500, handler)  # 500ms update rate

    await subscription.subscribe_data_change(nodes)

    print("Waiting for data changes...\n")
    await asyncio.sleep(duration)

    await subscription.delete()
    print("\nSubscription ended.")


async def main(server_url: str, subscribe: bool = False, subscribe_duration: int = 30):
    """Main client function"""
    print(f"Connecting to {server_url}...")

    client = Client(server_url)

    try:
        await client.connect()
        print("Connected!")

        # Browse available nodes
        nodes = await browse_nodes(client)

        # Subscribe if requested
        if subscribe and nodes:
            # Subscribe to first 5 nodes
            nodes_to_subscribe = nodes[:5]
            await subscribe_to_nodes(client, nodes_to_subscribe, subscribe_duration)

    except Exception as e:
        print(f"\nConnection error: {e}")
        print("\nTroubleshooting:")
        print("  1. Check if the bridge server is running")
        print("  2. Verify the server URL is correct")
        print("  3. Check firewall settings (port 4840)")
        return

    finally:
        await client.disconnect()
        print("Disconnected.")


def run():
    """Entry point"""
    parser = argparse.ArgumentParser(
        description='OPC UA Client Example - Connect to OPC DA-UA Bridge'
    )
    parser.add_argument(
        '--server',
        default='opc.tcp://localhost:4840/openopc2/',
        help='OPC UA server URL (default: opc.tcp://localhost:4840/openopc2/)'
    )
    parser.add_argument(
        '--subscribe',
        action='store_true',
        help='Subscribe to data changes'
    )
    parser.add_argument(
        '--duration',
        type=int,
        default=30,
        help='Subscription duration in seconds (default: 30)'
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  OPC UA Client Example")
    print("=" * 60)

    asyncio.run(main(args.server, args.subscribe, args.duration))


if __name__ == "__main__":
    run()

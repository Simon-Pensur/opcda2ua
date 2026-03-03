"""
Minimal OPC UA client to test connection to the bridge
"""
import asyncio
from asyncua import Client


async def main():
    # Connect to the OPC UA server on the VM
    url = "opc.tcp://localhost:4840/openopc2/"

    print(f"Connecting to {url}...")

    async with Client(url=url) as client:
        print("Connected!")

        # Get the root node
        root = client.nodes.root
        print(f"Root node: {root}")

        # Browse to find OpcDaTags folder
        objects = client.nodes.objects
        print(f"Objects node: {objects}")

        # List children of objects
        children = await objects.get_children()
        print(f"\nChildren of Objects node:")
        for child in children:
            name = await child.read_browse_name()
            print(f"  - {name}")

        # Try to find OpcDaTags folder
        for child in children:
            name = await child.read_browse_name()
            if "OpcDaTags" in str(name):
                print(f"\nFound OpcDaTags folder: {child}")
                tags = await child.get_children()
                print(f"Number of tags: {len(tags)}")

                # Read first 5 tags
                print("\nFirst 5 tags:")
                for tag in tags[:5]:
                    tag_name = await tag.read_browse_name()
                    try:
                        value = await tag.read_value()
                        print(f"  {tag_name}: {value}")
                    except Exception as e:
                        print(f"  {tag_name}: Error reading - {e}")
                break


if __name__ == "__main__":
    asyncio.run(main())

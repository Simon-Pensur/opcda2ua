"""Quick UA client probe: connect, walk OpcDaTags folder, sample known F_CV values
   (with description), then watch one tag for live updates from the DA subscription."""
import asyncio
from asyncua import Client, ua


async def main():
    url = "opc.tcp://localhost:4840/fix/"
    print(f"Connecting to {url}")
    async with Client(url=url, timeout=15) as client:
        ns_idx = await client.get_namespace_index("http://openopc2.bridge")
        print(f"Namespace index: {ns_idx}")

        objects = client.nodes.objects
        folder = await objects.get_child([f"{ns_idx}:OpcDaTags"])
        children = await folder.get_children()
        print(f"OpcDaTags folder has {len(children)} child nodes")

        # First 5 random tags by browse, with descriptions
        print("\nFirst 5 tags via browse:")
        for tag_node in children[:5]:
            try:
                bn = await tag_node.read_browse_name()
                dv = await tag_node.read_data_value()
                desc_dv = await tag_node.read_attribute(ua.AttributeIds.Description)
                desc = desc_dv.Value.Value.Text if desc_dv.Value.Value else ""
                print(f"  {bn.Name}")
                print(f"    Value={dv.Value.Value!r} ({dv.Value.VariantType.name})  Status={dv.StatusCode_.name}")
                if desc:
                    print(f"    Desc={desc!r}")
            except Exception as e:
                print(f"  ERROR: {e}")

        # Sample known F_CV tags
        targets = [
            "ALUR1.010_FIT_010_PV.F_CV",
            "ALUR1.010_TT_901_PV.F_CV",
        ]
        print("\nSampling targeted tags:")
        for tag in targets:
            try:
                node = await folder.get_child([f"{ns_idx}:{tag}"])
                dv = await node.read_data_value()
                desc_dv = await node.read_attribute(ua.AttributeIds.Description)
                desc = desc_dv.Value.Value.Text if desc_dv.Value.Value else ""
                print(f"  {tag}")
                print(f"    Value={dv.Value.Value!r} ({dv.Value.VariantType.name})  Status={dv.StatusCode_.name}")
                print(f"    Desc={desc!r}")
            except Exception as e:
                print(f"  {tag} -> ERROR: {e}")

        # Watch a tag for 8 seconds — should transition from Null/Bad to a Good Double
        watch = "ALUR1.010_FIT_010_PV.F_CV"
        print(f"\nWatching {watch} for 8 seconds:")
        node = await folder.get_child([f"{ns_idx}:{watch}"])
        for i in range(8):
            dv = await node.read_data_value()
            print(f"  t={i}s  value={dv.Value.Value!r}  status={dv.StatusCode_.name}")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())

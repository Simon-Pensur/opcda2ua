"""Quick diagnostic: SyncRead path tests."""
import sys, time
import pythoncom
pythoncom.CoInitialize()

from openopc2.config import OpenOpcConfig
from openopc2.da_client import OpcDaClient

config = OpenOpcConfig()
client = OpcDaClient(config)
client.connect("Intellution.OPCiFIX.1", "localhost")
print("Connected")

browser = client._opc.opc_client.CreateBrowser()
browser.MoveToRoot()
browser.MoveDown("ALUR1")
browser.MoveDown("AI")
browser.ShowBranches()
sample_tag_name = browser.Item(1)
fcv_tag = f"ALUR1.{sample_tag_name}.F_CV"
desc_tag = f"ALUR1.{sample_tag_name}.A_DESC"
print(f"Sample tag: {sample_tag_name}")
print(f"  F_CV item: {fcv_tag}")

# Test 1: Read F_CV with sync=True (avoid callback path)
print("\n[1] read sync=True F_CV ...")
try:
    r = client.read([fcv_tag], sync=True)
    print(f"    {r}")
except Exception as e:
    print(f"    ERROR: {e}")

print("\n[2] read sync=True A_DESC ...")
try:
    r = client.read([desc_tag], sync=True)
    print(f"    {r}")
except Exception as e:
    print(f"    ERROR: {e}")

print("\n[3] read sync=True batch 100 A_DESC ...")
browser.MoveToRoot(); browser.MoveDown("ALUR1"); browser.MoveDown("AI"); browser.ShowBranches()
batch = []
for i in range(1, 101):
    try:
        n = browser.Item(i)
        batch.append(f"ALUR1.{n}.A_DESC")
    except: pass
print(f"    {len(batch)} tags")
try:
    start = time.time()
    r = client.read(batch, sync=True)
    el = time.time() - start
    found = sum(1 for row in r if row[2] in ('Good','Uncertain') and row[1])
    print(f"    {el*1000:.0f}ms ({len(r)/el:.0f} tags/s, {found} encontradas)")
    for row in r[:3]:
        print(f"    sample: {row}")
except Exception as e:
    print(f"    ERROR: {e}")
    import traceback; traceback.print_exc()

print("\n[4] read sync=True batch 1000 A_DESC across multiple subnodes...")
all_descs = []
for sn in ("ALUR1","CALD","DEST","GEN","TRP"):
    try:
        browser.MoveToRoot(); browser.MoveDown(sn); browser.MoveDown("AI"); browser.ShowBranches()
        for i in range(1, min(browser.Count + 1, 201)):
            try:
                n = browser.Item(i)
                all_descs.append(f"{sn}.{n}.A_DESC")
            except: pass
    except Exception as e:
        print(f"    skip {sn}: {e}")
print(f"    {len(all_descs)} tags")
try:
    start = time.time()
    r = client.read(all_descs, sync=True)
    el = time.time() - start
    found = sum(1 for row in r if row[2] in ('Good','Uncertain') and row[1])
    print(f"    {el*1000:.0f}ms ({len(r)/el:.0f} tags/s, {found} encontradas)")
except Exception as e:
    print(f"    ERROR: {e}")

client.close()
print("\nDone.")

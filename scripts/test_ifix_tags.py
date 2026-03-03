"""
Test script to diagnose iFIX OPC DA connection
"""
import sys
sys.path.insert(0, r'C:\opcda2ua')

from openopc2.da_client import OpcDaClient
from openopc2.config import OpenOpcConfig

def main():
    config = OpenOpcConfig()
    client = OpcDaClient(config)

    print("Connecting to Intellution.OPCiFIX.1...")
    client.connect("Intellution.OPCiFIX.1", "localhost")
    print("Connected!")

    print("\n--- Test 1: list(flat=True, recursive=True) ---")
    try:
        tags = client.list(flat=True, recursive=True)
        print(f"Found {len(tags)} tags")
        if tags:
            for t in tags[:10]:
                print(f"  '{t}'")
    except Exception as e:
        print(f"Error: {e}")

    print("\n--- Test 2: list(flat=True, recursive=False) ---")
    try:
        tags = client.list(flat=True, recursive=False)
        print(f"Found {len(tags)} tags")
        if tags:
            for t in tags[:10]:
                print(f"  '{t}'")
    except Exception as e:
        print(f"Error: {e}")

    print("\n--- Test 3: list(flat=False, recursive=True) ---")
    try:
        tags = client.list(flat=False, recursive=True)
        print(f"Found {len(tags)} items")
        if tags:
            for t in tags[:10]:
                print(f"  {t}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n--- Test 4: list() default ---")
    try:
        tags = client.list()
        print(f"Found {len(tags)} items")
        if tags:
            for t in tags[:10]:
                print(f"  {t}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n--- Test 5: list with path='*' ---")
    try:
        tags = client.list('*')
        print(f"Found {len(tags)} items")
        if tags:
            for t in tags[:10]:
                print(f"  {t}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n--- Test 6: Try reading a known tag (FIX.PDB1.F_CV) ---")
    try:
        result = client.read(['FIX.PDB1.F_CV'])
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n--- Test 7: Try reading with node format ---")
    try:
        result = client.read(['FIX32.MYNODE.PDB1.F_CV'])
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")

    client.close()
    print("\nDone.")


if __name__ == "__main__":
    main()

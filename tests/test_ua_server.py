"""
Tests for OPC DA to OPC UA bridge server.

These tests require:
1. An OPC DA server (e.g., Matrikon OPC Simulation) running locally
2. asyncua package installed: pip install asyncua
"""

import unittest
import asyncio
import threading
import time


class TestUaServerImport(unittest.TestCase):
    """Test UA server module imports"""

    def test_import_with_asyncua(self):
        """Module imports correctly when asyncua is available"""
        try:
            from openopc2.ua_server import OpcDaUaBridge
            self.assertTrue(True)
        except ImportError as e:
            if "asyncua" in str(e):
                self.skipTest("asyncua not installed")
            raise

    def test_bridge_class_exists(self):
        """OpcDaUaBridge class exists"""
        try:
            from openopc2.ua_server import OpcDaUaBridge
            self.assertTrue(hasattr(OpcDaUaBridge, 'start'))
            self.assertTrue(hasattr(OpcDaUaBridge, 'stop'))
            self.assertTrue(hasattr(OpcDaUaBridge, 'set_endpoint'))
        except ImportError:
            self.skipTest("asyncua not installed")


class TestOpcDaUaBridge(unittest.TestCase):
    """Integration tests for the OPC DA to OPC UA bridge"""

    @classmethod
    def setUpClass(cls):
        """Check if requirements are available"""
        cls.asyncua_available = False
        cls.opc_da_available = False

        try:
            import asyncua
            cls.asyncua_available = True
        except ImportError:
            pass

        try:
            from openopc2.da_client import OpcDaClient
            from openopc2.config import OpenOpcConfig

            config = OpenOpcConfig()
            # Use Matrikon OPC class if Graybox is not available
            if config.OPC_CLASS == 'Graybox.OPC.DAWrapper':
                config.OPC_CLASS = 'Matrikon.OPC.Automation.1'
            if not config.OPC_SERVER:
                config.OPC_SERVER = 'Matrikon.OPC.Simulation.1'

            client = OpcDaClient(config)
            client.connect(config.OPC_SERVER)
            client.close()
            cls.opc_da_available = True
        except:
            pass

    def test_bridge_initialization(self):
        """Bridge initializes correctly"""
        if not self.asyncua_available:
            self.skipTest("asyncua not installed")

        from openopc2.ua_server import OpcDaUaBridge
        from openopc2.config import OpenOpcConfig

        config = OpenOpcConfig()
        bridge = OpcDaUaBridge(config)

        self.assertIsNone(bridge.ua_server)
        self.assertEqual(bridge.ua_nodes, {})
        self.assertFalse(bridge._running)

    def test_set_endpoint(self):
        """Bridge endpoint can be configured"""
        if not self.asyncua_available:
            self.skipTest("asyncua not installed")

        from openopc2.ua_server import OpcDaUaBridge

        bridge = OpcDaUaBridge()
        custom_endpoint = "opc.tcp://127.0.0.1:5000/custom/"
        bridge.set_endpoint(custom_endpoint)

        self.assertEqual(bridge._endpoint, custom_endpoint)

    def test_set_server_name(self):
        """Bridge server name can be configured"""
        if not self.asyncua_available:
            self.skipTest("asyncua not installed")

        from openopc2.ua_server import OpcDaUaBridge

        bridge = OpcDaUaBridge()
        bridge.set_server_name("Custom Bridge")

        self.assertEqual(bridge._server_name, "Custom Bridge")

    def test_bridge_requires_opc_server(self):
        """Bridge start raises error without OPC server name"""
        if not self.asyncua_available:
            self.skipTest("asyncua not installed")

        from openopc2.ua_server import OpcDaUaBridge
        from openopc2.config import OpenOpcConfig

        config = OpenOpcConfig()
        config.OPC_SERVER = None
        bridge = OpcDaUaBridge(config)

        async def test_start():
            with self.assertRaises(ValueError):
                await bridge.start(opc_server=None)

        asyncio.run(test_start())

    def test_bridge_starts_and_stops(self):
        """Bridge starts and stops correctly (integration test)"""
        if not self.asyncua_available:
            self.skipTest("asyncua not installed")
        if not self.opc_da_available:
            self.skipTest("OPC DA server not available")

        from openopc2.ua_server import OpcDaUaBridge
        from openopc2.config import OpenOpcConfig

        config = OpenOpcConfig()
        bridge = OpcDaUaBridge(config)

        # Run bridge in background thread
        started = threading.Event()
        error = [None]

        def run_bridge():
            try:
                async def start_and_signal():
                    await bridge._setup_ua_server()
                    started.set()
                    # Don't actually run the full bridge, just verify setup
                    bridge.stop()

                asyncio.run(start_and_signal())
            except Exception as e:
                error[0] = e
                started.set()

        thread = threading.Thread(target=run_bridge, daemon=True)
        thread.start()

        # Wait for bridge to start
        started.wait(timeout=10)
        bridge.stop()
        thread.join(timeout=5)

        if error[0]:
            self.fail(f"Bridge failed to start: {error[0]}")


class TestUaServerMain(unittest.TestCase):
    """Test the main() function"""

    def test_main_function_exists(self):
        """main() function exists in module"""
        try:
            from openopc2.ua_server import main
            self.assertTrue(callable(main))
        except ImportError:
            self.skipTest("asyncua not installed")


if __name__ == '__main__':
    unittest.main()

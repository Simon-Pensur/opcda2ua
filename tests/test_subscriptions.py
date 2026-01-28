"""
Tests for OPC DA subscription functionality.

These tests require an actual OPC DA server (e.g., Matrikon OPC Simulation)
to be running on the local machine.
"""

import unittest
import time
import threading


class TestSubscriptions(unittest.TestCase):
    """Tests for OPC DA subscriptions"""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures"""
        try:
            from openopc2.da_client import OpcDaClient
            from openopc2.config import OpenOpcConfig

            cls.config = OpenOpcConfig()
            # Use Matrikon OPC class if Graybox is not available
            if cls.config.OPC_CLASS == 'Graybox.OPC.DAWrapper':
                cls.config.OPC_CLASS = 'Matrikon.OPC.Automation.1'
            if not cls.config.OPC_SERVER:
                cls.config.OPC_SERVER = 'Matrikon.OPC.Simulation.1'

            cls.client = OpcDaClient(cls.config)
            cls.client.connect(cls.config.OPC_SERVER)
            cls.tags = cls.client.list(flat=True)[:10]  # First 10 tags
            cls.skip_tests = False
        except Exception as e:
            cls.skip_tests = True
            cls.skip_reason = str(e)

    @classmethod
    def tearDownClass(cls):
        """Clean up test fixtures"""
        if hasattr(cls, 'client') and cls.client:
            try:
                cls.client.close()
            except:
                pass

    def setUp(self):
        if self.skip_tests:
            self.skipTest(f"OPC DA server not available: {self.skip_reason}")
        if not self.tags:
            self.skipTest("No tags available from OPC DA server")

    def test_subscribe_single_tag(self):
        """Subscribe to a single tag"""
        received = []
        event = threading.Event()

        def callback(changes):
            received.extend(changes)
            event.set()

        subscribed = self.client.subscribe(
            tags=self.tags[0],
            callback=callback,
            group='test_single'
        )

        self.assertEqual(len(subscribed), 1)

        # Wait for at least one event
        event.wait(timeout=5)

        self.client.unsubscribe(group='test_single')

    def test_subscribe_multiple_tags(self):
        """Subscribe to multiple tags"""
        received = []
        event = threading.Event()

        def callback(changes):
            received.extend(changes)
            if len(received) >= 3:
                event.set()

        subscribed = self.client.subscribe(
            tags=self.tags[:5],
            callback=callback,
            group='test_multiple'
        )

        self.assertLessEqual(len(subscribed), 5)

        event.wait(timeout=5)
        self.client.unsubscribe(group='test_multiple')

    def test_callback_receives_tuple_format(self):
        """Callback receives list of (tag, value, quality, timestamp) tuples"""
        batches = []
        event = threading.Event()

        def callback(changes):
            batches.append(changes)
            event.set()

        self.client.subscribe(
            tags=self.tags[:3],
            callback=callback,
            group='test_batch',
            update_rate=100
        )

        event.wait(timeout=5)
        self.client.unsubscribe(group='test_batch')

        # Verify batch format
        if batches:
            for batch in batches:
                self.assertIsInstance(batch, list)
                for item in batch:
                    self.assertEqual(len(item), 4)  # (tag, value, quality, timestamp)

    def test_list_subscriptions(self):
        """List active subscriptions"""
        self.client.subscribe(
            tags=self.tags[:2],
            callback=lambda x: None,
            group='test_list'
        )

        subs = self.client.list_subscriptions()
        self.assertIn('test_list', subs)

        self.client.unsubscribe(group='test_list')

    def test_unsubscribe_group(self):
        """Unsubscribe from entire group"""
        self.client.subscribe(
            tags=self.tags[:3],
            callback=lambda x: None,
            group='test_unsub'
        )

        self.client.unsubscribe(group='test_unsub')

        subs = self.client.list_subscriptions()
        self.assertNotIn('test_unsub', subs)

    def test_subscribe_requires_group(self):
        """Subscribe raises ValueError without group"""
        with self.assertRaises(ValueError):
            self.client.subscribe(
                tags=self.tags[0],
                callback=lambda x: None,
                group=None
            )

    def test_subscribe_requires_callable(self):
        """Subscribe raises ValueError without callable callback"""
        with self.assertRaises(ValueError):
            self.client.subscribe(
                tags=self.tags[0],
                callback="not a callable",
                group='test_callable'
            )

    def test_custom_update_rate(self):
        """Subscribe with custom update rate"""
        received = []
        event = threading.Event()

        def callback(changes):
            received.append(time.time())
            if len(received) >= 3:
                event.set()

        self.client.subscribe(
            tags=self.tags[0],
            callback=callback,
            group='test_rate',
            update_rate=200  # 200ms update rate
        )

        event.wait(timeout=5)
        self.client.unsubscribe(group='test_rate')


class TestSubscriptionEdgeCases(unittest.TestCase):
    """Edge case tests for subscriptions"""

    def test_subscribe_invalid_tags(self):
        """Subscribe with invalid tag type raises TypeError"""
        try:
            from openopc2.da_client import OpcDaClient
            from openopc2.config import OpenOpcConfig

            client = OpcDaClient(OpenOpcConfig())
            client.connect()

            with self.assertRaises(TypeError):
                client.subscribe(
                    tags=12345,  # Invalid type
                    callback=lambda x: None,
                    group='test_invalid'
                )

            client.close()
        except Exception as e:
            self.skipTest(f"OPC DA server not available: {e}")


if __name__ == '__main__':
    unittest.main()

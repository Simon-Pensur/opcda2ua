"""
OPC DA to OPC UA Bridge Server

Exposes all variables from an OPC DA server as an OPC UA server,
allowing remote clients (Linux/Mac/Windows) to access OPC DA data
without requiring Pyro or DCOM.
"""

import asyncio
import threading
from queue import Queue, Empty
from typing import Dict, List, Optional
import logging

try:
    from asyncua import Server, ua
except ImportError:
    raise ImportError(
        "asyncua is required for OPC UA bridge. "
        "Install with: pip install openopc2[ua] or pip install asyncua"
    )

from openopc2.da_client import OpcDaClient
from openopc2.config import OpenOpcConfig
from openopc2.logger import log


class WriteHandler:
    """Handles write events from OPC UA clients"""

    def __init__(self, bridge: 'OpcDaUaBridge'):
        self.bridge = bridge
        self._initialized_nodes: set = set()  # Nodes that have received initial value

    def datachange_notification(self, node, val, data):
        """Called when a node value changes"""
        node_id = node.nodeid.to_string()
        if node_id in self.bridge.ua_node_to_tag:
            tag = self.bridge.ua_node_to_tag[node_id]

            # Skip the first notification (initial value when subscription starts)
            if node_id not in self._initialized_nodes:
                self._initialized_nodes.add(node_id)
                return

            # Check if this value matches what DA sent (if so, it's from DA, not client)
            last_da_value = self.bridge._last_da_values.get(tag)
            if last_da_value is not None:
                # Compare values (handle different types)
                try:
                    if val == last_da_value:
                        return  # This is from DA, not a client write
                except:
                    pass  # If comparison fails, treat as client write

            # This is a client write - forward to DA
            log.info(f"Client write detected: {tag} = {val}")
            self.bridge._write_queue.put((tag, val))


class OpcDaUaBridge:
    """
    Bridge connecting OPC DA (local COM) to OPC UA (TCP network).

    Architecture:
    - Main thread: asyncio event loop for OPC UA server
    - Separate thread: OPC DA client with COM/pythoncom
    - Communication: Thread-safe Queue for subscription events
    """

    def __init__(self, config: OpenOpcConfig = None):
        self.config = config or OpenOpcConfig()

        # OPC UA server
        self.ua_server: Optional[Server] = None
        self.ua_nodes: Dict[str, any] = {}  # tag_name -> UA Node
        self.ua_node_to_tag: Dict[str, str] = {}  # node_id -> tag_name (reverse mapping)
        self.ua_namespace_idx: int = 0

        # OPC DA client (runs in separate thread)
        self.da_client: Optional[OpcDaClient] = None
        self.da_thread: Optional[threading.Thread] = None
        self.da_thread_active: bool = False

        # Inter-thread communication (separate queues to avoid race conditions)
        self._da_to_main_queue: Queue = Queue()  # DA thread -> Main thread (data changes)
        self._main_to_da_queue: Queue = Queue()  # Main thread -> DA thread (commands)
        self._write_queue: Queue = Queue()  # UA writes to be sent to DA

        # State
        self._running = False
        self._endpoint = "opc.tcp://0.0.0.0:4840/openopc2/"
        self._server_name = "OpenOPC2 DA-UA Bridge"
        self._last_da_values: Dict[str, any] = {}  # Last value received from DA for each tag

    def set_endpoint(self, endpoint: str):
        """Configure OPC UA server endpoint"""
        self._endpoint = endpoint

    def set_server_name(self, name: str):
        """Configure OPC UA server name"""
        self._server_name = name

    async def _setup_ua_server(self):
        """Configure the OPC UA server"""
        self.ua_server = Server()
        await self.ua_server.init()

        self.ua_server.set_endpoint(self._endpoint)
        self.ua_server.set_server_name(self._server_name)

        # Configure namespace
        uri = "http://openopc2.bridge"
        self.ua_namespace_idx = await self.ua_server.register_namespace(uri)

        log.info(f"OPC UA Server configured at {self._endpoint}")

    async def _create_ua_nodes(self, tag_values: List[tuple]):
        """Create OPC UA nodes for each OPC DA tag with initial values

        Args:
            tag_values: List of (tag, value, quality, timestamp) tuples
        """
        objects = self.ua_server.nodes.objects

        # Create a folder for OPC DA tags
        da_folder = await objects.add_folder(self.ua_namespace_idx, "OpcDaTags")

        for tag, value, quality, timestamp in tag_values:
            try:
                # Use a default value based on type if the value is None
                if value is None:
                    value = 0  # Default to 0 for None values

                # Create variable with actual initial value (asyncua infers type)
                node = await da_folder.add_variable(
                    self.ua_namespace_idx,
                    tag,
                    value
                )
                await node.set_writable()
                self.ua_nodes[tag] = node
                # Store reverse mapping for write handling
                node_id = node.nodeid.to_string()
                self.ua_node_to_tag[node_id] = tag
            except Exception as e:
                log.warning(f"Could not create UA node for '{tag}': {e}")

        log.info(f"Created {len(self.ua_nodes)} OPC UA nodes")

    def _make_write_handler(self, tag: str):
        """Create a write handler for a specific tag"""
        def handler(node, value, data):
            # Queue write to OPC DA
            log.info(f"UA write received: {tag} = {value}")
            self._write_queue.put((tag, value))
            return ua.StatusCode(ua.StatusCodes.Good)
        return handler

    def _da_thread_main(self, opc_server: str, opc_host: str):
        """Main thread for OPC DA (COM requires its own thread)"""
        import pythoncom
        pythoncom.CoInitialize()

        try:
            # Create and connect OPC DA client
            self.da_client = OpcDaClient(self.config)
            self.da_client.connect(opc_server, opc_host)
            log.info(f"Connected to OPC DA server: {opc_server} on {opc_host}")

            # Get list of tags (filter out system/health tags that can't be mixed with regular tags)
            all_tags = self.da_client.list(flat=True, recursive=True)
            tags = [t for t in all_tags if not t.startswith('#') and not t.startswith('@')]
            log.info(f"Discovered {len(all_tags)} tags, {len(tags)} usable (filtered system tags)")

            # Read initial values for all tags
            log.info("Reading initial values from OPC DA...")
            tag_values = self.da_client.read(tags)
            log.info(f"Read {len(tag_values)} initial values")

            # Notify main thread of discovered tags with values (use DA->Main queue)
            self._da_to_main_queue.put(('tags_discovered', tag_values))

            # Wait for UA server to be ready (use Main->DA queue)
            while self.da_thread_active:
                try:
                    event = self._main_to_da_queue.get(timeout=0.1)
                    if event == 'ua_ready':
                        break
                except Empty:
                    continue

            if not self.da_thread_active:
                return

            # Subscribe to all tags
            def on_da_changes(changes):
                """Callback that receives OPC DA changes"""
                self._da_to_main_queue.put(('data_change', changes))

            # Extract tag names from tag_values for subscription
            tag_names = [tv[0] for tv in tag_values]

            # Split into groups if there are many tags (typical limit: ~1000 per group)
            chunk_size = 1000
            for i, chunk_start in enumerate(range(0, len(tag_names), chunk_size)):
                chunk = tag_names[chunk_start:chunk_start + chunk_size]
                try:
                    self.da_client.subscribe(
                        tags=chunk,
                        callback=on_da_changes,
                        group=f"ua_bridge_{i}",
                        update_rate=500,
                        deadband=0.0
                    )
                except Exception as e:
                    log.error(f"Failed to subscribe to chunk {i}: {e}")

            log.info(f"Subscribed to {len(tag_names)} tags in OPC DA")

            # Keep thread alive while active and process write requests
            while self.da_thread_active:
                pythoncom.PumpWaitingMessages()

                # Process any pending write requests from UA clients
                try:
                    while True:
                        tag, value = self._write_queue.get_nowait()
                        try:
                            self.da_client.write((tag, value))
                            log.info(f"DA write successful: {tag} = {value}")
                        except Exception as e:
                            log.error(f"DA write failed for {tag}: {e}")
                except Empty:
                    pass

                import time
                time.sleep(0.01)

        except Exception as e:
            log.error(f"OPC DA thread error: {e}")
            self._da_to_main_queue.put(('error', str(e)))

        finally:
            if self.da_client:
                try:
                    self.da_client.close()
                except:
                    pass
            pythoncom.CoUninitialize()
            log.info("OPC DA thread terminated")

    async def _process_da_events(self):
        """Process OPC DA events and update UA nodes"""
        while self._running:
            try:
                # Get event from DA->Main queue (non-blocking)
                try:
                    event = self._da_to_main_queue.get_nowait()
                except Empty:
                    await asyncio.sleep(0.01)
                    continue

                event_type, data = event

                if event_type == 'data_change':
                    # Update OPC UA nodes with new values
                    for tag, value, quality, timestamp in data:
                        if tag in self.ua_nodes:
                            node = self.ua_nodes[tag]
                            try:
                                # Store value from DA to distinguish from client writes
                                self._last_da_values[tag] = value
                                await node.write_value(value)
                            except Exception as e:
                                log.debug(f"Could not update UA node '{tag}': {e}")

                elif event_type == 'error':
                    log.error(f"OPC DA error: {data}")

            except Exception as e:
                log.error(f"Error processing DA events: {e}")

    async def start(self, opc_server: str = None, opc_host: str = None):
        """Start the OPC DA to OPC UA bridge"""
        opc_server = opc_server or self.config.OPC_SERVER
        opc_host = opc_host or self.config.OPC_HOST

        if not opc_server:
            raise ValueError("OPC server name is required")

        log.info("Starting OPC DA to OPC UA bridge...")

        # 1. Configure OPC UA server
        await self._setup_ua_server()

        # 2. Start OPC DA thread
        self.da_thread_active = True
        self.da_thread = threading.Thread(
            target=self._da_thread_main,
            args=(opc_server, opc_host),
            daemon=True
        )
        self.da_thread.start()

        # 3. Wait for tag discovery (from DA->Main queue)
        log.info("Waiting for OPC DA tag discovery...")
        tag_values = None
        timeout = 60  # Increased timeout to allow for reading initial values
        import time
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                event = self._da_to_main_queue.get(timeout=0.5)
                if event[0] == 'tags_discovered':
                    tag_values = event[1]
                    log.info(f"Received {len(tag_values)} tags with values from DA thread")
                    break
                elif event[0] == 'error':
                    raise RuntimeError(f"OPC DA error: {event[1]}")
            except Empty:
                await asyncio.sleep(0.01)

        if tag_values is None:
            raise RuntimeError("Timeout waiting for OPC DA tag discovery")

        # 4. Create OPC UA nodes with initial values
        await self._create_ua_nodes(tag_values)

        # 5. Set up internal subscription to detect client writes
        if self.ua_nodes:
            write_handler = WriteHandler(self)
            internal_sub = await self.ua_server.create_subscription(100, write_handler)
            nodes_list = list(self.ua_nodes.values())
            await internal_sub.subscribe_data_change(nodes_list)
            log.info(f"Internal subscription created for {len(nodes_list)} nodes (write detection)")

        # 6. Notify DA thread that it can start subscriptions (via Main->DA queue)
        self._main_to_da_queue.put('ua_ready')

        # 6. Start OPC UA server
        self._running = True
        async with self.ua_server:
            log.info("=" * 60)
            log.info(f"OPC UA Server running at: {self._endpoint}")
            log.info(f"Namespace: http://openopc2.bridge (index: {self.ua_namespace_idx})")
            log.info(f"Tags exposed: {len(self.ua_nodes)}")
            log.info("Press Ctrl+C to stop")
            log.info("=" * 60)

            # Process DA events in parallel
            await self._process_da_events()

    def stop(self):
        """Stop the bridge"""
        log.info("Stopping bridge...")
        self._running = False
        self.da_thread_active = False

        if self.da_thread:
            self.da_thread.join(timeout=5.0)


def main():
    """Entry point for running the bridge"""
    import argparse

    parser = argparse.ArgumentParser(description='OPC DA to OPC UA Bridge')
    parser.add_argument('--opc-server', default=None, help='OPC DA server name')
    parser.add_argument('--opc-host', default='localhost', help='OPC DA host')
    parser.add_argument('--ua-endpoint', default='opc.tcp://0.0.0.0:4840/openopc2/',
                        help='OPC UA endpoint')
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='Log level')
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(level=getattr(logging, args.log_level))

    config = OpenOpcConfig()
    if args.opc_server:
        config.OPC_SERVER = args.opc_server
    config.OPC_HOST = args.opc_host

    bridge = OpcDaUaBridge(config)
    bridge.set_endpoint(args.ua_endpoint)

    try:
        asyncio.run(bridge.start(args.opc_server, args.opc_host))
    except KeyboardInterrupt:
        bridge.stop()
        print("\nBridge stopped.")


if __name__ == "__main__":
    main()

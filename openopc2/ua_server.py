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

# OPC DA quality to OPC UA StatusCode mapping
DA_QUALITY_TO_UA_STATUS = {
    'Good': ua.StatusCodes.Good,
    'Uncertain': ua.StatusCodes.Uncertain,
    'Bad': ua.StatusCodes.Bad,
    'Unknown': ua.StatusCodes.BadUnknownResponse,
    'Error': ua.StatusCodes.Bad,
}


def da_quality_to_ua_status(quality: str) -> ua.StatusCode:
    """Convert OPC DA quality string to OPC UA StatusCode"""
    status_code = DA_QUALITY_TO_UA_STATUS.get(quality, ua.StatusCodes.Bad)
    return ua.StatusCode(status_code)

from openopc2.da_client import OpcDaClient
from openopc2.config import OpenOpcConfig
from openopc2.logger import log


def convert_pywin_value(value, convert_datetime_to_string=False):
    """Convert pywintypes values to standard Python types for OPC UA compatibility

    Args:
        value: The value to convert
        convert_datetime_to_string: If True, convert datetime to ISO string format
    """
    if value is None:
        return value

    # Check for pywintypes.datetime (has tzinfo with TimeZoneInfo type)
    type_name = type(value).__name__
    if type_name == 'datetime' and hasattr(value, 'tzinfo'):
        # Convert pywintypes.datetime to standard datetime
        from datetime import datetime, timezone
        try:
            dt = datetime(
                value.year, value.month, value.day,
                value.hour, value.minute, value.second,
                value.microsecond, tzinfo=timezone.utc
            )
            if convert_datetime_to_string:
                return dt.isoformat()
            return dt
        except Exception:
            return value

    # Handle standard datetime objects too
    from datetime import datetime
    if isinstance(value, datetime):
        if convert_datetime_to_string:
            return value.isoformat()
        return value

    return value


class WriteHandler:
    """Handles write events from OPC UA clients"""

    def __init__(self, bridge: 'OpcDaUaBridge'):
        self.bridge = bridge
        self._initialized_nodes: set = set()  # Nodes that have received initial value

    def datachange_notification(self, node, val, data):
        """Called when a node value changes"""
        # Check if writes are enabled
        if not self.bridge._enable_writes:
            return  # Writes disabled, ignore all changes

        node_id = node.nodeid.to_string()
        if node_id in self.bridge.ua_node_to_tag:
            tag = self.bridge.ua_node_to_tag[node_id]

            # Skip the first notification (initial value when subscription starts)
            if node_id not in self._initialized_nodes:
                self._initialized_nodes.add(node_id)
                return

            # Check if this is a pending DA update (not a client write)
            if tag in self.bridge._pending_da_updates:
                self.bridge._pending_da_updates.discard(tag)
                return  # This is from DA, not a client write

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
        self._pending_da_updates: set = set()  # Tags being updated from DA (to avoid write-back loop)
        self._discovery_timeout = 300  # Timeout in seconds for tag discovery (resets on progress)
        self._update_rate = 1000  # Subscription update rate in ms
        self._enable_writes = False  # Disable write-back to DA by default (safety)
        self._include_descriptions = True  # Read and expose tag descriptions (slower startup)
        self._ifix_optimized = False  # Optimized discovery for iFIX (enumerate containers, assume .F_CV)
        self._tag_refresh_interval = 600  # Tag refresh interval in seconds (default: 10 minutes)
        self._da_folder = None  # Reference to OPC DA tags folder for adding new nodes
        self._description_batch_size = 500  # Batch size for fast iFIX description reads
        self._skip_initial_read = False  # Skip slow initial value read; let subscriptions populate

    def set_endpoint(self, endpoint: str):
        """Configure OPC UA server endpoint"""
        self._endpoint = endpoint

    def set_server_name(self, name: str):
        """Configure OPC UA server name"""
        self._server_name = name

    def set_discovery_timeout(self, timeout: int):
        """Configure timeout in seconds for tag discovery"""
        self._discovery_timeout = timeout

    def set_update_rate(self, rate_ms: int):
        """Configure subscription update rate in milliseconds"""
        self._update_rate = rate_ms

    def set_enable_writes(self, enable: bool):
        """Enable or disable write-back to OPC DA"""
        self._enable_writes = enable

    def set_include_descriptions(self, include: bool):
        """Enable or disable reading tag descriptions from OPC DA"""
        self._include_descriptions = include

    def set_ifix_optimized(self, enabled: bool):
        """Enable optimized discovery for iFIX (enumerate containers, assume .F_CV)"""
        self._ifix_optimized = enabled

    def set_tag_refresh_interval(self, seconds: int):
        """Configure tag refresh interval in seconds (0 to disable)"""
        self._tag_refresh_interval = seconds

    def set_description_batch_size(self, n: int):
        """Configure batch size for fast iFIX description reads"""
        self._description_batch_size = n

    def set_skip_initial_read(self, skip: bool):
        """Skip slow initial value read; subscriptions will populate values."""
        self._skip_initial_read = skip

    def _datatype_for_tag(self, tag: str) -> "ua.NodeId":
        """Pick the OPC UA DataType NodeId based on iFIX suffix conventions.

        Used when creating the variable so that subsequent typed updates
        (e.g. Double for .F_CV) match — even when the initial value is Null.
        """
        if tag.endswith('.A_CV') or tag.endswith('.A_DESC') or '.A_' in tag:
            return ua.NodeId(ua.ObjectIds.String)
        if tag.endswith('.F_CV'):
            return ua.NodeId(ua.ObjectIds.Double)
        return ua.NodeId(ua.ObjectIds.Double)

    def _make_typed_variant(self, value, tag: str) -> "ua.Variant":
        """Build a Variant with explicit VariantType matching the node's DataType.

        Returns a Null variant when value is None — pair with an explicit
        datatype= argument on add_variable so subsequent typed writes are
        accepted (asyncua falls back to DataType when the stored variant
        is Null).
        """
        if value is None:
            return ua.Variant(None, ua.VariantType.Null)

        if isinstance(value, bool):
            return ua.Variant(value, ua.VariantType.Boolean)
        if isinstance(value, float):
            return ua.Variant(value, ua.VariantType.Double)
        if isinstance(value, int):
            if tag.endswith('.F_CV'):
                return ua.Variant(float(value), ua.VariantType.Double)
            return ua.Variant(value, ua.VariantType.Int64)
        if isinstance(value, str):
            return ua.Variant(value, ua.VariantType.String)
        return ua.Variant(value)

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

    async def _create_ua_nodes(self, tag_values: List[tuple], tag_descriptions: Dict[str, str] = None):
        """Create OPC UA nodes for each OPC DA tag with initial values

        Args:
            tag_values: List of (tag, value, quality, timestamp) tuples
            tag_descriptions: Optional dict mapping tag names to descriptions
        """
        if tag_descriptions is None:
            tag_descriptions = {}

        objects = self.ua_server.nodes.objects

        # Create a folder for OPC DA tags
        da_folder = await objects.add_folder(self.ua_namespace_idx, "OpcDaTags")
        self._da_folder = da_folder  # Save reference for adding new nodes during refresh

        for tag, value, quality, timestamp in tag_values:
            try:
                # Convert pywintypes to standard Python types
                original_type = type(value).__name__
                value = convert_pywin_value(value)

                if len(self.ua_nodes) < 5:
                    log.info(f"Tag '{tag}': original_type={original_type}, value={value}, quality={quality}, python_type={type(value).__name__}")

                # Build typed Variant (Null when value is None) and pick the
                # node's DataType from the tag suffix — explicit datatype= is
                # required so that future typed writes are accepted while the
                # current value is Null.
                variant = self._make_typed_variant(value, tag)
                datatype = self._datatype_for_tag(tag)

                node = await da_folder.add_variable(
                    self.ua_namespace_idx,
                    tag,
                    variant,
                    datatype=datatype,
                )

                # Read-only by default. set_writable() only when explicitly enabled
                # — defense-in-depth against writes to live process systems.
                if self._enable_writes:
                    await node.set_writable()

                # Apply quality via set_value:
                # - Null value: BadWaitingForInitialData until first DA update
                # - Non-Good DA quality: forward as-is
                if value is None:
                    dv = ua.DataValue(
                        variant,
                        StatusCode_=ua.StatusCode(ua.StatusCodes.BadWaitingForInitialData)
                    )
                    await node.set_value(dv)
                elif quality != 'Good':
                    ua_status = da_quality_to_ua_status(quality)
                    dv = ua.DataValue(variant, StatusCode_=ua_status)
                    await node.set_value(dv)

                # Set description if available
                if tag in tag_descriptions and tag_descriptions[tag]:
                    desc = ua.LocalizedText(tag_descriptions[tag])
                    await node.write_attribute(
                        ua.AttributeIds.Description,
                        ua.DataValue(ua.Variant(desc, ua.VariantType.LocalizedText))
                    )

                self.ua_nodes[tag] = node
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

    def _discover_tags_standard(self, ifix_only_fcv: bool = False) -> List[str]:
        """Standard tag discovery - enumerate all tags"""
        import time
        start_time = time.time()

        all_tags = []
        tag_count = [0]
        last_report_time = [time.time()]

        # Try flat=True first
        print("[PROGRESO] Enumerando tags (flat=True)...")
        for tag in self.da_client.ilist(flat=True, recursive=True):
            all_tags.append(tag)
            tag_count[0] += 1

            now = time.time()
            if now - last_report_time[0] >= 5 or tag_count[0] % 10000 == 0:
                print(f"[PROGRESO] {tag_count[0]:,} tags enumerados...")
                self._da_to_main_queue.put(('progress', f'{tag_count[0]} tags'))
                last_report_time[0] = now

        print(f"[PROGRESO] Listado completado: {len(all_tags):,} tags encontrados")
        log.info(f"Found {len(all_tags)} tags with flat=True")

        if not all_tags:
            log.info("No tags with flat=True, trying flat=False...")
            self._da_to_main_queue.put(('progress', 'discovering_flat_false'))
            print("[PROGRESO] Enumerando tags (flat=False)...")

            for tag in self.da_client.ilist(flat=False, recursive=True):
                all_tags.append(tag)
                tag_count[0] += 1

                now = time.time()
                if now - last_report_time[0] >= 5 or tag_count[0] % 10000 == 0:
                    print(f"[PROGRESO] {tag_count[0]:,} tags enumerados...")
                    self._da_to_main_queue.put(('progress', f'{tag_count[0]} tags'))
                    last_report_time[0] = now

            print(f"[PROGRESO] Listado completado: {len(all_tags):,} tags encontrados")
            log.info(f"Found {len(all_tags)} tags with flat=False")

        # Filter system tags
        tags = [t for t in all_tags if not t.startswith('#') and not t.startswith('@')]

        # Filter for iFIX F_CV tags only if requested
        if ifix_only_fcv:
            before_filter = len(tags)
            tags = [t for t in tags if t.endswith('.F_CV')]
            print(f"[PROGRESO] Filtrado --ifix-only-fcv: de {before_filter} a {len(tags)} tags")
            log.info(f"Filtered from {before_filter} to {len(tags)} tags ending with .F_CV")

        elapsed = time.time() - start_time
        log.info(f"Tag discovery completed in {elapsed:.1f} seconds. Total: {len(all_tags)}, usable: {len(tags)}")
        print(f"[PROGRESO] Descubrimiento completado en {elapsed:.1f} segundos")

        return tags

    def _read_descriptions_ifix_fast(self, tags: List[str], batch_size: int = 500) -> Dict[str, str]:
        """Read iFIX tag descriptions via batched SyncRead of .A_DESC siblings.

        iFIX exposes the description as a sibling field NODE.TAG.A_DESC. Reading
        these in batches via the standard OPC group / SyncRead mechanism is
        dramatically faster than calling GetItemProperties one tag at a time
        (one COM round-trip per tag).
        """
        import time
        start = time.time()

        desc_to_orig = {}
        desc_tags = []
        for tag in tags:
            for suffix in ('.F_CV', '.A_CV'):
                if tag.endswith(suffix):
                    desc_tag = tag[:-len(suffix)] + '.A_DESC'
                    desc_to_orig[desc_tag] = tag
                    desc_tags.append(desc_tag)
                    break

        if not desc_tags:
            log.warning("No .F_CV/.A_CV tags; cannot use iFIX fast description path")
            return {}

        print(f"[PROGRESO] Lectura rapida de descripciones (.A_DESC, batch={batch_size}, total={len(desc_tags)})")
        log.info(f"Reading descriptions via .A_DESC fast path (batch={batch_size}, count={len(desc_tags)})")

        descriptions = {}
        bad_quality = 0
        chunk_errors = 0
        last_report = time.time()

        for i in range(0, len(desc_tags), batch_size):
            chunk = desc_tags[i:i + batch_size]
            try:
                # Anonymous group is created and auto-removed by iread()
                results = self.da_client.read(chunk)
            except Exception as e:
                chunk_errors += 1
                log.debug(f"Error reading description chunk at {i}: {e}")
                continue

            for desc_tag, value, quality, _ in results:
                if quality not in ('Good', 'Uncertain'):
                    bad_quality += 1
                    continue
                if value is None:
                    continue
                orig_tag = desc_to_orig.get(desc_tag)
                if not orig_tag:
                    continue
                s = str(value).strip()
                if s:
                    descriptions[orig_tag] = s

            now = time.time()
            progress = min(i + batch_size, len(desc_tags))
            if now - last_report >= 5 or progress >= len(desc_tags):
                elapsed = now - start
                rate = progress / elapsed if elapsed > 0 else 0
                print(f"[PROGRESO] Descripciones: {progress}/{len(desc_tags)} "
                      f"({progress * 100 // len(desc_tags)}%, {rate:.0f} tags/s)")
                self._da_to_main_queue.put(('progress', f'descriptions {progress}/{len(desc_tags)}'))
                last_report = now

        elapsed = time.time() - start
        rate = len(desc_tags) / elapsed if elapsed > 0 else 0
        log.info(
            f"iFIX descriptions read in {elapsed:.1f}s "
            f"({rate:.0f} tags/s, {len(descriptions)} found, "
            f"{bad_quality} bad quality, {chunk_errors} chunk errors)"
        )
        print(f"[PROGRESO] Descripciones leidas: {len(descriptions)}/{len(desc_tags)} en {elapsed:.1f}s ({rate:.0f} tags/s)")
        return descriptions

    def _discover_tags_ifix_optimized(self) -> List[str]:
        """iFIX optimized discovery - navigate browser structure and build tag list

        Uses browser navigation to discover tags by type (branch).
        TX branch uses .A_CV suffix (text), all others use .F_CV (numeric).

        Structure: NODO.TAG.CAMPO
        - NODO: SCADA node name (e.g., UYMVDA01)
        - TAG: Block/tag name (e.g., AI_TEMP_001)
        - CAMPO: Field (.F_CV or .A_CV)

        Raises the underlying COM error on browser/discovery failure so the
        caller can distinguish a transient iFIX failure (dead COM proxy, iFIX
        not yet ready) from a legitimate empty result.
        """
        import time
        start_time = time.time()

        print("[PROGRESO] Modo iFIX optimizado: navegando estructura con browser...")
        log.info("iFIX optimized discovery: navigating browser structure...")
        self._da_to_main_queue.put(('progress', 'ifix browser start'))

        value_tags = []
        fcv_count = 0
        acv_count = 0

        try:
            # Get the browser from the OPC client
            browser = self.da_client._opc.opc_client.CreateBrowser()

            # Navigate from root
            browser.MoveToRoot()
            browser.ShowBranches()
            node_count = browser.Count

            if node_count == 0:
                print("[PROGRESO] No se encontraron nodos en la raiz")
                log.warning("No nodes found at root level")
                return value_tags

            # Get the SCADA node (first branch at root level)
            nodes = []
            for i in range(1, node_count + 1):
                try:
                    nodes.append(browser.Item(i))
                except:
                    pass

            print(f"[PROGRESO] Nodos SCADA encontrados: {nodes}")
            log.info(f"SCADA nodes found: {nodes}")

            # Process each SCADA node
            for node in nodes:
                browser.MoveToRoot()
                browser.MoveDown(node)

                # Get block types (branches: AA, AI, AO, TX, etc.)
                browser.ShowBranches()
                type_count = browser.Count

                block_types = []
                for i in range(1, type_count + 1):
                    try:
                        block_types.append(browser.Item(i))
                    except:
                        pass

                print(f"[PROGRESO] Nodo {node}: {len(block_types)} tipos de bloque")
                log.info(f"Node {node}: {len(block_types)} block types: {block_types[:10]}...")

                # Process each block type
                for block_type in block_types:
                    # Determine suffix based on block type
                    # TX (Text) blocks use A_CV, all others use F_CV
                    if block_type == 'TX':
                        suffix = '.A_CV'
                    else:
                        suffix = '.F_CV'

                    # Navigate into the block type
                    browser.MoveToRoot()
                    browser.MoveDown(node)
                    browser.MoveDown(block_type)

                    # Get all tags (sub-branches) in this type
                    browser.ShowBranches()
                    tag_count = browser.Count

                    tags_in_type = 0
                    for i in range(1, tag_count + 1):
                        try:
                            tag_name = browser.Item(i)
                            # Build the full ItemID: NODO.TAG.SUFFIX
                            item_id = f"{node}.{tag_name}{suffix}"
                            value_tags.append(item_id)
                            tags_in_type += 1

                            if suffix == '.A_CV':
                                acv_count += 1
                            else:
                                fcv_count += 1

                        except Exception as e:
                            log.debug(f"Error getting tag {i} in {block_type}: {e}")

                    if tags_in_type > 0:
                        log.info(f"  {block_type}: {tags_in_type} tags ({suffix})")

                    # Report progress
                    self._da_to_main_queue.put(('progress', f'{len(value_tags)} tags'))

                print(f"[PROGRESO] Total parcial: {len(value_tags)} tags")

        except Exception as e:
            log.error(f"Error during iFIX browser discovery: {e}")
            print(f"[ERROR] Error en descubrimiento: {e}")
            import traceback
            traceback.print_exc()
            elapsed = time.time() - start_time
            log.info(f"iFIX discovery aborted after {elapsed:.1f} seconds (raising)")
            raise

        elapsed = time.time() - start_time

        log.info(f"iFIX discovery completed in {elapsed:.1f} seconds")
        log.info(f"Value tags: {len(value_tags)} ({fcv_count} F_CV, {acv_count} A_CV)")
        print(f"[PROGRESO] Descubrimiento iFIX completado en {elapsed:.1f}s")
        print(f"[PROGRESO] Tags de valor: {len(value_tags)} ({fcv_count} F_CV, {acv_count} A_CV)")

        return value_tags

    def _da_thread_main(self, opc_server: str, opc_host: str, ifix_only_fcv: bool = False, update_rate: int = 1000, include_descriptions: bool = False, ifix_optimized: bool = False, tag_refresh_interval: int = 600, skip_initial_read: bool = False):
        """Main thread for OPC DA (COM requires its own thread)"""
        import pythoncom
        pythoncom.CoInitialize()

        try:
            print("[PROGRESO] Iniciando thread OPC DA...")
            log.info("DA thread starting...")
            # Send progress event to let main thread know we started
            self._da_to_main_queue.put(('progress', 'starting'))

            # Create and connect OPC DA client
            self.da_client = OpcDaClient(self.config)
            print(f"[PROGRESO] Conectando a servidor OPC DA: {opc_server} en {opc_host}...")
            log.info(f"Connecting to OPC DA server: {opc_server} on {opc_host}...")
            self._da_to_main_queue.put(('progress', 'connecting'))
            try:
                self.da_client.connect(opc_server, opc_host)
            except Exception as e:
                error_msg = (
                    f"No se pudo conectar al servidor OPC DA '{opc_server}' en '{opc_host}'.\n"
                    f"Posibles causas:\n"
                    f"  - El servidor no existe o el nombre es incorrecto\n"
                    f"  - El servidor es de 64 bits (este cliente es de 32 bits)\n"
                    f"  - El servidor no esta corriendo\n"
                    f"  - Problemas de permisos DCOM\n"
                    f"Use --list-servers para ver los servidores disponibles.\n"
                    f"Error original: {e}"
                )
                raise RuntimeError(error_msg)
            print(f"[PROGRESO] Conectado a servidor OPC DA: {opc_server}")
            log.info(f"Connected to OPC DA server: {opc_server} on {opc_host}")
            self._da_to_main_queue.put(('progress', 'connected'))

            # Discover tags using appropriate method.
            # Initial discovery is retried with COM-client rebuild because
            # at boot the bridge may race ahead of iFIX: CoCreateInstance of
            # Intellution.OPCiFIX.1 succeeds before iFIX has finished
            # publishing its address space, so the browser returns 0 nodes
            # at root (or raises RPC_S_SERVER_UNAVAILABLE shortly after).
            # Going live with 0 tags strands all UA clients on a dead
            # namespace until manual restart.
            import time
            print("[PROGRESO] Iniciando listado de tags...")
            self._da_to_main_queue.put(('progress', 'discovering'))

            INITIAL_DISCOVERY_MAX_ATTEMPTS = 60   # 60 * 10s = up to 10 minutes
            INITIAL_DISCOVERY_RETRY_SLEEP = 10
            tags = []
            for attempt in range(1, INITIAL_DISCOVERY_MAX_ATTEMPTS + 1):
                if not self.da_thread_active:
                    return
                try:
                    if ifix_optimized:
                        if attempt == 1:
                            log.info("Using iFIX optimized discovery (enumerate containers, assume .F_CV)")
                        tags = self._discover_tags_ifix_optimized()
                    else:
                        if attempt == 1:
                            log.info("Using standard tag discovery")
                        tags = self._discover_tags_standard(ifix_only_fcv)
                except Exception as discovery_exc:
                    log.warning(
                        f"Initial tag discovery failed (attempt {attempt}/{INITIAL_DISCOVERY_MAX_ATTEMPTS}): "
                        f"{discovery_exc}"
                    )
                    print(
                        f"[PROGRESO] Descubrimiento fallo (intento {attempt}/{INITIAL_DISCOVERY_MAX_ATTEMPTS}): "
                        f"{discovery_exc}"
                    )
                    tags = []

                if len(tags) > 0:
                    if attempt > 1:
                        log.info(f"Initial tag discovery succeeded on attempt {attempt} ({len(tags)} tags)")
                        print(f"[PROGRESO] Descubrimiento OK en intento {attempt}: {len(tags)} tags")
                    break

                if attempt >= INITIAL_DISCOVERY_MAX_ATTEMPTS:
                    log.error(
                        f"Initial tag discovery returned 0 tags after {INITIAL_DISCOVERY_MAX_ATTEMPTS} "
                        f"attempts; aborting bridge startup so the service wrapper can restart us"
                    )
                    print(
                        f"[ERROR] Descubrimiento inicial fallo {INITIAL_DISCOVERY_MAX_ATTEMPTS} veces. "
                        f"Saliendo para que WinSW reinicie."
                    )
                    raise RuntimeError(
                        f"OPC DA tag discovery returned 0 tags after "
                        f"{INITIAL_DISCOVERY_MAX_ATTEMPTS} attempts (iFIX not ready?)"
                    )

                # Rebuild the COM client before the next attempt. The current
                # proxy may be dead (RPC_S_SERVER_UNAVAILABLE) once iFIX
                # finishes its own startup sequence after we already grabbed
                # a stale handle.
                log.info(
                    f"Initial discovery returned 0 tags; rebuilding OPC DA client and retrying in "
                    f"{INITIAL_DISCOVERY_RETRY_SLEEP}s (attempt {attempt + 1}/{INITIAL_DISCOVERY_MAX_ATTEMPTS})"
                )
                print(
                    f"[PROGRESO] 0 tags. Reconectando OPC DA y reintentando en "
                    f"{INITIAL_DISCOVERY_RETRY_SLEEP}s..."
                )
                try:
                    self.da_client.close()
                except Exception:
                    pass
                # Sleep before reconnect so iFIX has time to come up.
                slept = 0
                while slept < INITIAL_DISCOVERY_RETRY_SLEEP and self.da_thread_active:
                    time.sleep(0.5)
                    slept += 0.5
                if not self.da_thread_active:
                    return
                try:
                    self.da_client = OpcDaClient(self.config)
                    self.da_client.connect(opc_server, opc_host)
                except Exception as reconnect_exc:
                    log.warning(f"OPC DA reconnect failed during initial discovery retry: {reconnect_exc}")
                    print(f"[PROGRESO] Reconexion fallo: {reconnect_exc}")

            # Read initial values, or skip and let subscriptions populate.
            tag_values = []
            total_tags = len(tags)

            if skip_initial_read:
                # Skip the slow per-chunk SyncRead bootstrap. Each iFIX chunk
                # creates+destroys an OPC group; iFIX/Graybox group lifecycle
                # cost balloons over time (rate decays from ~45 tags/s to <1).
                # Subscriptions will deliver real values within the first
                # update_rate window after they activate.
                log.info(f"Skipping initial value read for {total_tags} tags (--skip-initial-read)")
                print(f"[PROGRESO] Saltando lectura inicial: {total_tags} tags quedaran en Null hasta primera suscripcion")
                tag_values = [(tag, None, 'Bad', None) for tag in tags]
                self._da_to_main_queue.put(('progress', total_tags))
            else:
                log.info("Reading initial values from OPC DA...")
                print("[PROGRESO] Leyendo valores iniciales...")
                chunk_size = 100  # Smaller chunks for reliability
                error_count = 0

                def read_chunk_safe(tag_list):
                    """Read tags with fallback to smaller chunks on error"""
                    results = []
                    try:
                        results = self.da_client.read(tag_list)
                    except Exception:
                        if len(tag_list) > 10:
                            mid = len(tag_list) // 2
                            results.extend(read_chunk_safe(tag_list[:mid]))
                            results.extend(read_chunk_safe(tag_list[mid:]))
                        else:
                            for tag in tag_list:
                                try:
                                    r = self.da_client.read([tag])
                                    results.extend(r)
                                except:
                                    results.append((tag, None, 'Error', None))
                    return results

                for i in range(0, total_tags, chunk_size):
                    chunk = tags[i:i + chunk_size]
                    chunk_values = read_chunk_safe(chunk)
                    for tv in chunk_values:
                        if tv[2] != 'Error':
                            tag_values.append(tv)
                        else:
                            error_count += 1
                    progress = min(i + chunk_size, total_tags)
                    if progress % 500 == 0 or progress >= total_tags:
                        log.info(f"Read {progress}/{total_tags} tags ({100*progress//total_tags}%)")
                        print(f"[PROGRESO] Leidos {progress}/{total_tags} tags")
                    self._da_to_main_queue.put(('progress', progress))

                log.info(f"Finished reading {len(tag_values)} initial values ({error_count} errors)")
                print(f"[PROGRESO] Lectura completada: {len(tag_values)} tags validos, {error_count} errores")

            # Read tag descriptions if enabled
            tag_descriptions = {}
            if include_descriptions:
                self._da_to_main_queue.put(('progress', 'reading descriptions'))

                if ifix_optimized:
                    # Fast path for iFIX: batched SyncRead of .A_DESC siblings
                    tag_descriptions = self._read_descriptions_ifix_fast(
                        tags, batch_size=self._description_batch_size
                    )
                else:
                    # Slow path: GetItemProperties (one COM round-trip per tag)
                    print("[PROGRESO] Leyendo descripciones (GetItemProperties, serial)...")
                    log.info("Reading tag descriptions via GetItemProperties (serial)...")
                    DESCRIPTION_PROPERTY_ID = 101
                    desc_count = 0
                    desc_errors = 0
                    desc_start = time.time()

                    for i, tag in enumerate(tags):
                        try:
                            opc_client = self.da_client._opc.opc_client
                            prop_values, errors = opc_client.GetItemProperties(
                                tag, 1, [0, DESCRIPTION_PROPERTY_ID]
                            )
                            if prop_values and len(prop_values) > 0 and prop_values[0]:
                                description = str(prop_values[0])
                                if description and description.strip():
                                    tag_descriptions[tag] = description.strip()
                                    desc_count += 1
                        except Exception as e:
                            desc_errors += 1
                            log.debug(f"Could not read description for '{tag}': {e}")

                        if (i + 1) % 500 == 0:
                            elapsed = time.time() - desc_start
                            rate = (i + 1) / elapsed if elapsed > 0 else 0
                            print(f"[PROGRESO] Descripciones leidas: {i + 1}/{len(tags)} ({rate:.0f} tags/s)")
                            self._da_to_main_queue.put(('progress', f'descriptions {i + 1}/{len(tags)}'))

                    desc_elapsed = time.time() - desc_start
                    print(f"[PROGRESO] Descripciones leidas: {desc_count} de {len(tags)} en {desc_elapsed:.1f}s")
                    log.info(f"Read {desc_count} descriptions in {desc_elapsed:.1f}s ({desc_errors} errors)")

            # Notify main thread of discovered tags with values (use DA->Main queue)
            self._da_to_main_queue.put(('tags_discovered', (tag_values, tag_descriptions)))

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
            total_chunks = (len(tag_names) + chunk_size - 1) // chunk_size
            log.info(f"Subscribing to {len(tag_names)} tags in {total_chunks} groups...")
            self._da_to_main_queue.put(('progress', f'subscribing 0/{total_chunks}'))

            for i, chunk_start in enumerate(range(0, len(tag_names), chunk_size)):
                chunk = tag_names[chunk_start:chunk_start + chunk_size]
                try:
                    self.da_client.subscribe(
                        tags=chunk,
                        callback=on_da_changes,
                        group=f"ua_bridge_{i}",
                        update_rate=update_rate,
                        deadband=0.0
                    )
                    # Report progress after each chunk
                    progress_pct = 100 * (i + 1) // total_chunks
                    log.info(f"Subscribed group {i+1}/{total_chunks} ({progress_pct}%)")
                    self._da_to_main_queue.put(('progress', f'subscribing {i+1}/{total_chunks}'))
                except Exception as e:
                    log.error(f"Failed to subscribe to chunk {i}: {e}")

            log.info(f"Subscribed to {len(tag_names)} tags in OPC DA")
            self._da_to_main_queue.put(('progress', 'subscribed'))

            # Track current tags for refresh comparison
            current_tags = set(tag_names)
            last_refresh_time = time.time()
            next_group_id = total_chunks  # For new subscription groups

            # Keep thread alive while active and process write requests
            while self.da_thread_active:
                pythoncom.PumpWaitingMessages()

                # Process any pending write requests from UA clients
                try:
                    while True:
                        tag, value = self._write_queue.get_nowait()
                        # Defense-in-depth: refuse writes unless explicitly enabled.
                        # This is redundant with the WriteHandler check, but a hard
                        # guard at the COM boundary protects live process systems.
                        if not self._enable_writes:
                            log.warning(
                                f"BLOCKED write to OPC DA (writes disabled): {tag}={value}"
                            )
                            continue
                        try:
                            self.da_client.write((tag, value))
                            log.info(f"DA write successful: {tag} = {value}")
                        except Exception as e:
                            log.error(f"DA write failed for {tag}: {e}")
                except Empty:
                    pass

                # Check if it's time to refresh the tag list
                if tag_refresh_interval > 0:
                    now = time.time()
                    if now - last_refresh_time >= tag_refresh_interval:
                        last_refresh_time = now
                        log.info(f"Starting periodic tag refresh (interval: {tag_refresh_interval}s)...")
                        print(f"[PROGRESO] Iniciando refresco periodico de tags...")
                        refresh_start = time.time()

                        try:
                            # Rediscover tags. A failure here is almost
                            # always a stale COM proxy (iFIX restarted while
                            # we held its server handle). Treat it as
                            # transient and SKIP this refresh — do NOT
                            # interpret an empty/error result as "all 20k
                            # tags removed" and flip them all to Bad
                            # quality, which is what the previous code did.
                            discovery_failed = False
                            try:
                                if ifix_optimized:
                                    new_tags = set(self._discover_tags_ifix_optimized())
                                else:
                                    new_tags = set(self._discover_tags_standard(ifix_only_fcv))
                            except Exception as discovery_exc:
                                discovery_failed = True
                                new_tags = set()
                                log.warning(
                                    f"Periodic discovery raised {discovery_exc!r}; "
                                    f"skipping this refresh and attempting COM reconnect"
                                )
                                print(
                                    f"[PROGRESO] Refresco fallo: {discovery_exc}. "
                                    f"Reconectando OPC DA en background."
                                )

                            # Guard against the dead-proxy pattern where
                            # browser navigation returns 0 nodes without
                            # raising. If discovery returned empty but we
                            # previously had tags, treat it as transient
                            # and skip.
                            if not discovery_failed and not new_tags and current_tags:
                                discovery_failed = True
                                log.warning(
                                    f"Periodic discovery returned 0 tags but "
                                    f"{len(current_tags)} were known; treating as "
                                    f"transient and skipping refresh"
                                )
                                print(
                                    f"[PROGRESO] Refresco devolvio 0 tags pero "
                                    f"teniamos {len(current_tags)}: descartando ciclo."
                                )

                            if discovery_failed:
                                # Best-effort COM reconnect so the next
                                # refresh has a chance to succeed. Existing
                                # subscriptions stay in place; if they were
                                # already dead, an external client (or a
                                # WinSW restart) will eventually catch it.
                                try:
                                    self.da_client.close()
                                except Exception:
                                    pass
                                try:
                                    self.da_client = OpcDaClient(self.config)
                                    self.da_client.connect(opc_server, opc_host)
                                    log.info("OPC DA client reconnected after refresh failure")
                                    print("[PROGRESO] OPC DA reconectado tras fallo de refresco")
                                except Exception as reconnect_exc:
                                    log.warning(
                                        f"OPC DA reconnect after refresh failure failed: {reconnect_exc}"
                                    )
                                refresh_elapsed = time.time() - refresh_start
                                log.info(
                                    f"Tag refresh skipped (transient failure) after {refresh_elapsed:.1f}s"
                                )
                                print(
                                    f"[PROGRESO] Refresco saltado (fallo transitorio) en "
                                    f"{refresh_elapsed:.1f}s"
                                )
                                continue

                            # Find added and removed tags
                            added_tags = new_tags - current_tags
                            removed_tags = current_tags - new_tags

                            refresh_elapsed = time.time() - refresh_start
                            log.info(f"Tag refresh completed in {refresh_elapsed:.1f}s: {len(added_tags)} added, {len(removed_tags)} removed")
                            print(f"[PROGRESO] Refresco completado en {refresh_elapsed:.1f}s: +{len(added_tags)} -{len(removed_tags)} tags")

                            if added_tags:
                                log.info(f"New tags found: {list(added_tags)[:10]}{'...' if len(added_tags) > 10 else ''}")

                                # Read values for new tags
                                added_list = list(added_tags)
                                new_tag_values = []
                                for i in range(0, len(added_list), 500):
                                    chunk = added_list[i:i + 500]
                                    chunk_values = self.da_client.read(chunk)
                                    new_tag_values.extend(chunk_values)

                                # Read descriptions for new tags if enabled
                                new_descriptions = {}
                                if include_descriptions:
                                    if ifix_optimized:
                                        new_descriptions = self._read_descriptions_ifix_fast(
                                            added_list, batch_size=self._description_batch_size
                                        )
                                    else:
                                        DESCRIPTION_PROPERTY_ID = 101
                                        opc_client = self.da_client._opc.opc_client
                                        for tag in added_list:
                                            try:
                                                prop_values, errors = opc_client.GetItemProperties(
                                                    tag, 1, [0, DESCRIPTION_PROPERTY_ID]
                                                )
                                                if prop_values and len(prop_values) > 0 and prop_values[0]:
                                                    desc = str(prop_values[0]).strip()
                                                    if desc:
                                                        new_descriptions[tag] = desc
                                            except:
                                                pass

                                # Subscribe to new tags
                                try:
                                    self.da_client.subscribe(
                                        tags=added_list,
                                        callback=on_da_changes,
                                        group=f"ua_bridge_{next_group_id}",
                                        update_rate=update_rate,
                                        deadband=0.0
                                    )
                                    next_group_id += 1
                                    log.info(f"Subscribed to {len(added_list)} new tags")
                                except Exception as e:
                                    log.error(f"Failed to subscribe to new tags: {e}")

                                # Notify main thread to create UA nodes for new tags
                                self._da_to_main_queue.put(('new_tags', (new_tag_values, new_descriptions)))
                                current_tags.update(added_tags)

                            if removed_tags:
                                log.info(f"Tags removed: {list(removed_tags)[:10]}{'...' if len(removed_tags) > 10 else ''}")
                                # Notify main thread about removed tags (will set quality to Bad)
                                self._da_to_main_queue.put(('removed_tags', list(removed_tags)))
                                current_tags -= removed_tags

                        except Exception as e:
                            log.error(f"Error during tag refresh: {e}")

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
        events_processed = 0
        last_yield_time = asyncio.get_event_loop().time()

        while self._running:
            try:
                # Get event from DA->Main queue (non-blocking)
                try:
                    event = self._da_to_main_queue.get_nowait()
                except Empty:
                    await asyncio.sleep(0.05)  # Longer sleep when idle
                    events_processed = 0
                    continue

                event_type, data = event

                if event_type == 'data_change':
                    # Update OPC UA nodes with new values and quality
                    for tag, value, quality, timestamp in data:
                        if tag in self.ua_nodes:
                            node = self.ua_nodes[tag]
                            try:
                                value = convert_pywin_value(value)
                                self._pending_da_updates.add(tag)
                                self._last_da_values[tag] = value
                                ua_status = da_quality_to_ua_status(quality)
                                # Use typed Variant matching the node's locked DataType
                                variant = self._make_typed_variant(value, tag)
                                dv = ua.DataValue(variant, StatusCode_=ua_status)
                                await node.set_value(dv)
                            except Exception as e:
                                self._pending_da_updates.discard(tag)
                                from datetime import datetime
                                if isinstance(value, datetime):
                                    try:
                                        self._pending_da_updates.add(tag)
                                        str_value = value.isoformat()
                                        ua_status = da_quality_to_ua_status(quality)
                                        dv = ua.DataValue(
                                            ua.Variant(str_value, ua.VariantType.String),
                                            StatusCode_=ua_status
                                        )
                                        await node.set_value(dv)
                                        self._last_da_values[tag] = str_value
                                    except Exception:
                                        self._pending_da_updates.discard(tag)
                                else:
                                    log.debug(f"Could not update UA node '{tag}': {e}")

                    events_processed += 1

                    # Yield to event loop periodically to allow UA server to handle connections
                    # Yield every 10 events or every 100ms, whichever comes first
                    current_time = asyncio.get_event_loop().time()
                    if events_processed >= 10 or (current_time - last_yield_time) >= 0.1:
                        await asyncio.sleep(0)  # Yield to event loop
                        last_yield_time = current_time
                        events_processed = 0

                elif event_type == 'new_tags':
                    # Create UA nodes for new tags discovered during refresh
                    new_tag_values, new_descriptions = data
                    log.info(f"Creating {len(new_tag_values)} new UA nodes from tag refresh")

                    for tag, value, quality, timestamp in new_tag_values:
                        if tag not in self.ua_nodes and self._da_folder is not None:
                            try:
                                value = convert_pywin_value(value)
                                variant = self._make_typed_variant(value, tag)
                                datatype = self._datatype_for_tag(tag)

                                node = await self._da_folder.add_variable(
                                    self.ua_namespace_idx,
                                    tag,
                                    variant,
                                    datatype=datatype,
                                )

                                if self._enable_writes:
                                    await node.set_writable()

                                if value is None:
                                    dv = ua.DataValue(
                                        variant,
                                        StatusCode_=ua.StatusCode(ua.StatusCodes.BadWaitingForInitialData)
                                    )
                                    await node.set_value(dv)
                                elif quality != 'Good':
                                    ua_status = da_quality_to_ua_status(quality)
                                    dv = ua.DataValue(variant, StatusCode_=ua_status)
                                    await node.set_value(dv)

                                if tag in new_descriptions and new_descriptions[tag]:
                                    desc = ua.LocalizedText(new_descriptions[tag])
                                    await node.write_attribute(
                                        ua.AttributeIds.Description,
                                        ua.DataValue(ua.Variant(desc, ua.VariantType.LocalizedText))
                                    )

                                self.ua_nodes[tag] = node
                                node_id = node.nodeid.to_string()
                                self.ua_node_to_tag[node_id] = tag
                                log.debug(f"Created new UA node for '{tag}'")
                            except Exception as e:
                                log.warning(f"Could not create UA node for new tag '{tag}': {e}")

                    log.info(f"Created {len(new_tag_values)} new UA nodes")

                elif event_type == 'removed_tags':
                    # Mark removed tags with Bad quality
                    removed_list = data
                    log.info(f"Marking {len(removed_list)} removed tags with Bad quality")

                    for tag in removed_list:
                        if tag in self.ua_nodes:
                            try:
                                node = self.ua_nodes[tag]
                                # Set quality to Bad to indicate tag no longer exists
                                dv = ua.DataValue(
                                    ua.Variant(None),
                                    StatusCode_=ua.StatusCode(ua.StatusCodes.BadNodeIdUnknown)
                                )
                                await node.set_value(dv)
                                log.debug(f"Marked tag '{tag}' as removed")
                            except Exception as e:
                                log.warning(f"Could not mark removed tag '{tag}': {e}")

                elif event_type == 'error':
                    log.error(f"OPC DA error: {data}")

            except Exception as e:
                log.error(f"Error processing DA events: {e}")

    async def start(self, opc_server: str = None, opc_host: str = None, ifix_only_fcv: bool = False):
        """Start the OPC DA to OPC UA bridge"""
        opc_server = opc_server or self.config.OPC_SERVER
        opc_host = opc_host or self.config.OPC_HOST
        self._ifix_only_fcv = ifix_only_fcv

        if not opc_server:
            raise ValueError("OPC server name is required")

        log.info("Starting OPC DA to OPC UA bridge...")

        # 1. Configure OPC UA server
        await self._setup_ua_server()

        # 2. Start OPC DA thread
        self.da_thread_active = True
        self.da_thread = threading.Thread(
            target=self._da_thread_main,
            args=(opc_server, opc_host, self._ifix_only_fcv, self._update_rate, self._include_descriptions, self._ifix_optimized, self._tag_refresh_interval, self._skip_initial_read),
            daemon=True
        )
        self.da_thread.start()

        # 3. Wait for tag discovery (from DA->Main queue)
        log.info(f"Waiting for OPC DA tag discovery (timeout: {self._discovery_timeout}s)...")
        tag_values = None
        timeout_seconds = self._discovery_timeout  # Timeout resets on each progress event
        import time
        last_activity = time.time()

        tag_descriptions = {}
        while True:
            try:
                event = self._da_to_main_queue.get(timeout=0.5)
                if event[0] == 'tags_discovered':
                    # Unpack tag_values and descriptions
                    tag_values, tag_descriptions = event[1]
                    log.info(f"Received {len(tag_values)} tags with values from DA thread")
                    if tag_descriptions:
                        log.info(f"Received {len(tag_descriptions)} tag descriptions")
                    break
                elif event[0] == 'progress':
                    # Reset timeout on progress
                    last_activity = time.time()
                    log.info(f"Progreso: {event[1]}")
                elif event[0] == 'error':
                    raise RuntimeError(f"OPC DA error: {event[1]}")
            except Empty:
                # Check for timeout only when no activity
                elapsed = time.time() - last_activity
                if elapsed > timeout_seconds:
                    raise RuntimeError(
                        f"Timeout esperando descubrimiento de tags OPC DA.\n"
                        f"Para servidores iFIX, use --ifix-optimized para descubrimiento rapido.\n"
                        f"Use --timeout para aumentar el tiempo de espera.\n"
                        f"Ultimo evento hace {elapsed:.0f} segundos."
                    )
                await asyncio.sleep(0.01)

        # 4. Create OPC UA nodes with initial values and descriptions
        await self._create_ua_nodes(tag_values, tag_descriptions)

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


def check_opc_wrapper_dll():
    """Check if the OPC wrapper DLL (gbda_aut.dll) is registered in the system"""
    try:
        import win32com.client
        # Try to create the GrayBox OPC DA Wrapper object
        win32com.client.Dispatch("Graybox.OPC.DAWrapper")
        return True
    except Exception:
        return False


def main():
    """Entry point for running the bridge"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description='OPC DA to OPC UA Bridge - Expone servidores OPC DA como OPC UA',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:
  %(prog)s --opc-server "Matrikon.OPC.Simulation.1"
      Conecta al servidor Matrikon en localhost y expone en puerto 4840

  %(prog)s --opc-server "MyServer.1" --opc-host 192.168.1.100
      Conecta a un servidor OPC DA remoto

  %(prog)s --opc-server "MyServer.1" --ua-endpoint "opc.tcp://0.0.0.0:4841/mybridge/"
      Usa un endpoint OPC UA personalizado

Notas:
  - Requiere que gbda_aut.dll este registrada en el sistema
  - El servidor OPC UA escucha en todas las interfaces (0.0.0.0)
  - Los clientes OPC UA pueden conectarse desde cualquier equipo en la red
  - Este ejecutable es de 32 bits, solo puede conectarse a servidores OPC DA de 32 bits
"""
    )
    parser.add_argument('--list-servers', action='store_true',
                        help='Listar servidores OPC DA disponibles y salir')
    parser.add_argument('--test-connection', action='store_true',
                        help='Probar conexion al servidor OPC DA y salir')
    parser.add_argument('--read-tag',
                        help='Leer un tag especifico y mostrar valor y propiedades (para diagnostico)')
    parser.add_argument('--opc-server',
                        help='Nombre del servidor OPC DA (ej: "Matrikon.OPC.Simulation.1")')
    parser.add_argument('--opc-host', default='localhost',
                        help='Host donde corre el servidor OPC DA (default: localhost)')
    parser.add_argument('--ua-endpoint', default='opc.tcp://0.0.0.0:4840/openopc2/',
                        help='Endpoint del servidor OPC UA (default: opc.tcp://0.0.0.0:4840/openopc2/)')
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='Nivel de log (default: INFO)')
    parser.add_argument('--ifix-only-fcv', action='store_true',
                        help='Solo exponer tags que terminan en .F_CV (valor actual de iFIX)')
    parser.add_argument('--timeout', type=int, default=300,
                        help='Timeout en segundos para descubrimiento de tags (default: 300, se reinicia con cada evento de progreso)')
    parser.add_argument('--update-rate', type=int, default=1000,
                        help='Tasa de actualizacion de suscripciones en ms (default: 1000)')
    parser.add_argument('--enable-writes', action='store_true',
                        help='Habilitar escritura desde clientes UA hacia OPC DA (PELIGROSO, deshabilitado por defecto)')
    parser.add_argument('--no-descriptions', action='store_true',
                        help='No leer descripciones de tags DA (inicio mas rapido)')
    parser.add_argument('--ifix-optimized', action='store_true',
                        help='Modo optimizado para iFIX: enumera contenedores y asume .F_CV (mucho mas rapido)')
    parser.add_argument('--tag-refresh-interval', type=int, default=600,
                        help='Intervalo de refresco de tags en segundos (default: 600 = 10 min, 0 para deshabilitar)')
    parser.add_argument('--discover', type=int, nargs='?', const=200, default=None,
                        help='Modo descubrimiento: lista los primeros N tags (default: 200) con info detallada y sale')
    parser.add_argument('--description-batch-size', type=int, default=500,
                        help='Tamaño de batch para lectura rapida de descripciones en iFIX (default: 500)')
    parser.add_argument('--benchmark-descriptions', type=int, nargs='?', const=2000, default=None,
                        help='Compara metodos de lectura de descripciones sobre N tags (default 2000) y sale')
    parser.add_argument('--skip-initial-read', action='store_true',
                        help='Saltar la lectura inicial de valores (acelera bootstrap ~13x). Los nodos UA arrancan con valor Null y StatusCode BadWaitingForInitialData; las suscripciones populan con los datos reales en la primera notificacion (~update_rate ms)')
    args = parser.parse_args()

    # Verificar que la DLL wrapper de OPC esta instalada
    if not check_opc_wrapper_dll():
        print("\nERROR: La libreria wrapper de OPC no esta instalada.")
        print('Para instalarla ejecute "regsvr32 gbda_aut.dll" en una linea de comando como administrador.')
        print("Si no cuenta con el archivo puede solicitarlo a pensur@pensur.com\n")
        sys.exit(1)

    # Configure logging - need to set level on the rich logger specifically
    logging.basicConfig(level=getattr(logging, args.log_level))
    # Also set level on the rich logger used by openopc2
    log.setLevel(getattr(logging, args.log_level))

    config = OpenOpcConfig()

    # Listar servidores si se solicita
    if args.list_servers:
        print("\nBuscando servidores OPC DA disponibles...")
        print("(Este ejecutable es de 32 bits, solo puede conectarse a servidores de 32 bits)\n")
        try:
            client = OpcDaClient(config)
            servers = client.servers(args.opc_host)
            if servers:
                print(f"Servidores OPC DA encontrados en '{args.opc_host}':")
                for server in servers:
                    print(f"  - {server}")
                print(f"\nTotal: {len(servers)} servidores")
            else:
                print("No se encontraron servidores OPC DA.")
        except Exception as e:
            print(f"Error al listar servidores: {e}")
        sys.exit(0)

    # Leer un tag específico para diagnóstico
    if hasattr(args, 'read_tag') and args.read_tag:
        if not args.opc_server:
            print("\nERROR: Debe especificar --opc-server")
            sys.exit(1)
        print(f"\nLeyendo tag '{args.read_tag}' de '{args.opc_server}'...")
        try:
            client = OpcDaClient(config)
            client.connect(args.opc_server, args.opc_host)

            # Leer valor
            result = client.read([args.read_tag])
            print(f"\nResultado de read():")
            for tag, value, quality, timestamp in result:
                print(f"  Tag: {tag}")
                print(f"  Value: {value}")
                print(f"  Python type: {type(value).__name__}")
                print(f"  Quality: {quality}")
                print(f"  Timestamp: {timestamp}")

            # Leer propiedades
            print(f"\nPropiedades del tag:")
            try:
                props = client.properties(args.read_tag)
                for prop_id, prop_name, prop_value in props:
                    print(f"  [{prop_id}] {prop_name}: {prop_value}")
            except Exception as e:
                print(f"  Error leyendo propiedades: {e}")

            client.close()
        except Exception as e:
            print(f"\nERROR: {e}")
            import traceback
            traceback.print_exc()
        sys.exit(0)

    # Probar conexión si se solicita
    if args.test_connection:
        if not args.opc_server:
            print("\nERROR: Debe especificar --opc-server para probar conexion")
            sys.exit(1)
        print(f"\nProbando conexion a '{args.opc_server}' en '{args.opc_host}'...")
        try:
            client = OpcDaClient(config)
            client.connect(args.opc_server, args.opc_host)
            print("Conexion exitosa!")
            print(f"Servidor: {client.info()}")
            print("\nListando tags (primeros 10)...")
            tags = client.list(flat=True, recursive=True)
            if not tags:
                tags = client.list(flat=False, recursive=True)
            print(f"Total tags: {len(tags)}")
            for tag in tags[:10]:
                print(f"  - {tag}")
            if len(tags) > 10:
                print(f"  ... y {len(tags) - 10} mas")
            client.close()
        except Exception as e:
            print(f"\nERROR de conexion: {e}")
            import traceback
            traceback.print_exc()
        sys.exit(0)

    # Modo descubrimiento - listar tags con info detallada
    if args.discover is not None:
        if not args.opc_server:
            print("\nERROR: Debe especificar --opc-server para modo descubrimiento")
            sys.exit(1)

        max_tags = args.discover
        print(f"\n{'='*70}")
        print(f"MODO DESCUBRIMIENTO - Listando primeros {max_tags} tags")
        print(f"{'='*70}")
        print(f"Servidor: {args.opc_server}")
        print(f"Host: {args.opc_host}")
        print(f"{'='*70}\n")

        try:
            import pythoncom
            pythoncom.CoInitialize()

            client = OpcDaClient(config)
            client.connect(args.opc_server, args.opc_host)
            print(f"Conectado: {client.info()}\n")

            # Obtener el browser para navegar la estructura
            browser = client._opc.opc_client.CreateBrowser()

            print("="*70)
            print("METODO 1: Navegacion con Browser (estructura jerarquica)")
            print("="*70)

            # Mostrar propiedades del browser
            print(f"\nPropiedades del Browser:")
            print(f"  Organization: {browser.Organization}")  # 1=hierarchical, 2=flat
            print(f"  Filter: '{browser.Filter}'")
            print(f"  DataType: {browser.DataType}")
            print(f"  AccessRights: {browser.AccessRights}")

            # Navegar desde la raiz
            browser.MoveToRoot()
            print(f"\nDesde la RAIZ:")
            browser.ShowBranches()
            branch_count = browser.Count
            print(f"  Ramas (branches): {branch_count}")
            branches = []
            for i in range(1, min(branch_count + 1, 21)):  # Max 20 ramas
                try:
                    branch = browser.Item(i)
                    branches.append(branch)
                    print(f"    [{i}] {branch}")
                except:
                    pass

            browser.ShowLeafs(True)  # True = flat
            leaf_count = browser.Count
            print(f"  Hojas (leafs) en raiz: {leaf_count}")
            for i in range(1, min(leaf_count + 1, 11)):  # Max 10 hojas
                try:
                    leaf = browser.Item(i)
                    print(f"    [{i}] {leaf}")
                except:
                    pass

            # Navegar dentro de las primeras ramas
            if branches:
                print(f"\nNavegando dentro de las primeras ramas:")
                for branch in branches[:5]:  # Max 5 ramas
                    try:
                        browser.MoveToRoot()
                        browser.MoveDown(branch)
                        current_pos = browser.CurrentPosition if hasattr(browser, 'CurrentPosition') else '?'

                        browser.ShowBranches()
                        sub_branches = browser.Count

                        browser.ShowLeafs(True)
                        sub_leafs = browser.Count

                        print(f"\n  >> {branch}:")
                        print(f"     Sub-ramas: {sub_branches}, Hojas: {sub_leafs}")

                        # Mostrar algunas hojas
                        if sub_leafs > 0:
                            browser.ShowLeafs(True)
                            for i in range(1, min(sub_leafs + 1, 6)):  # Max 5 hojas
                                try:
                                    leaf = browser.Item(i)
                                    item_id = browser.GetItemID(leaf)
                                    print(f"       Hoja[{i}]: {leaf}")
                                    print(f"              ItemID: {item_id}")
                                except Exception as e:
                                    print(f"       Hoja[{i}]: Error - {e}")

                        # Mostrar sub-ramas
                        if sub_branches > 0:
                            browser.ShowBranches()
                            for i in range(1, min(sub_branches + 1, 6)):  # Max 5 sub-ramas
                                try:
                                    sub_branch = browser.Item(i)
                                    print(f"       Rama[{i}]: {sub_branch}")
                                except:
                                    pass
                    except Exception as e:
                        print(f"\n  >> {branch}: Error navegando - {e}")

            print("\n" + "="*70)
            print("METODO 2: ilist() con flat=True")
            print("="*70)
            tag_count = 0
            for tag in client.ilist(flat=True, recursive=True):
                tag_count += 1
                if tag_count <= 20:
                    print(f"  [{tag_count}] {tag}")
                if tag_count >= max_tags:
                    break
            print(f"  ... Total con flat=True: {tag_count} tags")

            print("\n" + "="*70)
            print("METODO 3: ilist() con flat=False (jerarquico)")
            print("="*70)
            tag_count = 0
            sample_tags = []
            for tag in client.ilist(flat=False, recursive=True):
                tag_count += 1
                if tag_count <= max_tags:
                    print(f"  [{tag_count}] {tag}")
                    sample_tags.append(tag)
                if tag_count >= max_tags:
                    break
            print(f"  ... Total con flat=False: {tag_count} tags (limitado a {max_tags})")

            # Analizar estructura de los tags encontrados
            if sample_tags:
                print("\n" + "="*70)
                print("ANALISIS DE ESTRUCTURA")
                print("="*70)

                # Contar por sufijos
                suffixes = {}
                for tag in sample_tags:
                    parts = tag.rsplit('.', 1)
                    if len(parts) == 2:
                        suffix = '.' + parts[1]
                        suffixes[suffix] = suffixes.get(suffix, 0) + 1

                print("\nSufijos encontrados:")
                for suffix, count in sorted(suffixes.items(), key=lambda x: -x[1])[:20]:
                    print(f"  {suffix}: {count} tags")

                # Contar niveles de profundidad
                depths = {}
                for tag in sample_tags:
                    depth = tag.count('.')
                    depths[depth] = depths.get(depth, 0) + 1

                print("\nProfundidad (cantidad de puntos):")
                for depth, count in sorted(depths.items()):
                    print(f"  {depth} puntos: {count} tags")

                # Mostrar algunos tags que terminan en F_CV o A_CV
                fcv_tags = [t for t in sample_tags if t.endswith('.F_CV')]
                acv_tags = [t for t in sample_tags if t.endswith('.A_CV')]

                if fcv_tags:
                    print(f"\nEjemplos de tags .F_CV ({len(fcv_tags)} encontrados):")
                    for tag in fcv_tags[:5]:
                        print(f"  {tag}")

                if acv_tags:
                    print(f"\nEjemplos de tags .A_CV ({len(acv_tags)} encontrados):")
                    for tag in acv_tags[:5]:
                        print(f"  {tag}")

                # Intentar leer algunos tags
                print("\n" + "="*70)
                print("LECTURA DE VALORES (primeros 5 tags)")
                print("="*70)
                test_tags = sample_tags[:5]
                try:
                    results = client.read(test_tags)
                    for tag, value, quality, timestamp in results:
                        print(f"\n  Tag: {tag}")
                        print(f"    Value: {value}")
                        print(f"    Type: {type(value).__name__}")
                        print(f"    Quality: {quality}")
                except Exception as e:
                    print(f"  Error leyendo: {e}")

            client.close()
            pythoncom.CoUninitialize()

            print("\n" + "="*70)
            print("FIN DEL MODO DESCUBRIMIENTO")
            print("="*70 + "\n")

        except Exception as e:
            print(f"\nERROR: {e}")
            import traceback
            traceback.print_exc()
        sys.exit(0)

    # Modo benchmark de descripciones
    if args.benchmark_descriptions is not None:
        if not args.opc_server:
            print("\nERROR: --opc-server requerido para benchmark")
            sys.exit(1)
        sample_size = args.benchmark_descriptions
        print(f"\n{'='*70}")
        print(f"BENCHMARK DESCRIPCIONES — sample objetivo: {sample_size} tags")
        print(f"{'='*70}\n")

        try:
            import pythoncom, time as _time
            pythoncom.CoInitialize()
            client = OpcDaClient(config)
            client.connect(args.opc_server, args.opc_host)
            print(f"Conectado a {args.opc_server}")

            # Reuse the bridge's discovery so results are realistic
            bench_bridge = OpcDaUaBridge(config)
            bench_bridge.da_client = client

            print("\n[1/3] Descubriendo tags...")
            disc_start = _time.time()
            if args.ifix_optimized:
                tags = bench_bridge._discover_tags_ifix_optimized()
            else:
                tags = bench_bridge._discover_tags_standard(args.ifix_only_fcv)
            print(f"      {len(tags)} tags en {_time.time() - disc_start:.1f}s")

            # Filter to value tags suitable for fast path
            value_tags = [t for t in tags if t.endswith('.F_CV') or t.endswith('.A_CV')]
            if not value_tags:
                value_tags = tags

            # Pick spread sample
            if len(value_tags) > sample_size:
                step = max(1, len(value_tags) // sample_size)
                sample = value_tags[::step][:sample_size]
            else:
                sample = value_tags
            print(f"      Sample efectivo: {len(sample)} tags\n")

            # Slow path: GetItemProperties
            print("[2/3] Metodo lento: GetItemProperties (1 llamada COM por tag)...")
            DESCRIPTION_PROPERTY_ID = 101
            opc_client = client._opc.opc_client
            slow_start = _time.time()
            slow_found = 0
            for tag in sample:
                try:
                    prop_values, _errs = opc_client.GetItemProperties(
                        tag, 1, [0, DESCRIPTION_PROPERTY_ID]
                    )
                    if prop_values and len(prop_values) > 0 and prop_values[0]:
                        if str(prop_values[0]).strip():
                            slow_found += 1
                except Exception:
                    pass
            slow_elapsed = _time.time() - slow_start
            slow_rate = len(sample) / slow_elapsed if slow_elapsed > 0 else 0
            print(f"      {len(sample)} tags en {slow_elapsed:.2f}s "
                  f"({slow_rate:.0f} tags/s, {slow_found} encontradas)\n")

            # Fast path at multiple batch sizes
            print("[3/3] Metodo rapido: lectura batch de .A_DESC")
            desc_to_orig = {}
            desc_tags = []
            for tag in sample:
                for suffix in ('.F_CV', '.A_CV'):
                    if tag.endswith(suffix):
                        dt = tag[:-len(suffix)] + '.A_DESC'
                        desc_to_orig[dt] = tag
                        desc_tags.append(dt)
                        break
            print(f"      {len(desc_tags)} desc-tags derivados\n")

            results = []
            for bs in [100, 200, 500, 1000, 2000, 5000]:
                if bs > len(desc_tags) and results:
                    break
                actual_bs = min(bs, len(desc_tags))
                fast_start = _time.time()
                fast_found = 0
                err = None
                try:
                    for i in range(0, len(desc_tags), actual_bs):
                        chunk = desc_tags[i:i + actual_bs]
                        read_results = client.read(chunk)
                        for _dt, value, quality, _ in read_results:
                            if quality in ('Good', 'Uncertain') and value is not None:
                                if str(value).strip():
                                    fast_found += 1
                except Exception as e:
                    err = str(e)
                fast_elapsed = _time.time() - fast_start
                if err:
                    print(f"      batch={actual_bs:>5}: ERROR — {err}")
                    continue
                rate = len(desc_tags) / fast_elapsed if fast_elapsed > 0 else 0
                speedup = slow_elapsed / fast_elapsed if fast_elapsed > 0 else 0
                results.append((actual_bs, fast_elapsed, rate, fast_found, speedup))
                print(f"      batch={actual_bs:>5}: {fast_elapsed:>6.2f}s "
                      f"({rate:>7.0f} tags/s, {fast_found} encontradas, {speedup:>5.1f}x mas rapido)")

            print(f"\n{'='*70}")
            if results:
                best = min(results, key=lambda r: r[1])
                projected_full = (len(value_tags) / best[2]) if best[2] > 0 else 0
                projected_slow = (len(value_tags) / slow_rate) if slow_rate > 0 else 0
                print(f"MEJOR batch_size: {best[0]} ({best[1]:.2f}s en sample, "
                      f"{best[2]:.0f} tags/s, {best[4]:.1f}x speedup)")
                print(f"Proyeccion para los {len(value_tags)} tags totales:")
                print(f"  - Lento:  {projected_slow:.1f}s ({projected_slow/60:.1f} min)")
                print(f"  - Rapido: {projected_full:.1f}s ({projected_full/60:.1f} min)")
                print(f"\nRecomendacion: --description-batch-size {best[0]}")
            print(f"{'='*70}\n")

            client.close()
            pythoncom.CoUninitialize()
        except Exception as e:
            print(f"\nERROR en benchmark: {e}")
            import traceback
            traceback.print_exc()
        sys.exit(0)

    # Verificar que se especificó el servidor
    if not args.opc_server:
        print("\nERROR: Debe especificar --opc-server o usar --list-servers")
        print("Use --help para ver las opciones disponibles\n")
        sys.exit(1)

    if args.opc_server:
        config.OPC_SERVER = args.opc_server
    config.OPC_HOST = args.opc_host

    bridge = OpcDaUaBridge(config)
    bridge.set_endpoint(args.ua_endpoint)
    bridge.set_discovery_timeout(args.timeout)
    bridge.set_update_rate(args.update_rate)
    bridge.set_enable_writes(args.enable_writes)
    bridge.set_include_descriptions(not args.no_descriptions)
    bridge.set_ifix_optimized(args.ifix_optimized)
    bridge.set_tag_refresh_interval(args.tag_refresh_interval)
    bridge.set_description_batch_size(args.description_batch_size)
    bridge.set_skip_initial_read(args.skip_initial_read)

    if args.ifix_optimized:
        print("\n*** Modo iFIX optimizado: enumerando contenedores y asumiendo .F_CV ***")
        print(f"*** Descripciones via .A_DESC en batches de {args.description_batch_size} ***")

    if args.skip_initial_read:
        print("*** Lectura inicial deshabilitada: nodos arrancan en Null hasta primera suscripcion ***\n")
    elif args.ifix_optimized:
        print()

    if args.tag_refresh_interval > 0:
        print(f"*** Refresco de tags cada {args.tag_refresh_interval} segundos ({args.tag_refresh_interval // 60} minutos) ***\n")

    if args.enable_writes:
        print("\n*** ADVERTENCIA: Escrituras habilitadas. Los clientes UA pueden modificar valores en OPC DA ***\n")
    else:
        print("*** Bridge en modo SOLO LECTURA (escrituras a OPC DA deshabilitadas) ***\n")

    try:
        asyncio.run(bridge.start(args.opc_server, args.opc_host, ifix_only_fcv=args.ifix_only_fcv))
    except KeyboardInterrupt:
        bridge.stop()
        print("\nBridge stopped.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OPC DA to OPC UA Bridge - Legacy Edition

Compatible with Windows XP / Server 2003 using Python 2.7.
Uses OpenOPC (sourceforge) for OPC DA and python-opcua for OPC UA.

Read-only bridge: reads OPC DA tags and exposes them as OPC UA nodes.

Usage:
    python opcda2ua_legacy.py -s "Matrikon.OPC.Simulation"
    python opcda2ua_legacy.py -s "MyServer" -p 4840 -i 1 -n -r 10
"""
from __future__ import print_function

import sys
import time
import argparse
import logging
import threading

try:
    import OpenOPC
except ImportError:
    print("ERROR: OpenOPC is required. Install from https://openopc.sourceforge.net/")
    sys.exit(1)

try:
    from opcua import Server, ua
except ImportError:
    print("ERROR: python-opcua is required. Install with: pip install opcua==0.98.13")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger("opcda2ua")

# OPC DA quality string to OPC UA StatusCode mapping
DA_QUALITY_TO_UA = {
    'Good': ua.StatusCodes.Good,
    'Uncertain': ua.StatusCodes.Uncertain,
    'Bad': ua.StatusCodes.Bad,
    'Unknown': ua.StatusCodes.Bad,
    'Error': ua.StatusCodes.Bad,
}


def map_quality(quality_str):
    """Map OPC DA quality string to UA StatusCode."""
    if quality_str is None:
        return ua.StatusCodes.Bad
    for key in ('Good', 'Uncertain', 'Bad'):
        if quality_str.startswith(key):
            return DA_QUALITY_TO_UA[key]
    return ua.StatusCodes.Bad


def parse_args():
    parser = argparse.ArgumentParser(
        description="OPC DA to OPC UA Bridge (Legacy - Python 2.7)"
    )
    parser.add_argument(
        "-s", "--server",
        required=True,
        help="OPC DA server name (e.g. 'Matrikon.OPC.Simulation')"
    )
    parser.add_argument(
        "-H", "--host",
        default="localhost",
        help="OPC DA server host (default: localhost)"
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=4840,
        help="OPC UA server port (default: 4840)"
    )
    parser.add_argument(
        "-i", "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)"
    )
    parser.add_argument(
        "-b", "--bind",
        default="0.0.0.0",
        help="OPC UA server bind address (default: 0.0.0.0)"
    )
    parser.add_argument(
        "-f", "--filter",
        default=None,
        help="OPC DA branch/folder to list. "
             "If not specified, lists all tags recursively."
    )
    parser.add_argument(
        "-n", "--no-properties",
        action="store_true",
        help="Exclude tag properties (e.g. .formato, .unidad). "
             "Keeps only base tag values."
    )
    parser.add_argument(
        "-l", "--list-branches",
        action="store_true",
        help="List available OPC DA branches and exit"
    )
    parser.add_argument(
        "-r", "--refresh",
        type=int,
        default=0,
        help="Re-discover tags every N minutes (add new, remove old). "
             "0 = disabled (default)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging"
    )
    parser.add_argument(
        "--smart",
        action="store_true",
        help="Filtro para filtro por convencion. Tags con .medida: expone "
             "solo X.medida (descarta X.*). Tags con .velocidad: expone "
             "X.velocidad, X.horasMarcha, X.alarmas, X.velocidadOp, "
             "X.velocidadProg. En ambos casos X.descripcion se lee como "
             "descripcion UA del nodo padre X. Tags con prefijo comun "
             "se agrupan en carpetas."
    )
    parser.add_argument(
        "--ifix",
        action="store_true",
        help="Filtro para servidores iFIX. Solo expone tags que terminan "
             "en .F_CV (valor numerico) o .A_CV (texto, bloques TX). "
             "Descarta todas las demas propiedades (.A_DESC, .F_HI, etc). "
             "Tags con prefijo comun se agrupan en carpetas."
    )
    return parser.parse_args()


class LegacyBridge(object):
    """
    OPC DA to OPC UA bridge for legacy systems.

    Threads:
      - Main: OPC UA server
      - Poll: reads OPC DA values and updates UA nodes (own OPC connection)
      - Refresh: re-discovers tags periodically, adds/removes UA nodes,
                 reads descriptions (own OPC connection, does not block polling)
    """

    def __init__(self, da_server, da_host="localhost", ua_port=4840,
                 ua_bind="0.0.0.0", poll_interval=1.0, tag_filter=None,
                 no_properties=False, refresh_minutes=0,
                 mode=None):
        self.da_server = da_server
        self.da_host = da_host
        self.ua_port = ua_port
        self.ua_bind = ua_bind
        self.poll_interval = poll_interval
        self.tag_filter = tag_filter
        self.no_properties = no_properties
        self.refresh_minutes = refresh_minutes
        self.mode = mode  # None, 'smart', or 'ifix'

        self.opc = None          # OpenOPC client (main thread, used for startup)
        self.ua_server = None    # python-opcua server
        self.ua_nodes = {}       # da_tag_name -> UA Node
        self.ua_idx = 0          # UA namespace index
        self.da_folder = None    # UA folder for DA tags
        self._ua_folders = {}    # group_name -> UA Folder (for hierarchical grouping)
        self._energy_bases = {}  # base -> (energy_group, suffix) for energy meters
        self._stop_event = threading.Event()
        self._lock = threading.Lock()  # protects ua_nodes and node operations
        self._tags_changed = threading.Event()  # signals poll thread to reload tag list

    # ------------------------------------------------------------------
    # Tag discovery & filtering
    # ------------------------------------------------------------------

    # Smart: properties to keep for .velocidad objects
    _VELOCIDAD_PROPS = frozenset([
        'velocidad', 'horasMarcha', 'alarmas', 'velocidadOp', 'velocidadProg',
    ])


    def _deduplicate_tags(self, tags):
        """Remove duplicate tags that appear under multiple OPC DA branches.

        WinCC exposes the same tags under multiple paths, e.g.:
          MYSERVER_SERVIDOR01::FAB_X.medida
          List of all tags::FAB_X.medida
          FAB_X.medida
        We keep only the version without '::' prefix (shortest path).
        If only prefixed versions exist, keep one copy with the prefix.
        """
        seen_base = {}  # base_name -> full_tag
        for t in tags:
            idx = t.find('::')
            if idx >= 0:
                base = t[idx + 2:]
            else:
                base = t
            # Prefer the version without prefix
            if base not in seen_base:
                seen_base[base] = t
            elif '::' in seen_base[base] and '::' not in t:
                # Replace prefixed version with non-prefixed
                seen_base[base] = t

        result = list(seen_base.values())
        if len(result) < len(tags):
            log.info("Deduplicated tags: %d -> %d", len(tags), len(result))
        return result

    def _filter_tags(self, tags):
        """Apply tag filtering based on mode and flags.

        Modes:
          None        - standard filtering (@ prefix, --no-properties)
          'smart' - keep .medida tags (solo value), .velocidad objects
                         (selected properties), discard all other properties
          'ifix'       - keep only .F_CV and .A_CV tags
        """
        before = len(tags)
        tags = [t for t in tags if '@' not in t and not t.startswith('#')]

        # Deduplicate tags from multiple WinCC branches
        tags = self._deduplicate_tags(tags)

        if self.mode == 'smart':
            tags = self._filter_smart(tags)
        elif self.mode == 'ifix':
            tags = self._filter_ifix(tags)
        elif self.no_properties:
            filtered = []
            for t in tags:
                idx = t.find('::')
                tag_part = t[idx + 2:] if idx >= 0 else t
                if '.' not in tag_part:
                    filtered.append(t)
            tags = filtered

        # Sort alphabetically so UA nodes appear in order
        tags.sort()
        log.info("Filtered tags: %d -> %d", before, len(tags))
        return tags

    def _filter_smart(self, tags):
        """Smart mode: smart filtering for suffix-convention filtering.

        Rules:
          1. ALL bases ending in _I are discarded (description-only).
          2. Tags with .medida: keep only X.medida -> simple node X.
          3. Tags with .velocidad: keep selected properties -> folder X.
          4. Energy meters: if any base ends in _Ir or _I_R, the prefix
             is an energy group. ALL bases starting with that prefix
             are grouped under a folder.
          5. Discard all other dotted properties (formato, unidad, etc.)
          6. .descripcion never kept (read separately for UA attr).
          7. Standalone tags (no dot): keep unless they end in _I or
             share a root with a medida/velocidad/energy base.
        """
        # First pass: catalog all names
        has_medida = set()     # dotted bases with .medida
        has_velocidad = set()  # dotted bases with .velocidad
        known_bases = set()    # all dotted bases
        standalone = set()     # tags without dot

        for t in tags:
            dot = t.rfind('.')
            if dot < 0:
                standalone.add(t)
                continue
            base = t[:dot]
            prop = t[dot + 1:]
            known_bases.add(base)
            if prop == 'medida':
                has_medida.add(base)
            elif prop == 'velocidad':
                has_velocidad.add(base)

        # Discover energy group prefixes from ALL names (dotted + standalone)
        _ENERGY_TRIGGERS = (
            '_Ir', '_Is', '_It',
            '_I_R', '_I_S', '_I_T',
            '_Urs', '_Ust', '_Utr',
            '_U_RS', '_U_ST', '_U_TR',
            '_P', '_Q',
        )
        all_names = has_medida | standalone
        energy_prefixes = set()
        for name in all_names:
            if name.endswith('_I'):
                continue
            for sfx in _ENERGY_TRIGGERS:
                if name.endswith(sfx):
                    energy_prefixes.add(name[:-len(sfx)])
                    break

        # Map ALL names that start with an energy prefix into groups
        # base/tag -> (group_prefix, suffix_label)
        self._energy_bases = {}
        for name in all_names:
            if name.endswith('_I'):
                continue
            for prefix in energy_prefixes:
                if name.startswith(prefix + '_') and name != prefix:
                    suffix = name[len(prefix) + 1:]
                    self._energy_bases[name] = (prefix, suffix)
                    break

        # Count _I bases for logging
        i_count = sum(1 for b in known_bases if b.endswith('_I'))

        # Build set of "occupied" names for standalone dedup
        occupied = has_medida | has_velocidad
        for prefix in energy_prefixes:
            occupied.add(prefix)

        # Second pass: filter
        filtered = []
        standalone_kept = 0
        for t in tags:
            dot = t.rfind('.')
            if dot < 0:
                # Standalone tag (no dot)
                if t.endswith('_I'):
                    continue
                # Energy component: keep it (will be grouped in folder)
                if t in self._energy_bases:
                    filtered.append(t)
                    standalone_kept += 1
                    continue
                # Skip if shares root with an occupied base
                if t in occupied:
                    continue
                shares_root = False
                pos = len(t)
                while pos > 0:
                    pos = t.rfind('_', 0, pos)
                    if pos < 0:
                        break
                    if t[:pos] in occupied:
                        shares_root = True
                        break
                if shares_root:
                    continue
                filtered.append(t)
                standalone_kept += 1
                continue

            base = t[:dot]
            prop = t[dot + 1:]

            # Skip .descripcion (read separately for UA attr)
            if prop == 'descripcion':
                continue

            # Skip ALL _I bases entirely
            if base.endswith('_I'):
                continue

            if base in has_medida:
                if prop == 'medida':
                    filtered.append(t)
            elif base in has_velocidad:
                if prop in self._VELOCIDAD_PROPS:
                    filtered.append(t)

        log.info("Smart filter: %d medida (%d energy in %d groups), "
                 "%d velocidad, %d standalone, %d _I skipped",
                 len(has_medida), len(self._energy_bases),
                 len(energy_prefixes), len(has_velocidad),
                 standalone_kept, i_count)
        return filtered

    def _filter_ifix(self, tags):
        """iFIX mode: keep only .F_CV and .A_CV tags."""
        filtered = [t for t in tags
                    if t.endswith('.F_CV') or t.endswith('.A_CV')]
        return filtered

    def _discover_with_client(self, opc_client):
        """Discover and filter tags using the given OPC client."""
        if self.tag_filter:
            try:
                tags = opc_client.list(self.tag_filter, flat=True)
            except Exception as e:
                log.error("Could not list branch '%s': %s", self.tag_filter, e)
                return []
        else:
            try:
                tags = opc_client.list('*', recursive=True, flat=True)
            except Exception:
                tags = []
                try:
                    branches = opc_client.list()
                    for branch in branches:
                        try:
                            items = opc_client.list(branch, flat=True)
                            tags.extend(items)
                        except Exception:
                            pass
                except Exception:
                    pass
        return self._filter_tags(tags)

    # ------------------------------------------------------------------
    # OPC DA helpers
    # ------------------------------------------------------------------

    def _read_batch(self, opc_client, tags):
        """Read a batch of tags. Returns list of (tag, value, quality, ts)."""
        if not tags:
            return []
        try:
            results = opc_client.read(tags, sync=True)
            if isinstance(results, list):
                return results
            val, qual, ts = results
            return [(tags[0], val, qual, ts)]
        except Exception as e:
            log.error("Error reading tags: %s", e)
            return []

    def _read_descriptions(self, opc_client, tags):
        """Read descriptions for tags.

        In smart mode: reads X_I.descripcion for each base object X
            (the _I suffix variant provides the description in WinCC).
            Returns dict keyed by the full DA tag name (e.g. X.medida)
            so the caller can map it to the correct UA node.
        In ifix mode: no description tags to read.
        Default: reads tag.descripcion for each tag.

        Returns dict {key: description_string} where key is:
          - full DA tag name (smart mode)
          - tag name (default mode)
        """
        desc_map = {}

        if self.mode == 'smart':
            # Collect all names that need X_I.descripcion:
            # - dotted tags: base is the part before the dot
            # - standalone tags: the tag name itself is the base
            base_to_tags = {}
            for t in tags:
                dot = t.rfind('.')
                if dot > 0:
                    base = t[:dot]
                else:
                    base = t  # standalone tag
                base_to_tags.setdefault(base, []).append(t)

            if not base_to_tags:
                return desc_map

            # Read X_I.descripcion for each base X
            bases = list(base_to_tags.keys())
            desc_tags = [b + '_I.descripcion' for b in bases]

            for i in range(0, len(desc_tags), 100):
                batch_desc = desc_tags[i:i + 100]
                batch_bases = bases[i:i + 100]
                try:
                    results = opc_client.read(batch_desc, sync=True)
                    if not isinstance(results, list):
                        val, qual, ts = results
                        results = [(batch_desc[0], val, qual, ts)]
                    for j, (_dtag, value, quality, _ts) in enumerate(results):
                        if value and quality and quality.startswith('Good'):
                            if isinstance(value, bytes):
                                value = value.decode('utf-8', errors='replace')
                            if value and str(value).strip():
                                desc_str = str(value).strip()
                                for tag in base_to_tags[batch_bases[j]]:
                                    desc_map[tag] = desc_str
                except Exception:
                    pass

            return desc_map

        if self.mode == 'ifix':
            # iFIX: no .descripcion convention, skip
            return desc_map

        # Default mode: read tag.descripcion for each tag
        desc_tags = []
        base_tags = []
        for t in tags:
            desc_tags.append(t + '.descripcion')
            base_tags.append(t)

        for i in range(0, len(desc_tags), 100):
            batch_desc = desc_tags[i:i + 100]
            batch_base = base_tags[i:i + 100]
            try:
                results = opc_client.read(batch_desc, sync=True)
                if not isinstance(results, list):
                    val, qual, ts = results
                    results = [(batch_desc[0], val, qual, ts)]
                for j, (_dtag, value, quality, _ts) in enumerate(results):
                    if value and quality and quality.startswith('Good'):
                        if isinstance(value, bytes):
                            value = value.decode('utf-8', errors='replace')
                        if value and str(value).strip():
                            desc_map[base_tags[i + j]] = str(value).strip()
            except Exception:
                pass

        return desc_map

    def _apply_descriptions(self, descs):
        """Apply description map to UA nodes.

        In smart mode: descriptions are keyed by DA tag name.
          - For .medida tags: description goes on the node itself
          - For .velocidad tags: description goes on the folder
        Default mode: descriptions are keyed by tag name, applied to nodes.
        """
        if self.mode == 'smart':
            seen_folders = set()
            for tag, desc in descs.items():
                dot = tag.rfind('.')
                if dot > 0:
                    base = tag[:dot]
                    prop = tag[dot + 1:]
                    if prop == 'medida':
                        # Description goes on the simple node
                        if tag in self.ua_nodes:
                            self._set_node_description(self.ua_nodes[tag], desc)
                    elif base not in seen_folders and base in self._ua_folders:
                        # Description goes on the folder (once per folder)
                        self._set_node_description(self._ua_folders[base], desc)
                        seen_folders.add(base)
                else:
                    # Standalone tag: description goes on the node
                    if tag in self.ua_nodes:
                        self._set_node_description(self.ua_nodes[tag], desc)
        else:
            for tag, desc in descs.items():
                if tag in self.ua_nodes:
                    self._set_node_description(self.ua_nodes[tag], desc)

    def _set_node_description(self, node, description):
        """Set the Description attribute on a UA node."""
        try:
            node.set_attribute(
                ua.AttributeIds.Description,
                ua.DataValue(ua.Variant(
                    ua.LocalizedText(description),
                    ua.VariantType.LocalizedText
                ))
            )
        except Exception as e:
            log.debug("Could not set description: %s", e)

    # ------------------------------------------------------------------
    # UA server setup
    # ------------------------------------------------------------------

    def connect_da(self):
        """Connect to OPC DA server via OpenOPC (main thread)."""
        log.info("Connecting to OPC DA server '%s' on %s...",
                 self.da_server, self.da_host)
        self.opc = OpenOPC.client()
        self.opc.connect(self.da_server, self.da_host)
        log.info("Connected to OPC DA server '%s'", self.da_server)

    def discover_tags(self):
        """Discover tags using main thread OPC connection."""
        log.info("Discovering tags...")
        tags = self._discover_with_client(self.opc)
        log.info("Discovered %d tags", len(tags))
        return tags

    def setup_ua_server(self):
        """Initialize and configure the OPC UA server, including the tags folder."""
        endpoint = "opc.tcp://%s:%d/opcda2ua/" % (self.ua_bind, self.ua_port)
        log.info("Initializing OPC UA server...")
        t0 = time.time()
        self.ua_server = Server()
        log.info("OPC UA server initialized in %.1f seconds", time.time() - t0)
        self.ua_server.set_endpoint(endpoint)
        self.ua_server.set_server_name("OpenOPC DA-UA Bridge (Legacy)")
        uri = "http://openopc.bridge.legacy"
        self.ua_idx = self.ua_server.register_namespace(uri)
        objects = self.ua_server.get_objects_node()
        self.da_folder = objects.add_folder(self.ua_idx, "OpcDaTags")

    def _get_or_create_folder(self, group_name):
        """Get existing UA folder for group or create it under da_folder."""
        if group_name in self._ua_folders:
            return self._ua_folders[group_name]
        try:
            folder_name = group_name.encode('utf-8') if isinstance(
                group_name, unicode) else str(group_name)
            folder = self.da_folder.add_folder(self.ua_idx, folder_name)
            self._ua_folders[group_name] = folder
            return folder
        except Exception as e:
            log.debug("Could not create folder '%s': %s", group_name, e)
            return self.da_folder

    def _parse_tag_group(self, tag):
        """Extract group name and leaf name from a tag.

        For smart mode:
          X.medida -> (None, X) -- simple node named X (no folder)
          X_Ir.medida -> (X, Ir) -- energy meter folder X / variable Ir
          X.velocidad -> (X, velocidad) -- folder X / variable velocidad
        For ifix mode:
          X.F_CV -> (X, F_CV)
        Default: (None, tag)

        Returns (group, leaf) or (None, tag) if no grouping applies.
        """
        if self.mode is None:
            return None, tag

        dot = tag.rfind('.')
        if dot > 0:
            base = tag[:dot]
            prop = tag[dot + 1:]
            if self.mode == 'smart' and prop == 'medida':
                # Check energy meter: base in _energy_bases -> folder
                if base in self._energy_bases:
                    energy_group, suffix_label = self._energy_bases[base]
                    return energy_group, suffix_label
                # Regular .medida: simple node named after the base
                return None, base
            return base, prop

        # Standalone tag (no dot)
        if self.mode == 'smart' and tag in self._energy_bases:
            energy_group, suffix_label = self._energy_bases[tag]
            return energy_group, suffix_label
        return None, tag

    def _add_ua_node(self, tag, value):
        """Create a UA node, grouped into folders when mode is set.

        In smart/ifix mode, tags sharing a common prefix before '.'
        are grouped under a folder node. E.g.:
          TG_BombaCon2.velocidad -> folder TG_BombaCon2 / variable velocidad
          TG_BombaCon2.alarmas   -> folder TG_BombaCon2 / variable alarmas
        """
        try:
            if value is None:
                value = 0

            group, leaf = self._parse_tag_group(tag)

            if group is not None:
                parent = self._get_or_create_folder(group)
                node_name = leaf.encode('utf-8') if isinstance(
                    leaf, unicode) else str(leaf)
            else:
                parent = self.da_folder
                node_name = tag.encode('utf-8') if isinstance(
                    tag, unicode) else str(tag)

            node = parent.add_variable(self.ua_idx, node_name, value)
            self.ua_nodes[tag] = node
            return node
        except Exception as e:
            log.warning("Could not create UA node for '%s': %s", tag, e)
            return None

    # ------------------------------------------------------------------
    # Poll thread (value updates only - never blocks for discovery)
    # ------------------------------------------------------------------

    def _poll_loop(self):
        """Background thread: bootstraps tags then polls OPC DA values.

        Phase 1 (bootstrap): connect to DA, discover tags, read initial
        values and create UA nodes progressively so the UA server is
        browsable as soon as possible.
        Phase 2 (poll): continuously read DA values and update UA nodes.
        """
        import pythoncom
        pythoncom.CoInitialize()

        BATCH_SIZE = 100

        try:
            poll_opc = OpenOPC.client()
            poll_opc.connect(self.da_server, self.da_host)
            log.info("Poll thread: connected to OPC DA server '%s'",
                     self.da_server)
        except Exception as e:
            log.error("Poll thread: could not connect: %s", e)
            pythoncom.CoUninitialize()
            return

        # --- Phase 1: discover and create nodes progressively ---
        log.info("Poll thread: discovering tags...")
        tags = self._discover_with_client(poll_opc)
        if not tags:
            log.error("Poll thread: no tags found")
            poll_opc.close()
            pythoncom.CoUninitialize()
            return
        log.info("Poll thread: discovered %d tags, reading values...", len(tags))

        t0 = time.time()
        created = 0
        for i in range(0, len(tags), BATCH_SIZE):
            if self._stop_event.is_set():
                break
            batch = tags[i:i + BATCH_SIZE]
            results = self._read_batch(poll_opc, batch)
            with self._lock:
                for tag, value, quality, _ts in results:
                    if self._add_ua_node(tag, value) is not None:
                        created += 1
            log.info("Poll thread: created %d/%d UA nodes...",
                     created, len(tags))

        elapsed = time.time() - t0
        log.info("Poll thread: bootstrap complete - %d nodes in %.1fs",
                 created, elapsed)

        # Read descriptions in background (non-blocking for polling)
        try:
            read_tags_list = list(self.ua_nodes.keys())
            descs = self._read_descriptions(poll_opc, read_tags_list)
            if descs:
                with self._lock:
                    self._apply_descriptions(descs)
                log.info("Poll thread: set %d descriptions", len(descs))
        except Exception as e:
            log.debug("Poll thread: could not read descriptions: %s", e)

        # --- Phase 2: poll loop ---
        tag_list = list(self.ua_nodes.keys())
        log.info("Poll thread: polling %d tags every %.1fs",
                 len(tag_list), self.poll_interval)

        try:
            while not self._stop_event.is_set():
                # Reload tag list if refresh thread changed it
                if self._tags_changed.is_set():
                    with self._lock:
                        tag_list = list(self.ua_nodes.keys())
                    self._tags_changed.clear()
                    log.info("Poll thread: tag list reloaded (%d tags)",
                             len(tag_list))

                # Read all tags in batches
                try:
                    for i in range(0, len(tag_list), BATCH_SIZE):
                        if self._stop_event.is_set():
                            break
                        batch = tag_list[i:i + BATCH_SIZE]
                        try:
                            results = poll_opc.read(batch, sync=True)
                            if not isinstance(results, list):
                                val, qual, ts = results
                                results = [(batch[0], val, qual, ts)]
                        except Exception as e:
                            log.error("Error reading batch %d: %s",
                                      i // BATCH_SIZE, e)
                            results = []

                        with self._lock:
                            for tag, value, quality, _ts in results:
                                if tag in self.ua_nodes and value is not None:
                                    try:
                                        status = map_quality(quality)
                                        dv = ua.DataValue(ua.Variant(value))
                                        dv.StatusCode = ua.StatusCode(status)
                                        self.ua_nodes[tag].set_data_value(dv)
                                    except Exception as e:
                                        log.debug("Could not update '%s': %s",
                                                  tag, e)
                except Exception as e:
                    log.error("Poll error: %s", e)

                self._stop_event.wait(self.poll_interval)
        finally:
            try:
                poll_opc.close()
            except Exception:
                pass
            pythoncom.CoUninitialize()
            log.info("Poll thread: stopped")

    # ------------------------------------------------------------------
    # Refresh thread (async discovery - does NOT block polling)
    # ------------------------------------------------------------------

    def _refresh_loop(self):
        """Background thread: periodically re-discovers tags."""
        import pythoncom
        pythoncom.CoInitialize()

        try:
            refresh_opc = OpenOPC.client()
            refresh_opc.connect(self.da_server, self.da_host)
            log.info("Refresh thread: connected to OPC DA")
        except Exception as e:
            log.error("Refresh thread: could not connect: %s", e)
            pythoncom.CoUninitialize()
            return

        interval_sec = self.refresh_minutes * 60

        try:
            while not self._stop_event.is_set():
                # Sleep first (initial discovery already done at startup)
                self._stop_event.wait(interval_sec)
                if self._stop_event.is_set():
                    break

                log.info("Refresh thread: re-discovering tags...")
                t0 = time.time()

                try:
                    current_tags = set(self._discover_with_client(refresh_opc))
                except Exception as e:
                    log.error("Refresh thread: discovery failed: %s", e)
                    continue

                if not current_tags:
                    log.warning("Refresh thread: got 0 tags, skipping")
                    continue

                with self._lock:
                    existing_tags = set(self.ua_nodes.keys())

                new_tags = current_tags - existing_tags
                removed_tags = existing_tags - current_tags

                # Remove stale nodes
                if removed_tags:
                    with self._lock:
                        for tag in removed_tags:
                            node = self.ua_nodes.pop(tag, None)
                            if node:
                                try:
                                    self.ua_server.delete_nodes([node], recursive=True)
                                except Exception:
                                    pass
                    log.info("Refresh thread: removed %d stale tags", len(removed_tags))

                # Add new nodes
                if new_tags:
                    new_list = list(new_tags)
                    added = 0
                    for i in range(0, len(new_list), 100):
                        batch = new_list[i:i + 100]
                        results = self._read_batch(refresh_opc, batch)
                        with self._lock:
                            for tag, value, quality, _ts in results:
                                if self._add_ua_node(tag, value) is not None:
                                    added += 1

                    # Read descriptions for new tags
                    if added > 0:
                        try:
                            descs = self._read_descriptions(
                                refresh_opc, new_list[:added])
                            if descs:
                                with self._lock:
                                    self._apply_descriptions(descs)
                                log.info("Refresh thread: set %d descriptions",
                                         len(descs))
                        except Exception:
                            pass

                    log.info("Refresh thread: added %d new tags", added)

                # Signal poll thread to reload its tag list
                if new_tags or removed_tags:
                    self._tags_changed.set()

                elapsed = time.time() - t0
                log.info("Refresh thread: done in %.1fs (%d tags, -%d +%d)",
                         elapsed, len(self.ua_nodes),
                         len(removed_tags), len(new_tags))
        finally:
            try:
                refresh_opc.close()
            except Exception:
                pass
            pythoncom.CoUninitialize()
            log.info("Refresh thread: stopped")

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self):
        """Start the bridge.

        The OPC UA server starts immediately (empty) so clients can
        connect right away. Tag discovery, initial reads and node
        creation happen in the background poll thread so the interface
        is available as fast as possible.
        """
        # 1. Setup and start OPC UA server immediately (no tags yet)
        self.setup_ua_server()
        self.ua_server.start()
        endpoint = "opc.tcp://%s:%d/opcda2ua/" % (self.ua_bind, self.ua_port)
        log.info("=" * 60)
        log.info("OPC UA server running at %s", endpoint)
        log.info("Tags will be added in background...")
        log.info("Press Ctrl+C to stop")
        log.info("=" * 60)

        # 2. Start poll thread (handles bootstrap + polling)
        self._stop_event.clear()
        poll_thread = threading.Thread(target=self._poll_loop)
        poll_thread.daemon = True
        poll_thread.start()

        # 3. Start refresh thread if configured
        if self.refresh_minutes > 0:
            refresh_thread = threading.Thread(target=self._refresh_loop)
            refresh_thread.daemon = True
            refresh_thread.start()
            log.info("Refresh thread started (every %d minutes)",
                     self.refresh_minutes)

        # 4. Keep main thread alive
        try:
            while not self._stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            self.stop()

    def stop(self):
        """Stop the bridge gracefully."""
        self._stop_event.set()
        if self.ua_server:
            try:
                self.ua_server.stop()
                log.info("OPC UA server stopped")
            except Exception:
                pass
        if self.opc:
            try:
                self.opc.close()
                log.info("OPC DA connection closed")
            except Exception:
                pass


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode = None
    if args.smart:
        mode = 'smart'
        log.info("Mode: smart (suffix-convention filtering filtering)")
    elif args.ifix:
        mode = 'ifix'
        log.info("Mode: iFIX (.F_CV/.A_CV filtering)")

    bridge = LegacyBridge(
        da_server=args.server,
        da_host=args.host,
        ua_port=args.port,
        ua_bind=args.bind,
        poll_interval=args.interval,
        tag_filter=args.filter,
        no_properties=args.no_properties,
        refresh_minutes=args.refresh,
        mode=mode,
    )

    try:
        if args.list_branches:
            bridge.connect_da()
            branches = bridge.opc.list()
            print("Top-level branches:")
            for b in branches:
                print("  %s" % b)
                try:
                    subs = bridge.opc.list(b)
                    for s in subs[:50]:
                        try:
                            count = len(bridge.opc.list(s, flat=True))
                        except Exception:
                            count = '?'
                        print("    %s (%s tags)" % (s, count))
                    if len(subs) > 50:
                        print("    ... and %d more" % (len(subs) - 50))
                except Exception as e:
                    print("    (error: %s)" % e)
            try:
                root_tags = bridge.opc.list('*', flat=True)
                print("\nRoot flat listing: %d items" % len(root_tags))
                for t in root_tags[:20]:
                    print("  %s" % t)
                if len(root_tags) > 20:
                    print("  ... and %d more" % (len(root_tags) - 20))
            except Exception as e:
                print("\nRoot flat listing error: %s" % e)
            bridge.stop()
        else:
            bridge.run()
    except Exception as e:
        log.error("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()

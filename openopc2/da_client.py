###########################################################################
#
# OpenOPC for Python Library Module
#
# Copyright (c) 2007-2012 Barry Barnreiter (barry_b@users.sourceforge.net)
# Copyright (c) 2014 Anton D. Kachalov (mouse@yandex.ru)
# Copyright (c) 2017 José A. Maita (jose.a.maita@gmail.com)
#
###########################################################################

import logging
import os
import re
import socket
import string
import sys
import time
import uuid
import threading
from multiprocessing import Queue
from queue import Queue as ThreadQueue, Empty

try:
    import Pyro5.api
    PYRO_AVAILABLE = True
except ImportError:
    PYRO_AVAILABLE = False

from openopc2 import system_health
from openopc2.config import OpenOpcConfig
from openopc2.exceptions import OPCError
from openopc2.da_com import OpcCom

from openopc2.logger import log

SOURCE_CACHE = 1
SOURCE_DEVICE = 2
OPC_STATUS = (0, 'Running', 'Failed', 'NoConfig', 'Suspended', 'Test')
BROWSER_TYPE = {0: 0,
                1: 'Hierarchical',
                2: 'Flat'}

current_client = None

# Win32 only modules not needed for 'open' protocol mode
if os.name == 'nt':
    try:
        import win32com.client
        import win32com.server.util
        import win32event
        import pythoncom
        import pywintypes

        # Win32 variant types
        pywintypes.datetime = pywintypes.TimeType
        vt = dict([(pythoncom.__dict__[vtype], vtype) for vtype in pythoncom.__dict__.keys() if vtype[:2] == "VT"])

        # Allow gencache to create the cached wrapper objects
        win32com.client.gencache.is_readonly = False
        win32com.client.gencache.Rebuild(verbose=0)

    # So we can work on Windows in "open" protocol mode without the need for the win32com modules
    except ImportError as e:
        log.exception(e)
        win32com_found = False
    else:
        win32com_found = True
else:
    win32com_found = False


def type_check(tags):
    """Perform a type check on a list of tags"""
    single = type(tags) not in (list, tuple)
    tags = tags if tags else []
    tags = [tags] if single else tags
    valid = len([t for t in tags if type(t) not in (str, bytes)]) == 0
    return tags, single, valid


def wild2regex(string):
    """Convert a Unix wildcard glob into a regular expression"""
    return string.replace('.', '\.').replace('*', '.*').replace('?', '.').replace('!', '^')


def tags2trace(tags):
    """Convert a list tags into a formatted string suitable for the trace callback log"""
    arg_str = ''
    for i, t in enumerate(tags[1:]):
        if i > 0: arg_str += ','
        arg_str += '%s' % t
    return arg_str


def exceptional(func, alt_return=None, alt_exceptions=(Exception,), final=None, catch=None):
    """Turns exceptions into an alternative return value"""

    def _exceptional(*args, **kwargs):
        try:
            try:
                return func(*args, **kwargs)
            except alt_exceptions:
                return alt_return
            except:
                if catch:
                    return catch(sys.exc_info(), lambda: func(*args, **kwargs))
                raise
        finally:
            if final:
                final()

    return _exceptional


class GroupEvents:
    def __init__(self):
        self.client = current_client

    def OnDataChange(self, TransactionID, NumItems, ClientHandles, ItemValues, Qualities, TimeStamps):
        self.client.callback_queue.put((TransactionID, ClientHandles, ItemValues, Qualities, TimeStamps))


class SubscriptionGroupEvents:
    """Handles callbacks for persistent subscriptions (separate from async reads)"""
    def __init__(self):
        self.client = current_client

    def OnDataChange(self, TransactionID, NumItems, ClientHandles, ItemValues, Qualities, TimeStamps):
        self.client._subscription_queue.put(
            (TransactionID, ClientHandles, ItemValues, Qualities, TimeStamps)
        )


def _pyro_expose(cls):
    """Decorator that applies Pyro5 expose if available, otherwise no-op"""
    if PYRO_AVAILABLE:
        return Pyro5.api.expose(cls)
    return cls


@_pyro_expose
class OpcDaClient:
    def __init__(self, open_opc_config: OpenOpcConfig = OpenOpcConfig()):
        """Instantiate OPC automation class"""

        self.opc_server = open_opc_config.OPC_SERVER
        self.opc_host = open_opc_config.OPC_HOST
        self.client_name = open_opc_config.OPC_CLIENT
        self.connected = False
        self.client_id = uuid.uuid4()
        self.config = open_opc_config
        self._opc: OpcCom = OpcCom(open_opc_config.OPC_CLASS)
        self._groups = {}
        self._group_tags = {}
        self._group_valid_tags = {}
        self._group_server_handles = {}
        self._group_handles_tag = {}
        self._group_hooks = {}
        self._open_serv = None
        self._open_self = None
        self._open_guid = None
        self._prev_serv_time = None
        self._tx_id = 0
        self.trace = None
        self.cpu = None

        self.callback_queue = Queue()
        self._event = win32event.CreateEvent(None, 0, 0, None)

        # Subscription data structures (separate from read() groups)
        self._subscription_groups = {}           # {group_name: subgroup_count}
        self._subscription_tags = {}             # {subgroup: [tags]}
        self._subscription_valid_tags = {}       # {subgroup: [valid_tags]}
        self._subscription_server_handles = {}   # {subgroup: {tag: server_handle}}
        self._subscription_handles_tag = {}      # {subgroup: {client_handle: tag}}
        self._subscription_hooks = {}            # {subgroup: SubscriptionGroupEvents}
        self._subscription_callbacks = {}        # {group_name: callback_function}
        self._subscription_queue = ThreadQueue() # Queue for subscription events
        self._subscription_thread = None         # Event processing thread
        self._subscription_thread_active = False
        self._subscription_lock = threading.Lock()
        self._subscription_next_handle = 0       # Global counter for unique client handles

    def set_trace(self, trace):
        if self._open_serv is None:
            self.trace = trace

    def connect(self, opc_server=None, opc_host='localhost'):
        """Connect to the specified OPC server"""

        log.info(f"OPC DA OpcDaClient connecting to {opc_server} {opc_host}")
        self._opc.connect(opc_server, opc_host)
        self.connected = True

        # With some OPC servers, the next OPC call immediately after Connect()
        # will occationally fail.  Sleeping for 1/100 second seems to fix this.
        time.sleep(0.01)

        self.opc_host = socket.gethostname() if opc_host == 'localhost' else opc_host

        # On reconnect we need to remove the old group names from OpenOPC's internal
        # cache since they are now invalid
        self._groups = {}
        self._group_tags = {}
        self._group_valid_tags = {}
        self._group_server_handles = {}
        self._group_handles_tag = {}
        self._group_hooks = {}

        # Clear subscription data on reconnect
        self._subscription_groups = {}
        self._subscription_tags = {}
        self._subscription_valid_tags = {}
        self._subscription_server_handles = {}
        self._subscription_handles_tag = {}
        self._subscription_hooks = {}
        self._subscription_callbacks = {}
        self._subscription_next_handle = 0  # Reset global handle counter

    def GUID(self):
        return self._open_guid

    def close(self, del_object=True):
        """Disconnect from the currently connected OPC server"""

        # Cancel all subscriptions before disconnecting
        for group in list(self._subscription_groups.keys()):
            try:
                self.unsubscribe(group=group)
            except:
                pass

        # Stop subscription thread
        self._stop_subscription_thread()

        try:
            self.remove(self.groups())

        except pythoncom.com_error as err:
            error_msg = 'Disconnect: %s' % self._get_error_str(err)
            raise OPCError(error_msg)

        except OPCError:
            pass

        finally:
            if self.trace:
                self.trace('Disconnect()')
            self._opc.disconnect()

            # Remove this object from the open gateway service
            if self._open_serv and del_object:
                self._open_serv.release_client(self._open_self)

    def iread(self, tags=None, group=None, size=None, pause=0, source='hybrid', update=-1, timeout=5000, sync=False,
              include_error=False, rebuild=False):
        """Iterable version of read()"""

        def add_items(tags):
            names = list(tags)

            names.insert(0, "")
            errors = []

            if self.trace:
                self.trace('Validate(%s)' % tags2trace(names))

            try:
                errors = opc_items.Validate(len(names) - 1, names)
            except:
                log.exception(f"Validation error {errors}")
                pass

            valid_tags = []
            valid_values = []
            client_handles = []

            if not sub_group in self._group_handles_tag:
                self._group_handles_tag[sub_group] = {}
                n = 0
            elif len(self._group_handles_tag[sub_group]) > 0:
                n = max(self._group_handles_tag[sub_group]) + 1
            else:
                n = 0

            for i, tag in enumerate(tags):
                if errors[i] == 0:
                    valid_tags.append(tag)
                    client_handles.append(n)
                    self._group_handles_tag[sub_group][n] = tag
                    n += 1
                elif include_error:
                    error_msgs[tag] = self._opc.get_error_string(errors[i])

                if self.trace and errors[i] != 0:
                    self.trace('%s failed validation' % tag)

            client_handles.insert(0, 0)
            valid_tags.insert(0, "")
            server_handles = []
            errors = []

            if self.trace:
                self.trace('AddItems(%s)' % tags2trace(valid_tags))

            try:
                server_handles, errors = opc_items.AddItems(len(client_handles) - 1, valid_tags, client_handles)
            except Exception as e:
                log.exception("Error adding items to Group", exc_info=True)
                pass

            valid_tags_tmp = []
            server_handles_tmp = []
            valid_tags.pop(0)

            if not sub_group in self._group_server_handles:
                self._group_server_handles[sub_group] = {}

            for i, tag in enumerate(valid_tags):
                if errors[i] == 0:
                    valid_tags_tmp.append(tag)
                    server_handles_tmp.append(server_handles[i])
                    self._group_server_handles[sub_group][tag] = server_handles[i]
                elif include_error:
                    error_msgs[tag] = self._opc.GetErrorString(errors[i])

            valid_tags = valid_tags_tmp
            server_handles = server_handles_tmp

            return valid_tags, server_handles

        def remove_items(tags):
            if self.trace:
                self.trace('RemoveItems(%s)' % tags2trace([''] + tags))
            server_handles = [self._group_server_handles[sub_group][tag] for tag in tags]
            server_handles.insert(0, 0)

            try:
                errors = opc_items.Remove(len(server_handles) - 1, server_handles)
            except pythoncom.com_error as err:
                error_msg = 'RemoveItems: %s' % self._get_error_str(err)
                raise OPCError(error_msg)

        try:
            self._update_tx_time()

            if include_error:
                sync = True

            if sync:
                update = -1

            tags, single, valid = type_check(tags)
            if not valid:
                raise TypeError("iread(): 'tags' parameter must be a string or a list of strings")

            # Group exists
            if group in self._groups and not rebuild:
                num_groups = self._groups[group]
                data_source = SOURCE_CACHE

            # Group non-existant
            else:
                if size:
                    # Break-up tags into groups of 'size' tags
                    tag_groups = [tags[i:i + size] for i in range(0, len(tags), size)]
                else:
                    tag_groups = [tags]

                num_groups = len(tag_groups)
                data_source = SOURCE_DEVICE

            for gid in range(num_groups):
                if gid > 0 and pause > 0:
                    time.sleep(pause / 1000.0)

                error_msgs = {}
                opc_groups = self._opc.groups
                opc_groups.DefaultGroupUpdateRate = update

                # Anonymous group
                if group is None:
                    try:
                        if self.trace:
                            self.trace('AddGroup()')
                        opc_group = opc_groups.Add()
                    except pythoncom.com_error as err:
                        error_msg = 'AddGroup: %s' % self._get_error_str(err)
                        raise OPCError(error_msg)
                    sub_group = group
                    new_group = True
                else:
                    sub_group = '%s.%d' % (group, gid)

                    # Existing named group
                    try:
                        if self.trace:
                            self.trace('GetOPCGroup(%s)' % sub_group)
                        opc_group = opc_groups.GetOPCGroup(sub_group)
                        new_group = False

                    # New named group
                    except:
                        try:
                            if self.trace:
                                self.trace('AddGroup(%s)' % sub_group)
                            opc_group = opc_groups.Add(sub_group)
                        except pythoncom.com_error as err:
                            error_msg = 'AddGroup: %s' % self._get_error_str(err)
                            raise OPCError(error_msg)
                        self._groups[str(group)] = len(tag_groups)
                        new_group = True

                opc_items = opc_group.OPCItems

                if new_group:
                    opc_group.IsSubscribed = 1
                    opc_group.IsActive = 1
                    if not sync:
                        if self.trace:
                            self.trace('WithEvents(%s)' % opc_group.Name)
                        global current_client
                        current_client = self
                        self._group_hooks[opc_group.Name] = win32com.client.WithEvents(opc_group, GroupEvents)

                    tags = tag_groups[gid]

                    valid_tags, server_handles = add_items(tags)

                    self._group_tags[sub_group] = tags
                    self._group_valid_tags[sub_group] = valid_tags

                # Rebuild existing group
                elif rebuild:
                    tags = tag_groups[gid]

                    valid_tags = self._group_valid_tags[sub_group]
                    add_tags = [t for t in tags if t not in valid_tags]
                    del_tags = [t for t in valid_tags if t not in tags]

                    if len(add_tags) > 0:
                        valid_tags, server_handles = add_items(add_tags)
                        valid_tags = self._group_valid_tags[sub_group] + valid_tags

                    if len(del_tags) > 0:
                        remove_items(del_tags)
                        valid_tags = [t for t in valid_tags if t not in del_tags]

                    self._group_tags[sub_group] = tags
                    self._group_valid_tags[sub_group] = valid_tags

                    if source == 'hybrid':
                        data_source = SOURCE_DEVICE

                # Existing group
                else:
                    tags = self._group_tags[sub_group]
                    valid_tags = self._group_valid_tags[sub_group]
                    if sync:
                        server_handles = [item.ServerHandle for item in opc_items]

                tag_value = {}
                tag_quality = {}
                tag_time = {}
                tag_error = {}

                # Sync Read
                if sync:
                    values = []
                    errors = []
                    qualities = []
                    timestamps = []

                    if len(valid_tags) > 0:
                        server_handles.insert(0, 0)

                        if source != 'hybrid':
                            data_source = SOURCE_CACHE if source == 'cache' else SOURCE_DEVICE

                        if self.trace:
                            self.trace('SyncRead(%s)' % data_source)

                        try:
                            values, errors, qualities, timestamps = opc_group.SyncRead(data_source,
                                                                                       len(server_handles) - 1,
                                                                                       server_handles)
                        except pythoncom.com_error as err:
                            error_msg = f"SyncRead:  {self._get_error_str(err)}"
                            raise OPCError(error_msg)

                        for i, tag in enumerate(valid_tags):
                            tag_value[tag] = values[i]
                            tag_quality[tag] = qualities[i]
                            tag_time[tag] = timestamps[i]
                            tag_error[tag] = errors[i]

                # Async Read
                else:
                    if len(valid_tags) > 0:
                        if self._tx_id >= 0xFFFF:
                            self._tx_id = 0
                        self._tx_id += 1

                        if source != 'hybrid':
                            data_source = SOURCE_CACHE if source == 'cache' else SOURCE_DEVICE

                        if self.trace:
                            self.trace('AsyncRefresh(%s)' % data_source)

                        try:
                            opc_group.AsyncRefresh(data_source, self._tx_id)
                        except pythoncom.com_error as err:
                            error_msg = 'AsyncRefresh: %s' % self._get_error_str(err)
                            raise OPCError(error_msg)

                        tx_id = 0
                        start = time.time() * 1000

                        while tx_id != self._tx_id:
                            now = time.time() * 1000
                            if now - start > timeout:
                                raise TimeoutError('Callback: Timeout waiting for data')

                            if self.callback_queue.empty():
                                pythoncom.PumpWaitingMessages()
                            else:
                                tx_id, handles, values, qualities, timestamps = self.callback_queue.get()

                        for i, h in enumerate(handles):
                            tag = self._group_handles_tag[sub_group][h]
                            tag_value[tag] = values[i]
                            tag_quality[tag] = qualities[i]
                            tag_time[tag] = timestamps[i]

                for tag in tags:
                    if tag in tag_value:
                        if (not sync and len(valid_tags) > 0) or (sync and tag_error[tag] == 0):
                            value = tag_value[tag]
                            if type(value) == pywintypes.TimeType:
                                value = str(value)
                            quality = OpcCom.get_quality_string(tag_quality[tag])
                            timestamp = str(tag_time[tag])
                        else:
                            value = None
                            quality = 'Error'
                            timestamp = None
                        if include_error:
                            error_msgs[tag] = self._opc.get_error_string(tag_error[tag]).strip('\r\n')
                    else:
                        value = None
                        quality = 'Error'
                        timestamp = None
                        if tag and include_error and not error_msgs:
                            error_msgs[tag] = ''

                    if single:
                        if include_error:
                            yield value, quality, timestamp, error_msgs[tag]
                        else:
                            yield value, quality, timestamp
                    else:
                        if include_error:
                            yield tag, value, quality, timestamp, error_msgs[tag]
                        else:
                            yield tag, value, quality, timestamp

                if group is None:
                    try:
                        if not sync and opc_group.Name in self._group_hooks:
                            if self.trace:
                                self.trace('CloseEvents(%s)' % opc_group.Name)
                            self._group_hooks[opc_group.Name].close()

                        if self.trace:
                            self.trace('RemoveGroup(%s)' % opc_group.Name)
                        opc_groups.Remove(opc_group.Name)

                    except pythoncom.com_error as err:
                        error_msg = 'RemoveGroup: %s' % self._get_error_str(err)
                        raise OPCError(error_msg)

        except pythoncom.com_error as err:
            error_msg = f'read: {self._get_error_str(err)}'
            raise OPCError(error_msg)

    def read(self, tags=None, group=None, size=None, pause=0, source='hybrid', update=-1, timeout=5000, sync=False,
             include_error=False, rebuild=False):
        """Return list of (value, quality, time) tuples for the specified tag(s)"""

        tags_list, single, valid = type_check(tags)
        if not valid:
            raise TypeError("read(): 'tags' parameter must be a string or a list of strings")

        num_health_tags = len([t for t in tags_list if t[:1] == '@'])
        num_opc_tags = len([t for t in tags_list if t[:1] != '@'])

        if num_health_tags > 0:
            if num_opc_tags > 0:
                raise TypeError("read(): system health and OPC tags cannot be included in the same group")
            results = self._read_health(tags)
        else:
            results = self.iread(tags, group, size, pause, source, update, timeout, sync, include_error, rebuild)

        return list(results)[0] if single else list(results)

    def _read_health(self, tags):
        """Return values of special system health monitoring tags"""

        self._update_tx_time()
        tags, single, valid = type_check(tags)

        time_str = time.strftime('%x %H:%M:%S')
        results = []
        #
        for t in tags:
            #     if t == '@MemFree':
            #         value = system_health.mem_free()
            #     elif t == '@MemUsed':
            #         value = system_health.mem_used()
            #     elif t == '@MemTotal':
            #         value = system_health.mem_total()
            #     elif t == '@MemPercent':
            #         value = system_health.mem_percent()
            #     elif t == '@DiskFree':
            #         value = system_health.disk_free()
            #     elif t == '@SineWave':
            #         value = system_health.sine_wave()
            #     elif t == '@SawWave':
            #         value = system_health.saw_wave()
            #
            #     elif t == '@CpuUsage':
            #         if self.cpu is None:
            #             self.cpu = system_health.CPU()
            #             time.sleep(0.1)
            #         value = self.cpu.get_usage()
            #
            #     else:
            #         value = None
            #
            #         m = re.match('@TaskMem\((.*?)\)', t)
            #         if m:
            #             image_name = m.group(1)
            #             value = system_health.task_mem(image_name)
            #
            #         m = re.match('@TaskCpu\((.*?)\)', t)
            #         if m:
            #             image_name = m.group(1)
            #             value = system_health.task_cpu(image_name)
            #
            #         m = re.match('@TaskExists\((.*?)\)', t)
            #         if m:
            #             image_name = m.group(1)
            #             value = system_health.task_exists(image_name)
            value = 10000
            if value is None:
                quality = 'Error'
            else:
                quality = 'Good'

            if single:
                results.append((value, quality, time_str))
            else:
                results.append((t, value, quality, time_str))

        return results

    def iwrite(self, tag_value_pairs, size=None, pause=0, include_error=False):
        """Iterable version of write()"""

        try:
            self._update_tx_time()

            def _valid_pair(p):
                if type(p) in (list, tuple) and len(p) >= 2 and type(p[0]) in (str, bytes):
                    return True
                else:
                    return False

            if type(tag_value_pairs) not in (list, tuple):
                raise TypeError(
                    "write(): 'tag_value_pairs' parameter must be a (tag, value) tuple or a list of (tag,value) tuples")

            if tag_value_pairs is None:
                tag_value_pairs = ['']
                single = False
            elif type(tag_value_pairs[0]) in (str, bytes):
                tag_value_pairs = [tag_value_pairs]
                single = True
            else:
                single = False

            invalid_pairs = [p for p in tag_value_pairs if not _valid_pair(p)]
            if len(invalid_pairs) > 0:
                raise TypeError(
                    "write(): 'tag_value_pairs' parameter must be a (tag, value) tuple or a list of (tag,value) tuples")

            names = [tag[0] for tag in tag_value_pairs]
            tags = [tag[0] for tag in tag_value_pairs]
            values = [tag[1] for tag in tag_value_pairs]

            # Break-up tags & values into groups of 'size' tags
            if size:
                name_groups = [names[i:i + size] for i in range(0, len(names), size)]
                tag_groups = [tags[i:i + size] for i in range(0, len(tags), size)]
                value_groups = [values[i:i + size] for i in range(0, len(values), size)]
            else:
                name_groups = [names]
                tag_groups = [tags]
                value_groups = [values]

            num_groups = len(tag_groups)

            status = []

            for gid in range(num_groups):
                if gid > 0 and pause > 0:
                    time.sleep(pause / 1000.0)

                opc_groups = self._opc.groups
                opc_group = opc_groups.Add()
                opc_items = opc_group.OPCItems

                names = name_groups[gid]
                tags = tag_groups[gid]
                values = value_groups[gid]

                names.insert(0, "")
                errors = []

                try:
                    errors = opc_items.Validate(len(names) - 1, names)
                except:
                    log.exception(errors)
                    pass

                n = 1
                valid_tags = []
                valid_values = []
                client_handles = []
                error_msgs = {}

                for i, tag in enumerate(tags):
                    if errors[i] == 0:
                        valid_tags.append(tag)
                        valid_values.append(values[i])
                        client_handles.append(n)
                        error_msgs[tag] = ''
                        n += 1
                    elif include_error:
                        error_msgs[tag] = self._opc.get_error_string(errors[i])
                        pass

                client_handles.insert(0, 0)
                valid_tags.insert(0, "")
                server_handles = []
                errors = []

                try:
                    server_handles, errors = opc_items.AddItems(len(client_handles) - 1, valid_tags, client_handles)
                except:
                    pass

                valid_tags_tmp = []
                valid_values_tmp = []
                server_handles_tmp = []
                valid_tags.pop(0)

                for i, tag in enumerate(valid_tags):
                    if errors[i] == 0:
                        valid_tags_tmp.append(tag)
                        valid_values_tmp.append(valid_values[i])
                        server_handles_tmp.append(server_handles[i])
                        error_msgs[tag] = ''
                    elif include_error:
                        error_msgs[tag] = self._opc.GetErrorString(errors[i])

                valid_tags = valid_tags_tmp
                valid_values = valid_values_tmp
                server_handles = server_handles_tmp

                server_handles.insert(0, 0)
                valid_values.insert(0, 0)
                errors = []

                if len(valid_values) > 1:
                    try:
                        errors = opc_group.SyncWrite(len(server_handles) - 1, server_handles, valid_values)
                    except:
                        pass

                n = 0
                for tag in tags:
                    if tag in valid_tags:
                        if errors[n] == 0:
                            status = 'Success'
                        else:
                            status = 'Error'
                        if include_error:  error_msgs[tag] = self._opc.get_error_string(errors[n])
                        n += 1
                    else:
                        status = 'Error'

                    # OPC servers often include newline and carriage return characters
                    # in their error message strings, so remove any found.
                    if include_error:  error_msgs[tag] = error_msgs[tag].strip('\r\n')

                    if single:
                        if include_error:
                            yield (status, error_msgs[tag])
                        else:
                            yield status
                    else:
                        if include_error:
                            yield (tag, status, error_msgs[tag])
                        else:
                            yield (tag, status)

                opc_groups.Remove(opc_group.Name)

        except pythoncom.com_error as err:
            error_msg = 'write: %s' % self._get_error_str(err)
            raise OPCError(error_msg)

    def write(self, tag_value_pairs, size=None, pause=0, include_error=False):
        """Write list of (tag, value) pair(s) to the server"""
        status = list(self.iwrite(tag_value_pairs, size, pause, include_error))
        return status

    def groups(self):
        """Return a list of active tag groups"""
        return self._groups.keys()

    def remove(self, groups):
        """Remove the specified tag group(s)"""

        try:

            opc_groups = self._opc.groups

            if type(groups) in (str, bytes):
                groups = [groups]
                single = True
            else:
                single = False

            status = []

            for group in groups:
                if group in self._groups:
                    for i in range(self._groups[group]):
                        sub_group = '%s.%d' % (group, i)

                        if sub_group in self._group_hooks:
                            if self.trace: self.trace('CloseEvents(%s)' % sub_group)
                            self._group_hooks[sub_group].close()

                        try:
                            if self.trace: self.trace('RemoveGroup(%s)' % sub_group)
                            errors = opc_groups.Remove(sub_group)
                        except pythoncom.com_error as err:
                            error_msg = 'RemoveGroup: %s' % self._get_error_str(err)
                            raise OPCError(error_msg)

                        del (self._group_tags[sub_group])
                        del (self._group_valid_tags[sub_group])
                        del (self._group_handles_tag[sub_group])
                        del (self._group_server_handles[sub_group])
                    del (self._groups[group])

        except pythoncom.com_error as err:
            error_msg = 'remove: %s' % self._get_error_str(err)
            raise OPCError(error_msg)

    def iproperties(self, tags, property_ids: list = []):
        """Iterable version of properties()"""

        self._update_tx_time()

        tags, single_tag, valid = type_check(tags)
        if not valid:
            raise TypeError("properties(): 'tags' parameter must be a string or a list of strings")

        for tag in tags:
            tag_properties, errors = self._opc.get_tag_properties(tag, property_ids)
            yield tag_properties

    def properties(self, tags, id=None):
        """Return list of property tuples (id, name, value) for the specified tag(s) """

        single = type(tags) not in (list, tuple) and type(id) not in (type(None), list, tuple)
        props = list(self.iproperties(tags, id))
        return props[0] if single else props

    def ilist(self, paths='*', recursive: bool = False, flat: bool = False, include_type: bool = False,
              access_rights: int = None):
        """Iterable version of list()

        """

        try:
            self._update_tx_time()

            try:
                browser = self._opc.create_browser()
            # For OPC servers that don't support browsing
            except:
                log.exception("This Server does not support Browsing")
                return

            if access_rights:
                browser.AccessRights = access_rights

            paths, single, valid = type_check(paths)
            if not valid:
                raise TypeError("list(): 'paths' parameter must be a string or a list of strings")

            if len(paths) == 0:
                paths = ['*']
            nodes = {}

            for path in paths:

                if flat:
                    browser.MoveToRoot()
                    browser.Filter = ''
                    browser.ShowLeafs(True)
                    pattern = re.compile('^%s$' % wild2regex(path), re.IGNORECASE)
                    matches = filter(pattern.search, browser)

                    if include_type:
                        matches = [(x, node_type) for x in matches]

                    for node in matches:
                        yield node
                    continue

                queue = list()
                queue.append(path)

                while len(queue) > 0:
                    tag = queue.pop(0)

                    browser.MoveToRoot()
                    browser.Filter = ''
                    pattern = None

                    path_str = '/'
                    path_list = tag.replace('.', '/').split('/')
                    path_list = [p for p in path_list if len(p) > 0]
                    found_filter = False
                    path_postfix = '/'

                    for i, p in enumerate(path_list):
                        if found_filter:
                            path_postfix += p + '/'
                        elif p.find('*') >= 0:
                            pattern = re.compile('^%s$' % wild2regex(p), re.IGNORECASE)
                            found_filter = True
                        elif len(p) != 0:
                            pattern = re.compile('^.*$')
                            browser.ShowBranches()

                            # Branch node, so move down
                            if len(browser) > 0:
                                try:
                                    browser.MoveDown(p)
                                    path_str += p + '/'
                                except:
                                    if i < len(path_list) - 1: return
                                    pattern = re.compile('^%s$' % wild2regex(p), re.IGNORECASE)

                            # Leaf node, so append all remaining path parts together
                            # to form a single search expression
                            else:
                                p = string.join(path_list[i:], '.')
                                pattern = re.compile('^%s$' % wild2regex(p), re.IGNORECASE)
                                break

                    browser.ShowBranches()

                    if len(browser) == 0:
                        browser.ShowLeafs(False)
                        lowest_level = True
                        node_type = 'Leaf'
                    else:
                        lowest_level = False
                        node_type = 'Branch'

                    matches = filter(pattern.search, browser)

                    if not lowest_level and recursive:
                        queue += [path_str + x + path_postfix for x in matches]
                    else:
                        if lowest_level:
                            matches = [exceptional(browser.GetItemID, x)(x) for x in matches]
                        if include_type:
                            matches = [(x, node_type) for x in matches]
                        for node in matches:
                            if not node in nodes:
                                yield node
                            nodes[node] = True

        except pythoncom.com_error as err:
            error_msg = 'list: %s' % self._get_error_str(err)
            raise OPCError(error_msg)

    def list(self, paths='*', recursive=False, flat=False, include_type=False, access_rights: int=None):
        """Return list of item nodes at specified path(s) (tree browser)"""

        nodes = self.ilist(paths, recursive, flat, include_type, access_rights)
        return list(nodes)

    def servers(self, opc_host='localhost'):
        """Return list of available OPC servers"""

        try:

            servers = self._opc.get_opc_servers(opc_host)
            servers = [s for s in servers if s != None]
            return servers

        except pythoncom.com_error as err:
            error_msg = 'servers: %s' % self._get_error_str(err)
            raise OPCError(error_msg)

    def info(self):
        """Return list of (name, value) pairs about the OPC server"""

        try:
            self._update_tx_time()

            info_list = []
            info_list += [('Protocol', 'gateway' if self._open_serv else 'com')]
            info_list += [('Class', self._opc.opc_class)]
            info_list += [('Client Name', self._opc.client_name)]
            info_list += [('OPC Host', self.opc_host)]
            info_list += [('OPC Server', self._opc.server_name)]
            info_list += [('State', OPC_STATUS[self._opc.server_state])]
            info_list += [
                ('Version', f'{self._opc.major_version}.{self._opc.minor_version} (Build{self._opc.build_number})')]

            browser = self._opc.create_browser()
            browser_type = BROWSER_TYPE.get(browser.Organization, 'Not Supported')

            info_list += [('Browser', browser_type)]
            info_list += [('Start Time', str(self._opc.start_time))]
            info_list += [('Current Time', str(self._opc.current_time))]
            info_list += [('Vendor', self._opc.vendor_info)]

            return info_list

        except pythoncom.com_error as err:
            error_msg = 'info: %s' % self._get_error_str(err)
            raise OPCError(error_msg)

    def ping(self):
        """Check if we are still talking to the OPC server"""
        try:
            # Convert OPC server time to milliseconds
            opc_serv_time = int(float(self._opc.current_time.timestamp()) * 1000)
            if opc_serv_time == self._prev_serv_time:
                return False
            else:
                self._prev_serv_time = opc_serv_time
                return True
        except pythoncom.com_error:
            return False

    def _get_error_str(self, err):
        """Return the error string for a OPC or COM error code"""

        hr, msg, exc, arg = err.args

        if exc == None:
            error_str = str(msg)
        else:
            scode = exc[5]

            try:
                opc_err_str = self._opc.GetErrorString(scode).strip('\r\n')
            except:
                opc_err_str = None

            try:
                com_err_str = pythoncom.GetScodeString(scode).strip('\r\n')
            except:
                com_err_str = None

            # OPC error codes and COM error codes are overlapping concepts,
            # so we combine them together into a single error message.

            if opc_err_str is None and com_err_str is None:
                error_str = str(scode)
            elif opc_err_str is com_err_str:
                error_str = opc_err_str
            elif opc_err_str is None:
                error_str = com_err_str
            elif com_err_str is None:
                error_str = opc_err_str
            else:
                error_str = '%s (%s)' % (opc_err_str, com_err_str)

        return error_str

    def _update_tx_time(self):
        """Update the session's last transaction time in the Gateway Service"""
        if self._open_serv:
            self._open_serv.tx_times[self._open_guid] = time.time()

    def subscribe(self, tags, callback, group, update_rate=1000, deadband=0.0):
        """
        Create persistent subscription to OPC DA data changes.

        Args:
            tags: str | list[str] - Tags to subscribe to
            callback: callable - Function called when values change
                     Signature: callback(changes) where changes = [(tag, value, quality, timestamp), ...]
            group: str - Group name (required)
            update_rate: int - Update frequency in ms (default: 1000)
            deadband: float - Dead band 0-100% (default: 0.0)

        Returns:
            list[str] - Successfully subscribed tags

        Raises:
            ValueError: If group is None or callback is not callable
            OPCError: If OPC operation error occurs
        """
        if group is None:
            raise ValueError("subscribe(): 'group' parameter is required")
        if not callable(callback):
            raise ValueError("subscribe(): 'callback' parameter must be callable")

        tags, single, valid = type_check(tags)
        if not valid:
            raise TypeError("subscribe(): 'tags' parameter must be a string or a list of strings")

        try:
            # Determine subgroup name
            if group in self._subscription_groups:
                gid = self._subscription_groups[group]
            else:
                gid = 0
                self._subscription_groups[group] = 1

            sub_group = f"SUB_{group}.{gid}"

            opc_groups = self._opc.groups
            opc_groups.DefaultGroupUpdateRate = update_rate

            # Create OPC group
            if self.trace:
                self.trace(f'AddGroup({sub_group})')
            opc_group = opc_groups.Add(sub_group)
            opc_group.IsSubscribed = 1
            opc_group.IsActive = 1
            opc_group.UpdateRate = update_rate
            opc_group.DeadBand = deadband

            opc_items = opc_group.OPCItems

            # Validate tags
            names = list(tags)
            names.insert(0, "")
            errors = []

            if self.trace:
                self.trace(f'Validate({tags})')

            try:
                errors = opc_items.Validate(len(names) - 1, names)
            except:
                pass

            valid_tags = []
            client_handles = []

            if sub_group not in self._subscription_handles_tag:
                self._subscription_handles_tag[sub_group] = {}

            # Use global handle counter to ensure unique handles across all groups
            for i, tag in enumerate(tags):
                if errors[i] == 0:
                    valid_tags.append(tag)
                    client_handles.append(self._subscription_next_handle)
                    self._subscription_handles_tag[sub_group][self._subscription_next_handle] = tag
                    self._subscription_next_handle += 1
                elif self.trace:
                    self.trace(f'{tag} failed validation')

            # Add items
            client_handles.insert(0, 0)
            valid_tags.insert(0, "")

            if self.trace:
                self.trace(f'AddItems({valid_tags[1:]})')

            try:
                server_handles, errors = opc_items.AddItems(len(client_handles) - 1, valid_tags, client_handles)
            except Exception as e:
                log.exception("Error adding items to subscription group", exc_info=True)
                raise OPCError(f"Failed to add items to subscription: {e}")

            valid_tags.pop(0)

            if sub_group not in self._subscription_server_handles:
                self._subscription_server_handles[sub_group] = {}

            final_valid_tags = []
            for i, tag in enumerate(valid_tags):
                if errors[i] == 0:
                    final_valid_tags.append(tag)
                    self._subscription_server_handles[sub_group][tag] = server_handles[i]
                elif self.trace:
                    self.trace(f'{tag} failed AddItems')

            # Store subscription data
            self._subscription_tags[sub_group] = tags
            self._subscription_valid_tags[sub_group] = final_valid_tags
            self._subscription_callbacks[group] = callback

            # Create event hook
            if self.trace:
                self.trace(f'WithEvents({opc_group.Name})')
            global current_client
            current_client = self
            self._subscription_hooks[sub_group] = win32com.client.WithEvents(opc_group, SubscriptionGroupEvents)

            # Start event processing thread
            self._start_subscription_thread()

            # Force the OPC DA server to push current values for ALL items in the
            # group via DataChange. iFIX/Graybox doesn't auto-emit on AddItems for
            # subscribed groups, so without this slow-changing process variables
            # remain in BadWaitingForInitialData until they next change (hours/days).
            # AsyncRefresh from cache is one COM call per group (~21 for 20k tags)
            # and does not block — values flow in via the existing event hook.
            try:
                if self._tx_id >= 0xFFFF:
                    self._tx_id = 0
                self._tx_id += 1
                if self.trace:
                    self.trace(f'AsyncRefresh(cache) on {sub_group}')
                opc_group.AsyncRefresh(SOURCE_CACHE, self._tx_id)
                log.info(f"AsyncRefresh dispatched for group '{sub_group}' "
                         f"to populate initial values for {len(final_valid_tags)} items")
            except Exception as e:
                log.warning(f"AsyncRefresh failed for group '{sub_group}': {e}; "
                            "subscribed items will populate only when their value changes")

            log.info(f"Subscribed to {len(final_valid_tags)} tags in group '{group}'")
            return final_valid_tags

        except pythoncom.com_error as err:
            error_msg = f'subscribe: {self._get_error_str(err)}'
            raise OPCError(error_msg)

    def unsubscribe(self, tags=None, group=None):
        """
        Cancel subscriptions.

        Args:
            tags: str | list[str] | None - Specific tags to unsubscribe
            group: str | None - Entire group to unsubscribe

        Note: If group is specified, tags parameter is ignored
        """
        try:
            opc_groups = self._opc.groups

            if group is not None:
                # Remove entire group
                if group not in self._subscription_groups:
                    return

                for gid in range(self._subscription_groups[group]):
                    sub_group = f"SUB_{group}.{gid}"

                    if sub_group in self._subscription_hooks:
                        if self.trace:
                            self.trace(f'CloseEvents({sub_group})')
                        self._subscription_hooks[sub_group].close()
                        del self._subscription_hooks[sub_group]

                    try:
                        if self.trace:
                            self.trace(f'RemoveGroup({sub_group})')
                        opc_groups.Remove(sub_group)
                    except pythoncom.com_error:
                        pass

                    # Clean up data structures
                    if sub_group in self._subscription_tags:
                        del self._subscription_tags[sub_group]
                    if sub_group in self._subscription_valid_tags:
                        del self._subscription_valid_tags[sub_group]
                    if sub_group in self._subscription_handles_tag:
                        del self._subscription_handles_tag[sub_group]
                    if sub_group in self._subscription_server_handles:
                        del self._subscription_server_handles[sub_group]

                del self._subscription_groups[group]
                if group in self._subscription_callbacks:
                    del self._subscription_callbacks[group]

                log.info(f"Unsubscribed from group '{group}'")

            elif tags is not None:
                # Remove specific tags (not yet implemented - remove whole group for now)
                tags, single, valid = type_check(tags)
                log.warning("Unsubscribing individual tags not yet implemented. Use group parameter.")

        except pythoncom.com_error as err:
            error_msg = f'unsubscribe: {self._get_error_str(err)}'
            raise OPCError(error_msg)

    def _subscription_event_loop(self):
        """Event loop for processing subscription callbacks (runs in separate thread)"""
        while self._subscription_thread_active:
            try:
                # Process COM messages (required for callbacks to arrive)
                pythoncom.PumpWaitingMessages()

                # Get events from queue
                try:
                    tx_id, handles, values, qualities, timestamps = self._subscription_queue.get(timeout=0.01)
                except Empty:
                    continue

                # Find group and build changes list
                changes = []
                group_name = None

                # Make a copy to avoid "dictionary changed size during iteration"
                handles_tag_copy = dict(self._subscription_handles_tag)
                for sub_group, handles_map in handles_tag_copy.items():
                    for i, handle in enumerate(handles):
                        if handle in handles_map:
                            tag = handles_map[handle]
                            quality = OpcCom.get_quality_string(qualities[i])
                            timestamp = str(timestamps[i])
                            changes.append((tag, values[i], quality, timestamp))
                            # Extract group name from subgroup
                            group_name = sub_group.split('.')[0].replace('SUB_', '')

                # Execute user callback
                if changes and group_name and group_name in self._subscription_callbacks:
                    callback = self._subscription_callbacks[group_name]
                    try:
                        callback(changes)
                    except Exception as e:
                        log.error(f"Subscription callback error: {e}")

            except Exception as e:
                if self._subscription_thread_active:
                    log.error(f"Subscription event loop error: {e}")

    def _start_subscription_thread(self):
        """Start event processing thread if not already running"""
        with self._subscription_lock:
            if self._subscription_thread is None or not self._subscription_thread.is_alive():
                self._subscription_thread_active = True
                self._subscription_thread = threading.Thread(
                    target=self._subscription_event_loop,
                    daemon=True
                )
                self._subscription_thread.start()
                log.info("Subscription event thread started")

    def _stop_subscription_thread(self):
        """Stop event processing thread"""
        self._subscription_thread_active = False
        if self._subscription_thread:
            self._subscription_thread.join(timeout=2.0)
            self._subscription_thread = None
            log.info("Subscription event thread stopped")

    def list_subscriptions(self):
        """Return dict {group: [tags]} of active subscriptions"""
        result = {}
        for sub_group, tags in self._subscription_valid_tags.items():
            group_name = sub_group.split('.')[0].replace('SUB_', '')
            if group_name not in result:
                result[group_name] = []
            result[group_name].extend(tags)
        return result

    def __getitem__(self, key):
        """Read single item (tag as dictionary key)"""
        value, quality, time = self.read(key)
        return value

    def __setitem__(self, key, value):
        """Write single item (tag as dictionary key)"""
        self.write((key, value))
        return

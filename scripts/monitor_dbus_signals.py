#!/usr/bin/env python3

# Copyright 2021 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Monitor D-Bus properties and signals and verify that the signals are correct
with respect to their properties.
"""

_INTERFACE_RE = None
_MO = None
_SERVICE = None
_TOP_OBJECT = None
_TOP_OBJECT_PATH = None
_TOP_OBJECT_INTERFACES = None

_OBJECT_MANAGER = None
_PROPERTIES = None
_INTROSPECTABLE = None


INVALIDATED = None

_MAKE_MO = None

_CALLBACK_ERRORS = []

_EMITS_CHANGED_PROP = "org.freedesktop.DBus.Property.EmitsChangedSignal"

try:
    # isort: STDLIB
    import argparse
    import os
    import re
    import sys
    import time
    import xml.etree.ElementTree as ET
    from enum import Enum

    # isort: THIRDPARTY
    import dbus
    import dbus.mainloop.glib
    from deepdiff.diff import DeepDiff
    from gi.repository import GLib

    # isort: FIRSTPARTY
    from dbus_python_client_gen import make_class

    class EmitsChangedSignal(Enum):
        """
        Values for EmitsChangedSignal introspection property.
        """

        TRUE = "true"
        INVALIDATES = "invalidates"
        CONST = "const"
        FALSE = "false"

        def __str__(self):
            return self.value

        @staticmethod
        def from_str(code_str):
            """
            Get constant from string.
            """
            for item in list(EmitsChangedSignal):
                if code_str == str(item):
                    return item
            return None

    class Invalidated:  # pylint: disable=too-few-public-methods
        """
        Used to record in the updated GetManagedObjects value that a value has
        been invalidated.
        """

        def __repr__(self):
            return "Invalidated()"

    INVALIDATED = Invalidated()

    class MissingInterface:  # pylint: disable=too-few-public-methods
        """
        Used to record in the updated GetManagedObjects value that when a
        property changed signal was received, the interface for that property
        could not be be found.
        """

        def __repr__(self):
            return "MissingInterface()"

    MISSING_INTERFACE = MissingInterface()

    # a minimal chunk of introspection data, enough for the methods needed.
    _SPECS = {
        "org.freedesktop.DBus.ObjectManager": """
            <interface name="org.freedesktop.DBus.ObjectManager">
              <method name="GetManagedObjects">
                <arg name="objpath_interfaces_and_properties" type="a{oa{sa{sv}}}" direction="out" />
              </method>
            </interface>
        """,
        "org.freedesktop.DBus.Properties": """
            <interface name="org.freedesktop.DBus.Properties">
                <method name="GetAll">
                    <arg name="interface_name" type="s" direction="in"/>
                    <arg name="props" type="a{sv}" direction="out"/>
                </method>
            </interface>
        """,
        "org.freedesktop.DBus.Introspectable": """
            <interface name="org.freedesktop.DBus.Introspectable">
                <method name="Introspect">
                    <arg name="xml_data" type="s" direction="out"/>
                </method>
            </interface>
        """,
    }

    _TIMEOUT = 120000

    _OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
    _PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
    _INTROSPECTABLE_IFACE = "org.freedesktop.DBus.Introspectable"

    _OBJECT_MANAGER = make_class(
        "ObjectManager", ET.fromstring(_SPECS[_OBJECT_MANAGER_IFACE]), _TIMEOUT
    )

    _PROPERTIES = make_class(
        "Properties", ET.fromstring(_SPECS[_PROPERTIES_IFACE]), _TIMEOUT
    )

    _INTROSPECTABLE = make_class(
        "Introspectable", ET.fromstring(_SPECS[_INTROSPECTABLE_IFACE]), _TIMEOUT
    )

    def _make_mo():
        """
        Returns the result of calling ObjectManager.GetManagedObjects +
        the result of calling Properties.GetAll on the top object for
        selected interfaces.
        """

        mos = _OBJECT_MANAGER.Methods.GetManagedObjects(_TOP_OBJECT, {})

        mos = {
            o: {
                k: v for k, v in d.items() if re.fullmatch(_INTERFACE_RE, k) is not None
            }
            for o, d in mos.items()
        }

        mos[_TOP_OBJECT_PATH] = {}

        for interface in _TOP_OBJECT_INTERFACES:
            props = _PROPERTIES.Methods.GetAll(
                _TOP_OBJECT, {"interface_name": interface}
            )
            mos[_TOP_OBJECT_PATH][interface] = props

        return mos

    _MAKE_MO = _make_mo

    def _interfaces_added(object_path, interfaces_added):
        """
        Update the record with the interfaces added.

        :param str object_path: D-Bus object path
        :param dict interfaces_added: map of interfaces to D-Bus properties
        """
        interfaces_added = {
            k: v
            for k, v in interfaces_added.items()
            if re.fullmatch(_INTERFACE_RE, k) is not None
        }

        if object_path == _TOP_OBJECT_PATH:
            interfaces_added = {
                k: v for k, v in interfaces_added.items() if k in _TOP_OBJECT_INTERFACES
            }

        try:
            print(
                "Interfaces added:",
                object_path,
                os.linesep,
                interfaces_added,
                os.linesep,
                file=sys.stderr,
                flush=True,
            )

            if object_path in _MO.keys():
                for interface, props in interfaces_added.items():
                    _MO[object_path][interface] = props
            else:
                _MO[object_path] = interfaces_added
        except Exception as exc:  # pylint: disable=broad-except
            _CALLBACK_ERRORS.append(exc)

    def _interfaces_removed(object_path, interfaces):
        """
        Updates current ManagedObjects result on interfaces removed signal
        received.

        :param str object_path: D-Bus object path
        :param list interfaces: list of interfaces removed
        """
        try:
            print(
                "Interfaces removed:",
                object_path,
                os.linesep,
                interfaces,
                os.linesep,
                file=sys.stderr,
                flush=True,
            )

            if object_path in _MO.keys():
                for interface in interfaces:
                    if interface in _MO[object_path]:
                        del _MO[object_path][interface]

                # The InterfacesRemoved signal is sent when an object is
                # removed as well as when a single interface is removed.
                # Assume that when all the interfaces are gone, this means
                # that the object itself has been removed.
                if _MO[object_path] == {}:
                    del _MO[object_path]
        except Exception as exc:  # pylint: disable=broad-except
            _CALLBACK_ERRORS.append(exc)

    def _properties_changed(*props_changed, object_path=None):
        """
        Properties changed handler.

        :param tuple props_changed: D-Bus properties changed record

        NOTE: On https://dbus.freedesktop.org/doc/dbus-specification.html,
        PropertiesChanged is defined as a three tuple. For some reason in
        the dbus-python implementation it is passed either as three separate
        arguments or as a tuple. For this reason it is necessary to use a
        * argument, rather than the expected arguments.
        """
        try:
            if not object_path.startswith(_TOP_OBJECT_PATH):
                return

            interface_name = props_changed[0]
            properties_changed = props_changed[1]
            properties_invalidated = props_changed[2]

            print(
                "Properties changed:",
                object_path,
                interface_name,
                os.linesep,
                properties_invalidated,
                os.linesep,
                properties_changed,
                os.linesep,
                file=sys.stderr,
                flush=True,
            )

            try:
                data = _MO[object_path]
            except KeyError as err:
                err_str = (
                    f"Attempted to update managed version of managed "
                    f"objects data structure with new property information "
                    f"for object path {object_path} and interface "
                    f"{interface_name}, but there was no entry for that "
                    f"object path."
                )
                debug_str = "Value of GetManagedObjects result: "
                raise RuntimeError(
                    os.linesep.join([err_str, debug_str, str(_MO)])
                ) from err

            if interface_name not in data:
                if (
                    object_path == _TOP_OBJECT_PATH
                    and interface_name not in _TOP_OBJECT_INTERFACES
                ) or re.fullmatch(_INTERFACE_RE, interface_name) is None:
                    return

                data[interface_name] = MISSING_INTERFACE

            if data[interface_name] is MISSING_INTERFACE:
                return

            for prop, value in properties_changed.items():
                data[interface_name][prop] = value
            for prop in properties_invalidated:
                data[interface_name][prop] = INVALIDATED
        except Exception as exc:  # pylint: disable=broad-except
            _CALLBACK_ERRORS.append(exc)

    def _monitor(service, manager, manager_interfaces, interface_re):
        """
        Monitor the signals and properties of the manager object.

        :param str service: the service to monitor
        :param str manager: object path that of the ObjectManager implementor
        :param manager_interfaces: list of manager interfaces
        :type manager_interfaces: list of str
        :param interface_re: regular expression to match interfaces to check
        :type interface_re: re.Pattern
        """

        global _TOP_OBJECT, _TOP_OBJECT_PATH, _TOP_OBJECT_INTERFACES, _SERVICE, _MO, _INTERFACE_RE  # pylint: disable=global-statement

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        _SERVICE = service
        _TOP_OBJECT_PATH = manager
        _TOP_OBJECT_INTERFACES = manager_interfaces
        _INTERFACE_RE = interface_re

        while True:
            try:
                _TOP_OBJECT = bus.get_object(service, _TOP_OBJECT_PATH)
            except Exception as err:  # pylint: disable=broad-exception-caught
                print(
                    f'Failed to get top object "{_TOP_OBJECT_PATH}" for '
                    f'service "{_SERVICE}". Error: {err}. Retrying.'
                )
                time.sleep(4)
            else:
                break

        _TOP_OBJECT.connect_to_signal(
            dbus_interface=_OBJECT_MANAGER_IFACE,
            signal_name="InterfacesAdded",
            handler_function=_interfaces_added,
        )

        _TOP_OBJECT.connect_to_signal(
            dbus_interface=_OBJECT_MANAGER_IFACE,
            signal_name="InterfacesRemoved",
            handler_function=_interfaces_removed,
        )

        bus.add_signal_receiver(
            _properties_changed,
            signal_name="PropertiesChanged",
            path_keyword="object_path",
        )

        while True:
            try:
                _MO = _MAKE_MO()
            except Exception as err:  # pylint: disable=broad-exception-caught
                print(
                    "Failed to get initial GetManagedObjects result for "
                    f'service "{_SERVICE}" and top object '
                    f'"{_TOP_OBJECT_PATH}". Error: {err}. Retrying.'
                )
                time.sleep(4)
            else:
                break

        loop = GLib.MainLoop()
        loop.run()

    def _gen_parser():
        """
        Generate the parser.
        """
        parser = argparse.ArgumentParser(
            description=(
                "Monitor D-Bus signals and check for consistency with "
                "reported D-Bus properties."
            )
        )

        parser.add_argument("service", help="The D-Bus service to monitor")
        parser.add_argument(
            "manager", help="Object that implements the ObjectManager interface"
        )

        parser.add_argument(
            "--top-interface",
            action="extend",
            dest="top_interface",
            nargs="*",
            type=str,
            default=[],
            help="interface belonging to the top object",
        )

        parser.add_argument(
            "--only-check",
            default=".*",
            type=re.compile,
            help="regular expression that restricts interfaces to check",
        )

        return parser

    def main():
        """
        The main method.
        """

        parser = _gen_parser()

        args = parser.parse_args()

        _monitor(args.service, args.manager, args.top_interface, args.only_check)

    if __name__ == "__main__":
        main()

except KeyboardInterrupt:

    class Diff:  # pylint: disable=too-few-public-methods
        """
        Diff between two different managed object results.
        """

    class AddedProperty(Diff):  # pylint: disable=too-few-public-methods
        """
        Property appears in new result but not in recorded result.
        """

        def __init__(self, object_path, interface_name, key, new_value):
            self.object_path = object_path
            self.interface_name = interface_name
            self.key = key
            self.new_value = new_value

        def __repr__(self):
            return (
                f"AddedProperty({self.object_path!r}, {self.interface_name!r}, "
                f"{self.key!r}, {self.new_value!r})"
            )

        def __str__(self):
            return (
                f"Added Property:{os.linesep}  {self.object_path}{os.linesep}"
                f"  {self.interface_name}{os.linesep}  {self.key}{os.linesep}"
                f"  {self.new_value}"
            )

    class RemovedProperty(Diff):  # pylint: disable=too-few-public-methods
        """
        Property appears in recorded result but not in new result.
        """

        def __init__(self, object_path, interface_name, key, old_value):
            self.object_path = object_path
            self.interface_name = interface_name
            self.key = key
            self.old_value = old_value

        def __repr__(self):
            return (
                f"RemovedProperty({self.object_path!r}, {self.interface_name!r}, "
                f"{self.key!r}, {self.old_value!r})"
            )

        def __str__(self):
            return (
                f"Removed Property:{os.linesep}  {self.object_path}{os.linesep}"
                f"  {self.interface_name}{os.linesep}  {self.key}{os.linesep}"
                f"  {self.old_value}"
            )

    class DifferentProperty(Diff):  # pylint: disable=too-few-public-methods
        """
        Difference between two properties.
        """

        def __init__(
            self, object_path, interface_name, key, old_value, new_value
        ):  # pylint: disable=too-many-arguments
            self.object_path = object_path
            self.interface_name = interface_name
            self.key = key
            self.old_value = old_value
            self.new_value = new_value

        def __repr__(self):
            return (
                f"DifferentProperty({self.object_path!r}, {self.interface_name!r}, "
                f"{self.key!r}, {self.old_value!r}, {self.new_value!r})"
            )

        def __str__(self):
            return (
                f"Different Property:{os.linesep}"
                f"  {self.object_path}{os.linesep}  {self.key}{os.linesep}"
                f"  {self.old_value}{os.linesep}"
                f"  {self.new_value}{os.linesep}"
            ) + os.linesep.join(
                f"    {line}"
                for line in DeepDiff(self.old_value, self.new_value).pretty()
            )

    class NotInvalidatedProperty(Diff):  # pylint: disable=too-few-public-methods
        """
        Represents a case where the property should have been invalidated but
        was updated instead.
        """

        def __init__(
            self, object_path, interface_name, key, old_value, new_value
        ):  # pylint: disable=too-many-arguments
            self.object_path = object_path
            self.interface_name = interface_name
            self.key = key
            self.old_value = old_value
            self.new_value = new_value

        def __repr__(self):
            return (
                f"NotInvalidatedProperty({self.object_path!r}, "
                f"{self.interface_name!r}, {self.key!r}, {self.old_value!r}, "
                f"{self.new_value!r})"
            )

        def __str__(self):
            return (
                f"Not Invalidated Property:{os.linesep}"
                f"  {self.object_path}{os.linesep}"
                f"  {self.interface_name}{os.linesep}  {self.key}{os.linesep}"
                f"  {self.new_value}"
            )

    class ChangedProperty(Diff):  # pylint: disable=too-few-public-methods
        """
        Represents a case where the property should have been constant but
        seems to have changed.
        """

        def __init__(
            self, object_path, interface_name, key, old_value, new_value
        ):  # pylint: disable=too-many-arguments
            self.object_path = object_path
            self.interface_name = interface_name
            self.key = key
            self.old_value = old_value
            self.new_value = new_value

        def __repr__(self):
            return (
                f"ChangedProperty({self.object_path!r}, "
                f"{self.interface_name!r}, {self.key!r}, {self.old_value!r}, "
                f"{self.new_value!r})"
            )

        def __str__(self):
            return (
                f"Changed Property:{os.linesep}  {self.object_path}{os.linesep}"
                f"  {self.interface_name}{os.linesep}  {self.key}{os.linesep}"
                f"  {self.old_value}{os.linesep}  {self.new_value}{os.linesep}"
            ) + os.linesep.join(
                f"    {line}"
                for line in DeepDiff(self.old_value, self.new_value).pretty()
            )

    class RemovedObjectPath(Diff):  # pylint: disable=too-few-public-methods
        """
        Object path appears in recorded result but not in new result.
        """

        def __init__(self, object_path, old_value):
            self.object_path = object_path
            self.old_value = old_value

        def __repr__(self):
            return f"RemovedObjectPath({self.object_path!r}, {self.old_value!r})"

        def __str__(self):
            return (
                f"Removed Object Path:{os.linesep}"
                f"{self.object_path}{os.linesep}  {self.old_value}"
            )

    class AddedInterface(Diff):  # pylint: disable=too-few-public-methods
        """
        Interface appears in new result but not in recorded result.
        """

        def __init__(self, object_path, interface_name, new_value):
            self.object_path = object_path
            self.interface_name = interface_name
            self.new_value = new_value

        def __repr__(self):
            return (
                f"AddedInterface({self.object_path!r}, {self.interface_name!r}, "
                f"{self.new_value!r})"
            )

        def __str__(self):
            return (
                f"Added Interface:{os.linesep}  {self.object_path}{os.linesep}"
                f"  {self.interface_name}{os.linesep}  {self.new_value}"
            )

    class AddedObjectPath(Diff):  # pylint: disable=too-few-public-methods
        """
        Object path appears in new result but not in recorded result.
        """

        def __init__(self, object_path, new_value):
            self.object_path = object_path
            self.new_value = new_value

        def __repr__(self):
            return f"AddedObjectPath({self.object_path!r}, {self.new_value!r})"

        def __str__(self):
            return (
                f"Added Object Path:{os.linesep}"
                f"  {self.object_path}{os.linesep}  {self.new_value}"
            )

    class RemovedInterface(Diff):  # pylint: disable=too-few-public-methods
        """
        Interface appears in recorded result but not in new result.
        """

        def __init__(self, object_path, interface_name, old_value):
            self.object_path = object_path
            self.interface_name = interface_name
            self.old_value = old_value

        def __repr__(self):
            return (
                f"RemovedInterface({self.object_path!r}, {self.interface_name!r}, "
                f"{self.old_value!r})"
            )

        def __str__(self):
            return (
                f"Removed Interface:{os.linesep}"
                f"  {self.object_path}{os.linesep}"
                f"  {self.interface_name}{os.linesep}  {self.old_value}"
            )

    class MissingInterface(Diff):  # pylint: disable=too-few-public-methods
        """
        Attempted to update a property on this interface, but the interface
        itself was missing when that happened.
        """

        def __init__(self, object_path, interface_name):
            self.object_path = object_path
            self.interface_name = interface_name

        def __repr__(self):
            return f"MissingInterface({self.object_path!r}, {self.interface_name!r}"

        def __str__(self):
            return (
                f"Missing Interface:{os.linesep}"
                f"  {self.object_path}{os.linesep}  {self.interface_name}"
            )

    def _check_props(object_path, ifn, old_props, new_props):
        """
        Find differences between two sets of properties.

        :param str object_path: D-Bus object path
        :param str ifn: a single interface name
        :param old_props: map of keys to stored property values
        :type old_props: dict or MISSING_INTERFACE
        :param dict new_props: map of keys to current property values

        :rtype list:
        :returns: a list of records of properties changed
        """

        diffs = []

        proxy = dbus.SystemBus().get_object(_SERVICE, object_path, introspect=False)
        xml_data = ET.fromstring(_INTROSPECTABLE.Methods.Introspect(proxy, {}))

        if old_props is MISSING_INTERFACE:
            diffs.append(MissingInterface(object_path, ifn))
            return diffs

        old_props_keys = frozenset(old_props.keys())
        new_props_keys = frozenset(new_props.keys())

        for key in old_props_keys - new_props_keys:
            diffs.append(RemovedProperty(object_path, ifn, key, old_props[key]))

        for key in new_props_keys - old_props_keys:
            diffs.append(AddedProperty(object_path, ifn, key, new_props[key]))

        for key in new_props_keys & old_props_keys:
            new_value = new_props[key]
            old_value = old_props[key]

            emits_signal_prop = xml_data.findall(
                f'./interface[@name="{ifn}"]/property[@name="{key}"]'
                f'/annotation[@name="{_EMITS_CHANGED_PROP}"]'
            )
            emits_signal = (
                EmitsChangedSignal.TRUE
                if emits_signal_prop == []
                else EmitsChangedSignal.from_str(emits_signal_prop[0].attrib["value"])
            )

            if new_value != old_value:
                if emits_signal is EmitsChangedSignal.TRUE:
                    diffs.append(
                        DifferentProperty(object_path, ifn, key, old_value, new_value)
                    )

                if (
                    emits_signal is EmitsChangedSignal.INVALIDATES
                    and old_value is not INVALIDATED
                ):
                    diffs.append(
                        NotInvalidatedProperty(
                            object_path, ifn, key, old_value, new_value
                        )
                    )

                if emits_signal is EmitsChangedSignal.CONST:
                    diffs.append(
                        ChangedProperty(object_path, ifn, key, old_value, new_value)
                    )

        return diffs

    def _check():
        """
        Check whether the current managed objects value matches the updated one.
        Returns a list of differences discovered. If the list is empty, then
        no differences were discovered.

        :rtype list:
        :returns a list of discrepancies discovered
        """
        if _OBJECT_MANAGER is None:
            return []

        if _PROPERTIES is None:
            return []

        if _MO is None:
            return []

        mos = _MAKE_MO()  # pylint: disable=not-callable

        diffs = []

        old_object_paths = frozenset(_MO.keys())
        new_object_paths = frozenset(mos.keys())

        for object_path in old_object_paths - new_object_paths:
            diffs.append(
                # pylint: disable=unsubscriptable-object
                RemovedObjectPath(object_path, _MO[object_path])
            )

        for object_path in new_object_paths - old_object_paths:
            diffs.append(AddedObjectPath(object_path, mos[object_path]))

        for object_path in new_object_paths & old_object_paths:
            old_data = _MO[object_path]  # pylint: disable=unsubscriptable-object
            new_data = mos[object_path]

            old_ifns = frozenset(old_data.keys())
            new_ifns = frozenset(new_data.keys())

            for ifn in new_ifns - old_ifns:
                diffs.append(AddedInterface(object_path, ifn, new_data[ifn]))

            for ifn in old_ifns - new_ifns:
                diffs.append(RemovedInterface(object_path, ifn, old_data[ifn]))

            for ifn in old_ifns & new_ifns:
                old_props = old_data[ifn]
                new_props = new_data[ifn]

                prop_diffs = _check_props(object_path, ifn, old_props, new_props)
                diffs.extend(prop_diffs)

        return diffs

    assert isinstance(_CALLBACK_ERRORS, list)
    if _CALLBACK_ERRORS:
        print(os.linesep.join(_CALLBACK_ERRORS))
        sys.exit(3)

    try:
        result = _check()
    except Exception as exco:  # pylint: disable=broad-except
        print(f"{exco}")
        sys.exit(4)

    assert isinstance(result, list)
    if not result:
        sys.exit(0)

    print(os.linesep.join(sorted(str(diff) for diff in result)))
    sys.exit(1)

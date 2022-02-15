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

_MO = None
_TOP_OBJECT = None
_TOP_OBJECT_PATH = None
_TOP_OBJECT_INTERFACES = None

_OBJECT_MANAGER = None
_PROPERTIES = None


INVALIDATED = None

_MAKE_MO = None

try:

    # isort: STDLIB
    import argparse
    import os
    import sys
    import xml.etree.ElementTree as ET

    # isort: THIRDPARTY
    import dbus
    import dbus.mainloop.glib
    from gi.repository import GLib

    # isort: FIRSTPARTY
    from dbus_python_client_gen import make_class

    class Invalidated:  # pylint: disable=too-few-public-methods
        """
        Used to record in the updated GetManagedObjects value that a value has
        been invalidates.
        """

        def __repr__(self):
            return "Invalidated()"

    INVALIDATED = Invalidated()

    # a minimal chunk of introspection data, enough for the methods needed.
    _SPECS = {
        "org.freedesktop.DBus.ObjectManager": """
            <interface name="org.freedesktop.DBus.ObjectManager">
                <method name="GetManagedObjects" />
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
    }

    _TIMEOUT = 120000

    _OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
    _PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

    _OBJECT_MANAGER = make_class(
        "ObjectManager", ET.fromstring(_SPECS[_OBJECT_MANAGER_IFACE]), _TIMEOUT
    )

    _PROPERTIES = make_class(
        "Properties", ET.fromstring(_SPECS[_PROPERTIES_IFACE]), _TIMEOUT
    )

    def _make_mo():
        """
        Returns the result of calling ObjectManager.GetManagedObjects +
        the result of calling Properties.GetAll on the top object for
        selected interfaces.
        """
        mos = _OBJECT_MANAGER.Methods.GetManagedObjects(_TOP_OBJECT, {})
        mos[_TOP_OBJECT_PATH] = dict()

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
        global _MO  # pylint: disable=global-statement

        print(
            "Interfaces added:",
            object_path,
            os.linesep,
            interfaces_added,
            os.linesep,
            file=sys.stderr,
            flush=True,
        )

        if _MO is None:
            _MO = _MAKE_MO()
        else:
            if object_path in _MO.keys():
                for interface, props in interfaces_added.items():
                    _MO[object_path][interface] = props
            else:
                _MO[object_path] = interfaces_added

    def _interfaces_removed(object_path, interfaces):
        """
        Updates current ManagedObjects result on interfaces removed signal
        received.

        :param str object_path: D-Bus object path
        :param list interfaces: list of interfaces removed
        """
        global _MO  # pylint: disable=global-statement

        print(
            "Interfaces removed:",
            object_path,
            os.linesep,
            interfaces,
            os.linesep,
            file=sys.stderr,
            flush=True,
        )

        if _MO is None:
            _MO = _MAKE_MO()
        else:
            if object_path in _MO.keys():
                for interface in interfaces:
                    del _MO[object_path][interface]

                # The InterfacesRemoved signal is sent when an object is removed
                # as well as when a single interface is removed. Assume that when
                # all the interfaces are gone, this means that the object itself
                # has been removed.
                if _MO[object_path] == dict():
                    del _MO[object_path]

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
        global _MO  # pylint: disable=global-statement

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

        if _MO is None:
            _MO = _MAKE_MO()
        else:
            data = _MO[object_path]

            if interface_name not in data:
                data[interface_name] = dict()

            for prop, value in properties_changed.items():
                data[interface_name][prop] = value
            for prop in properties_invalidated:
                data[interface_name][prop] = INVALIDATED

    def _monitor(service, manager, manager_interfaces):
        """
        Monitor the signals and properties of the manager object.

        :param str service: the service to monitor
        :param str manager: object path that of the ObjectManager implementor
        :param manager_interfaces: list of manager interfaces
        :type manager_interfaces: list of str
        """

        global _TOP_OBJECT, _TOP_OBJECT_PATH, _TOP_OBJECT_INTERFACES  # pylint: disable=global-statement

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        _TOP_OBJECT_PATH = manager
        _TOP_OBJECT_INTERFACES = manager_interfaces
        _TOP_OBJECT = bus.get_object(service, _TOP_OBJECT_PATH)

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

        return parser

    def main():
        """
        The main method.
        """

        parser = _gen_parser()

        args = parser.parse_args()

        _monitor(args.service, args.manager, args.top_interface)

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

    class RemovedObjectPath(Diff):  # pylint: disable=too-few-public-methods
        """
        Object path appears in recorded result but not in new result.
        """

        def __init__(self, object_path, old_value):
            self.object_path = object_path
            self.old_value = old_value

        def __repr__(self):
            return f"RemovedObjectPath({self.object_path!r}, {self.old_value!r})"

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

    class AddedObjectPath(Diff):  # pylint: disable=too-few-public-methods
        """
        Object path appears in new result but not in recorded result.
        """

        def __init__(self, object_path, new_value):
            self.object_path = object_path
            self.new_value = new_value

        def __repr__(self):
            return f"AddedObjectPath({self.object_path!r}, {self.new_value!r})"

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

    def _check_props(object_path, ifn, old_props, new_props):
        """
        Find differences between two sets of properties.

        :param str object_path: D-Bus object path
        :param str ifn: a single interface name
        :param dict old_props: map of keys to stored property values
        :param dict new_props: map of keys to current property values

        :rtype list:
        :returns: a list of records of properties changed
        """

        diffs = []

        for key, new_value in new_props.items():
            if key not in old_props:
                diffs.append(AddedProperty(object_path, ifn, key, new_value))
                continue

            old_value = old_props[key]

            if (not old_value is INVALIDATED) and new_value != old_value:
                diffs.append(
                    DifferentProperty(object_path, ifn, key, old_value, new_value)
                )

            del old_props[key]

        for key, old_value in old_props.items():
            diffs.append(RemovedProperty(object_path, ifn, key, old_value))

        return diffs

    def _check():
        """
        Check whether the current managed objects value matches the updated one.
        Returns a list of differences discovered. If the list is empty, then
        no differences were discovered.

        :rtype list:
        :returns a list of discrepancies discovered
        """
        if _MO is None:
            return []

        if _OBJECT_MANAGER is None:
            return []

        if _PROPERTIES is None:
            return []

        mos = _MAKE_MO()  # pylint: disable=not-callable

        diffs = []
        for object_path, new_data in mos.items():
            if object_path not in _MO:  # pylint: disable=unsupported-membership-test
                diffs.append(AddedObjectPath(object_path, new_data))
                continue

            old_data = _MO[object_path]  # pylint: disable=unsubscriptable-object

            for ifn, new_props in new_data.items():
                if ifn not in old_data:
                    diffs.append(AddedInterface(object_path, ifn, new_props))
                    continue

                old_props = old_data[ifn]
                prop_diffs = _check_props(object_path, ifn, old_props, new_props)
                diffs.extend(prop_diffs)
                del old_data[ifn]

            for ifn, old_props in old_data.items():
                diffs.append(RemovedInterface(object_path, ifn, old_props))

            del _MO[object_path]  # pylint: disable=unsupported-delete-operation

        if _MO != dict():
            for object_path, old_data in _MO.items():
                diffs.append(RemovedObjectPath(object_path, old_data))

        return diffs

    result = _check()
    if result == []:
        sys.exit(0)

    print(os.linesep.join(repr(diff) for diff in result))
    sys.exit(1)

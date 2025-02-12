#!/usr/bin/env python3

# Copyright 2025 Red Hat, Inc.
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
Stash Stratis metadata for a specific pool.
"""

# isort: STDLIB
import argparse
import datetime
import os
import time
import xml.etree.ElementTree as ET

# isort: THIRDPARTY
import dbus

# isort: FIRSTPARTY
from dbus_python_client_gen import make_class

_SPECS = {
    "org.freedesktop.DBus.ObjectManager": """
        <interface name="org.freedesktop.DBus.ObjectManager">
          <method name="GetManagedObjects">
            <arg name="objpath_interfaces_and_properties" type="a{oa{sa{sv}}}" direction="out" />
          </method>
        </interface>
    """,
    "org.storage.stratis3.pool.r8": """
        <interface name="org.storage.stratis3.pool.r8">
            <method name="Metadata">
                <arg name="current" type="b" direction="in" />
                <arg name="results" type="s" direction="out" />
                <arg name="return_code" type="q" direction="out" />
                <arg name="return_string" type="s" direction="out" />
            </method>
            <property name="Name" type="s" access="read" />
        </interface>
    """,
}

_OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
_POOL_IFACE = "org.storage.stratis3.pool.r8"


def run(namespace):
    """
    Monitor the metadata.
    """

    object_manager = make_class(
        "ObjectManager", ET.fromstring(_SPECS[_OBJECT_MANAGER_IFACE])
    )
    pool_spec = ET.fromstring(_SPECS[_POOL_IFACE])
    pool = make_class("Pool", pool_spec)

    bus = dbus.SystemBus()
    service = "org.storage.stratis3"
    proxy = bus.get_object(service, "/org/storage/stratis3")

    managed_objects = object_manager.Methods.GetManagedObjects(proxy, {})

    (pool_object_path, _) = next(
        (op, p)
        for (op, p) in managed_objects.items()
        if p.get(_POOL_IFACE) is not None
        and p[_POOL_IFACE]["Name"] == namespace.pool_name
    )

    for _ in range(namespace.limit):
        (metadata, return_code, message) = pool.Methods.Metadata(
            bus.get_object(service, pool_object_path), {"current": False}
        )
        if return_code != 0:
            raise RuntimeError(message)

        with open(
            os.path.join(
                namespace.output_dir,
                f'{datetime.datetime.now().strftime("%Y_%m_%d-%I_%M_%S_%p")}.json',
            ),
            mode="w",
            encoding="utf-8",
        ) as file:
            print(metadata, file=file)
        time.sleep(namespace.interval)


def _gen_parser():
    """
    Generate the parser.
    """

    parser = argparse.ArgumentParser(
        description=("Read and store Stratis metadata for a particular pool.")
    )

    parser.add_argument("output_dir", help="directory for output files")
    parser.add_argument("pool_name", help="name of pool to monitor")
    parser.add_argument(
        "--interval",
        help="interval between invocations of metadata method (seconds)",
        default=4,
        type=int,
    )
    parser.add_argument(
        "--limit", help="number of metadata files to store", default=100, type=int
    )
    return parser


def main():
    """
    The main method.
    """
    parser = _gen_parser()

    namespace = parser.parse_args()

    run(namespace)


if __name__ == "__main__":
    main()

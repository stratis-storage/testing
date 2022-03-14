# Copyright 2019 Red Hat, Inc.
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
DBus methods for blackbox testing.
"""
# isort: STDLIB
import os

# isort: THIRDPARTY
import dbus

from .utils import random_string

_TEST_PREF = os.getenv("STRATIS_UT_PREFIX", "STRATI5_DE5TROY_ME1_")


def p_n():
    """
    Return a random pool name
    :return: Random String
    """
    return _TEST_PREF + "pool" + random_string()


def fs_n():
    """
    Return a random FS name
    :return: Random String
    """
    return _TEST_PREF + "fs" + random_string()


# This function is an exact copy of the get_timeout function in
# the stratis_cli source code, except that it raises RuntimeError where
# that function raises StratisCliEnvironmentError.
# The function should not be imported, as these tests are intended to be
# run in an environment where the Stratis CLI source code is not certainly
# available.
def _get_timeout(value):
    """
    Turn an input str or int into a float timeout value.

    :param value: the input str or int
    :type value: str or int
    :raises RuntimeError:
    :returns: float
    """

    maximum_dbus_timeout_ms = 1073741823

    # Ensure the input str is not a float
    if isinstance(value, float):
        raise RuntimeError(
            "The timeout value provided is a float; it should be an integer."
        )

    try:
        timeout_int = int(value)

    except ValueError as err:
        raise RuntimeError("The timeout value provided is not an integer.") from err

    # Ensure the integer is not too small
    if timeout_int < -1:
        raise RuntimeError(
            "The timeout value provided is smaller than the smallest acceptable value, -1."
        )

    # Ensure the integer is not too large
    if timeout_int > maximum_dbus_timeout_ms:
        raise RuntimeError(
            "The timeout value provided exceeds the largest acceptable value, %s."
            % maximum_dbus_timeout_ms
        )

    # Convert from milliseconds to seconds
    return timeout_int / 1000


# pylint: disable=too-many-public-methods
class StratisDbus:
    "Wrappers around stratisd DBus calls"

    _OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
    _BUS = dbus.SystemBus()
    _BUS_NAME = "org.storage.stratis3"
    _TOP_OBJECT = "/org/storage/stratis3"
    REVISION_NUMBER = 1
    _REVISION = "r%s" % REVISION_NUMBER
    BUS_NAME = _BUS_NAME
    TOP_OBJECT = _TOP_OBJECT

    _MNGR_IFACE = "%s.Manager.%s" % (_BUS_NAME, _REVISION)
    _REPORT_IFACE = "%s.Report.%s" % (_BUS_NAME, _REVISION)
    _POOL_IFACE = "%s.pool.%s" % (_BUS_NAME, _REVISION)
    _FS_IFACE = "%s.filesystem.%s" % (_BUS_NAME, _REVISION)
    _BLKDEV_IFACE = "%s.blockdev.%s" % (_BUS_NAME, _REVISION)
    POOL_IFACE = _POOL_IFACE
    FS_IFACE = _FS_IFACE
    MNGR_IFACE = _MNGR_IFACE

    _DBUS_TIMEOUT_SECONDS = 120
    _TIMEOUT = _get_timeout(
        os.environ.get("STRATIS_DBUS_TIMEOUT", _DBUS_TIMEOUT_SECONDS * 1000)
    )

    @staticmethod
    def legacy_manager_interfaces():
        """
        Return a list of legacy manager interfaces, i.e., all Manager
        interfaces that precede the current, newest one.
        :rtype: list of str
        """
        interface_prefix = "%s.Manager" % StratisDbus.BUS_NAME
        return [
            "%s.r%s" % (interface_prefix, rn)
            for rn in range(StratisDbus.REVISION_NUMBER)
        ]

    @staticmethod
    def get_managed_objects():
        """
        Get managed objects for stratis
        :return: A dict,  Keys are object paths with dicts containing interface
                          names mapped to property dicts.
                          Property dicts map names to values.
        """
        object_manager = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, StratisDbus._TOP_OBJECT),
            StratisDbus._OBJECT_MANAGER,
        )
        return object_manager.GetManagedObjects(timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def stratisd_version():
        """
        Get stratisd version
        :return: The current stratisd version
        :rtype: str
        """
        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, StratisDbus._TOP_OBJECT),
            dbus.PROPERTIES_IFACE,
        )
        return iface.Get(
            StratisDbus._MNGR_IFACE, "Version", timeout=StratisDbus._TIMEOUT
        )

    @staticmethod
    def pool_list():
        """
        Query the pools
        :return: A list of pool names
        :rtype: List of str
        """
        pool_objects = [
            obj_data[StratisDbus._POOL_IFACE]
            for _, obj_data in StratisDbus.get_managed_objects().items()
            if StratisDbus._POOL_IFACE in obj_data
            and obj_data[StratisDbus._POOL_IFACE]["Name"].startswith(_TEST_PREF)
        ]

        return [pool_obj["Name"] for pool_obj in pool_objects]

    @staticmethod
    def blockdev_list():
        """
        Query the blockdevs
        :return: A list of blockdev names
        :rtype: List of str
        """
        blockdev_objects = [
            obj_data[StratisDbus._BLKDEV_IFACE]
            for _, obj_data in StratisDbus.get_managed_objects().items()
            if StratisDbus._BLKDEV_IFACE in obj_data
            and obj_data[StratisDbus._BLKDEV_IFACE]["Name"].startswith(_TEST_PREF)
        ]

        return [blockdev_obj["Name"] for blockdev_obj in blockdev_objects]

    @staticmethod
    def set_key(key_desc, temp_file):
        """
        Set a key
        :param str key_desc: The key description
        :param temp_file:
        """
        manager_iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, StratisDbus._TOP_OBJECT),
            StratisDbus._MNGR_IFACE,
        )

        with open(temp_file.name, "r") as fd_for_dbus:
            return manager_iface.SetKey(key_desc, fd_for_dbus.fileno())

    @staticmethod
    def unset_key(key_desc):
        """
        Unset a key
        """
        manager_iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, StratisDbus._TOP_OBJECT),
            StratisDbus._MNGR_IFACE,
        )

        return manager_iface.UnsetKey(key_desc)

    @staticmethod
    def get_keys():
        """
        Return a list of the key descriptions of all keys with a
        distinguishing Stratis prefix.

        :return: The return values of the ListKeys call
        :rtype: The D-Bus types as, q, and s
        """
        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, StratisDbus._TOP_OBJECT),
            StratisDbus._MNGR_IFACE,
        )
        return iface.ListKeys(timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def pool_create(
        pool_name, devices, *, key_desc=None, clevis_info=None, redundancy=None
    ):
        """
        Create a pool
        :param str pool_name: The name of the pool to create
        :param str devices: A list of devices that can be used to create the pool
        :param key_desc: Key description
        :type key_desc: str or NoneType
        :param clevis_info: pin identifier and JSON clevis configuration
        :type clevis_info: str * str OR NoneType
        :param redundancy: redundancy
        :type redundancy: int
        :return: The return values of the CreatePool call
        :rtype: The D-Bus types (b(oao)), q, and s
        """
        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, StratisDbus._TOP_OBJECT),
            StratisDbus._MNGR_IFACE,
        )
        return iface.CreatePool(
            pool_name,
            (True, redundancy) if redundancy is not None else (False, 0),
            devices,
            (True, key_desc) if key_desc is not None else (False, ""),
            (True, clevis_info) if clevis_info is not None else (False, ("", "")),
            timeout=StratisDbus._TIMEOUT,
        )

    @staticmethod
    def pool_destroy(pool_name):
        """
        Destroy a pool
        :param str pool_name: The name of the pool to destroy
        :return: The object path of the DestroyPool call, or None
        :rtype: The D-Bus types (bs), q, and s, or None
        """
        pool_objects = {
            path: obj_data[StratisDbus._POOL_IFACE]
            for path, obj_data in StratisDbus.get_managed_objects().items()
            if StratisDbus._POOL_IFACE in obj_data
            and obj_data[StratisDbus._POOL_IFACE]["Name"].startswith(_TEST_PREF)
        }

        pool_paths = [
            path
            for path, pool_obj in pool_objects.items()
            if pool_obj["Name"] == pool_name
        ]
        if len(pool_paths) != 1:
            return None

        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, StratisDbus._TOP_OBJECT),
            StratisDbus._MNGR_IFACE,
        )
        return iface.DestroyPool(pool_paths[0], timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def fs_list():
        """
        Query the file systems
        :return: A dict,  Key being the fs name, the value being the pool name
        :rtype: dict mapping str to str
        """
        objects = StratisDbus.get_managed_objects().items()

        fs_objects = [
            obj_data[StratisDbus._FS_IFACE]
            for _, obj_data in objects
            if StratisDbus._FS_IFACE in obj_data
            and obj_data[StratisDbus._FS_IFACE]["Name"].startswith(_TEST_PREF)
        ]

        pool_path_to_name = {
            obj: obj_data[StratisDbus._POOL_IFACE]["Name"]
            for obj, obj_data in objects
            if StratisDbus._POOL_IFACE in obj_data
            and obj_data[StratisDbus._POOL_IFACE]["Name"].startswith(_TEST_PREF)
        }

        return {
            fs_object["Name"]: pool_path_to_name[fs_object["Pool"]]
            for fs_object in fs_objects
        }

    @staticmethod
    def pool_init_cache(pool_path, devices):
        """
        Initialize the cache for a pool with a list of devices.
        :param str pool_path: The object path of the pool to which the cache device will be added
        :param str devices: A list of devices that can be initialized as a cache device
        :return: The return values of the InitCache call
        :rtype: The D-Bus types (bao), q, and s
        """
        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, pool_path),
            StratisDbus._POOL_IFACE,
        )
        return iface.InitCache(devices, timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def pool_add_cache(pool_path, devices):
        """
        Add a block device as a cache device
        :param str pool_path: The object path of the pool to which the cache device will be added
        :param str devices: A list of devices that can be added as a cache device
        :return: The return values of the AddCacheDevs call
        :rtype: The D-Bus types (bao), q, and s
        """
        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, pool_path),
            StratisDbus._POOL_IFACE,
        )
        return iface.AddCacheDevs(devices, timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def pool_add_data(pool_path, devices):
        """
        Add a disk to an existing pool
        :param str pool_path: The object path of the pool to which the data device will be added
        :param str devices: A list of devices that can be added as a data device
        :return: The return values of the AddCacheDevs call
        :rtype: The D-Bus types (bao), q, and s
        """
        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, pool_path),
            StratisDbus._POOL_IFACE,
        )
        return iface.AddDataDevs(devices, timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def pool_rename(pool_name, pool_name_rename):
        """
        Rename a pool
        :param str pool_name: The name of the pool to be renamed
        :param str pool_name_rename: The new name that the pool will have
        :return: The return values of the SetName call, or None
        :rtype: The D-Bus types (bs), q, and s, or None
        """
        objects = StratisDbus.get_managed_objects().items()

        pool_objects = {
            path: obj_data[StratisDbus._POOL_IFACE]
            for path, obj_data in objects
            if StratisDbus._POOL_IFACE in obj_data
            and obj_data[StratisDbus._POOL_IFACE]["Name"].startswith(_TEST_PREF)
        }

        pool_paths = [
            path
            for path, pool_obj in pool_objects.items()
            if pool_obj["Name"] == pool_name
        ]
        if len(pool_paths) != 1:
            return None

        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, pool_paths[0]),
            StratisDbus._POOL_IFACE,
        )
        return iface.SetName(pool_name_rename, timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def pool_set_param(pool_path, dbus_param, dbus_value):
        """
        Set D-Bus parameter on a pool
        :param str pool_path: The object path of the pool
        :param str dbus_param: The parameter to be set
        :param str dbus_value: The value
        """
        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, pool_path),
            dbus.PROPERTIES_IFACE,
        )

        try:
            iface.Set(
                StratisDbus._POOL_IFACE,
                dbus_param,
                dbus_value,
                timeout=StratisDbus._TIMEOUT,
            )
        except dbus.exceptions.DBusException:
            return False

        return True

    @staticmethod
    def fs_create(pool_path, fs_name, *, fs_size=None):
        """
        Create a filesystem
        :param str pool_path: The object path of the pool in which the filesystem will be created
        :param str fs_name: The name of the filesystem to create
        :param str fs_size: The size of the filesystem to create
        :return: The return values of the CreateFilesystems call
        :rtype: The D-Bus types (ba(os)), q, and s
        """
        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, pool_path),
            StratisDbus._POOL_IFACE,
        )

        file_spec = (
            (fs_name, (False, "")) if fs_size is None else (fs_name, (True, fs_size))
        )

        return iface.CreateFilesystems([file_spec], timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def fs_destroy(pool_name, fs_name):
        """
        Destroy a filesystem
        :param str pool_name: The name of the pool which contains the filesystem
        :param str fs_name: The name of the filesystem to destroy
        :return: The return values of the DestroyFilesystems call, or None
        :rtype: The D-Bus types (bas), q, and s, or None
        """
        objects = StratisDbus.get_managed_objects().items()

        pool_objects = {
            path: obj_data[StratisDbus._POOL_IFACE]
            for path, obj_data in objects
            if StratisDbus._POOL_IFACE in obj_data
            and obj_data[StratisDbus._POOL_IFACE]["Name"].startswith(_TEST_PREF)
        }
        fs_objects = {
            path: obj_data[StratisDbus._FS_IFACE]
            for path, obj_data in objects
            if StratisDbus._FS_IFACE in obj_data
            and obj_data[StratisDbus._FS_IFACE]["Name"].startswith(_TEST_PREF)
        }

        pool_paths = [
            path
            for path, pool_obj in pool_objects.items()
            if pool_obj["Name"] == pool_name
        ]
        if len(pool_paths) != 1:
            return None

        pool_path = pool_paths[0]

        fs_paths = [
            path
            for path, fs_obj in fs_objects.items()
            if fs_obj["Name"] == fs_name and fs_obj["Pool"] == pool_path
        ]
        if len(fs_paths) != 1:
            return None

        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, pool_path),
            StratisDbus._POOL_IFACE,
        )
        return iface.DestroyFilesystems(fs_paths, timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def fs_rename(pool_name, fs_name, fs_name_rename):
        """
        Rename a filesystem
        :param str pool_name: The name of the filesystem's pool
        :param str fs_name: The name of the filesystem to be renamed
        :param str fs_name_rename: The new name that the snapshot will have
        :return: The return values of the SetName call, or None
        :rtype: The D-Bus types (bs), q, and s, or None
        """
        objects = StratisDbus.get_managed_objects().items()

        pool_objects = {
            path: obj_data[StratisDbus._POOL_IFACE]
            for path, obj_data in objects
            if StratisDbus._POOL_IFACE in obj_data
            and obj_data[StratisDbus._POOL_IFACE]["Name"].startswith(_TEST_PREF)
        }

        pool_paths = [
            path
            for path, pool_obj in pool_objects.items()
            if pool_obj["Name"] == pool_name
        ]

        if len(pool_paths) != 1:
            return None

        pool_path = pool_paths[0]

        fs_objects = {
            path: obj_data[StratisDbus._FS_IFACE]
            for path, obj_data in objects
            if StratisDbus._FS_IFACE in obj_data
            and obj_data[StratisDbus._FS_IFACE]["Name"].startswith(_TEST_PREF)
        }

        fs_paths = [
            path
            for path, fs_obj in fs_objects.items()
            if fs_obj["Name"] == fs_name and fs_obj["Pool"] == pool_path
        ]

        if len(fs_paths) != 1:
            return None

        fs_path = fs_paths[0]

        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, fs_path),
            StratisDbus._FS_IFACE,
        )
        return iface.SetName(fs_name_rename, timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def fs_snapshot(pool_path, fs_path, snapshot_name):
        """
        Snapshot a filesystem
        :param str pool_path: The object path of the pool containing the fs
        :param str fs_name: The object path of the filesystem to snapshot
        :param str snapshot_name: The name of the snapshot to be made
        :return: The return values of the SnapshotFilesystem call
        :rtype: The D-Bus types (bo), q, and s
        """
        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, pool_path),
            StratisDbus._POOL_IFACE,
        )
        return iface.SnapshotFilesystem(
            fs_path, snapshot_name, timeout=StratisDbus._TIMEOUT
        )

    @staticmethod
    def get_report(report_name):
        """
        Get the report with the given name.
        :param str report_name: The name of the report
        :return: The JSON report as a string with a status code and string
        :rtype: The D-Bus types s, q, and s
        """
        iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, StratisDbus._TOP_OBJECT),
            StratisDbus._REPORT_IFACE,
        )
        return iface.GetReport(report_name, timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def get_engine_state_report():
        """
        Get the engine state report.
        :return: The JSON report as a string with a status code and string
        :rtype: The D-Bus types s, q, and s
        """
        manager_iface = dbus.Interface(
            StratisDbus._BUS.get_object(StratisDbus._BUS_NAME, StratisDbus._TOP_OBJECT),
            StratisDbus._MNGR_IFACE,
        )
        return manager_iface.EngineStateReport(timeout=StratisDbus._TIMEOUT)

    @staticmethod
    def reconnect():
        """
        Close and reopen bus connection.
        """
        StratisDbus._BUS.close()
        StratisDbus._BUS = dbus.SystemBus()

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
Tests of stratisd.
"""
# pylint: disable=too-many-lines

# isort: STDLIB
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from tempfile import NamedTemporaryFile

# isort: THIRDPARTY
import dbus

# isort: LOCAL
from testlib.dbus import StratisDbus, fs_n, p_n
from testlib.infra import MONITOR_DBUS_SIGNALS, KernelKey, StratisdSystemdStart
from testlib.utils import (
    create_relative_device_path,
    exec_command,
    exec_test_command,
    resolve_symlink,
    skip,
)

_ROOT = 0
_NON_ROOT = 1


def _raise_error_exception(return_code, msg, return_value_exists):
    """
    Check result of a D-Bus call in a context where it is in error
    if the call fails.
    :param int return_code: the return code from the D-Bus call
    :param str msg: the message returned on the D-Bus
    :param bool return_value_exists: whether a value representing
                                     a valid result was returned
    """
    if return_code != 0:
        raise RuntimeError(
            f"Expected return code of 0; actual return code: {return_code}, error_msg: {msg}"
        )

    if not return_value_exists:
        raise RuntimeError(
            "Result value was default or placeholder value and does not represent a valid result"
        )


def _skip_condition(num_devices_required):
    """
    Returns a function that raises a skipTest exception if a test should be
    skipped.
    """

    def the_func():
        if len(StratisCertify.DISKS) < num_devices_required:
            raise unittest.SkipTest(
                f"Test requires {num_devices_required} devices; "
                f"only {len(StratisCertify.DISKS)} available"
            )

    return the_func


def make_test_pool(pool_name, pool_disks):
    """
    Create a test pool that will later get destroyed
    :param str pool_name: Name of the pool to be created
    :param list pool_disks: List of disks with which the pool will be created
    :return: Object path of the created pool
    """
    (obj_path_exists, (obj_path, _)), return_code, msg = StratisDbus.pool_create(
        pool_name,
        pool_disks,
    )

    _raise_error_exception(return_code, msg, obj_path_exists)
    return obj_path


def make_test_filesystem(pool_path, fs_name):
    """
    Create a test filesystem that will later get destroyed
    :param str pool_path: Object path of a test pool
    :param str fs_name: Name of the filesystem to be created
    :return: Object path of the created filesystem
    """
    (
        (
            filesystems_created,
            (array_of_tuples_with_obj_paths_and_names),
        ),
        return_code,
        msg,
    ) = StratisDbus.fs_create(pool_path, fs_name)

    _raise_error_exception(return_code, msg, filesystems_created)
    exec_command(["udevadm", "settle"])
    return array_of_tuples_with_obj_paths_and_names[0][0]


def acquire_filesystem_symlink_targets(
    pool_name, filesystem_name, pool_path, filesystem_path
):
    """
    Acquire the symlink targets of the "/dev/stratis" symlink,
    and the equivalent device-mapper "/dev/mapper" link, generated
    via the info from get_managed_objects().
    NOTE: This may require a preceding "udevadm settle" call, to
    ensure that up-to-date pool and filesystem information is being
    collected.
    :param str pool_name: pool name
    :param str filesystem_name: filesystem name
    :param str pool_path: pool path
    :param str filesystem_path: filesystem path
    :return: str fsdevdest, str fsdevmapperlinkdest
    """
    objects = StratisDbus.get_managed_objects()

    pool_gmodata = objects[pool_path]
    pool_uuid = pool_gmodata[StratisDbus.POOL_IFACE]["Uuid"]
    filesystem_gmodata = objects[filesystem_path]
    filesystem_uuid = filesystem_gmodata[StratisDbus.FS_IFACE]["Uuid"]

    filesystem_devnode = "/dev/stratis/" + pool_name + "/" + filesystem_name

    fs_devmapperlinkstr = (
        "/dev/mapper/stratis-1-" + pool_uuid + "-thin-fs-" + filesystem_uuid
    )

    fsdevdest = resolve_symlink(filesystem_devnode)
    fsdevmapperlinkdest = resolve_symlink(fs_devmapperlinkstr)
    return fsdevdest, fsdevmapperlinkdest


class StratisCertify(unittest.TestCase):
    """
    Unit tests for the stratisd package.
    """

    def _inequality_test(self, result, expected_non_result):
        """
        :param object result: the result of a test
        :param object expected_non_result: a value which the result must
                                           not match, but which has the
                                           expected type
        """
        self.assertIsInstance(result, type(expected_non_result))
        self.assertNotEqual(result, expected_non_result)

    def _unittest_command(self, result, expected_return_code):
        """
        :param result: a tuple of the (optional) return value, the
                       return code, and the return message from a
                       D-Bus call
        :type result: tuple of object * dbus.UInt16 * str OR tuple
                      of dbus.UInt16 * str if there is no return value
        :raises: AssertionError if the actual return code is not
                 equal to the expected return code
        """
        if len(result) == 3:
            (_, return_code, msg) = result
        else:
            (return_code, msg) = result

        self.assertEqual(return_code, expected_return_code, msg=msg)

        self.assertEqual(
            type(return_code),
            type(expected_return_code),
            "return code has unexpected D-Bus signature",
        )


class StratisdCertify(
    StratisdSystemdStart, StratisCertify
):  # pylint: disable=too-many-public-methods
    """
    Tests on stratisd, the principal daemon.
    """

    def setUp(self):
        """
        Setup for an individual test.

        :return: None
        """
        super().setUp()

        if StratisCertify.monitor_dbus is True:
            command = [
                MONITOR_DBUS_SIGNALS,
                StratisDbus.BUS_NAME,
                StratisDbus.TOP_OBJECT,
                f"--top-interface={StratisDbus.MNGR_IFACE}",
            ]
            command.extend(
                f"--top-interface={intf}"
                for intf in StratisDbus.legacy_manager_interfaces()
            )
            # pylint: disable=consider-using-with
            try:
                self.trace = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                )
            except FileNotFoundError as err:
                raise RuntimeError("monitor_dbus_signals script not found.") from err

    def tearDown(self):
        """
        Tear down an individual test.  For now, this only stops the
        D-Bus trace.
        :return: None
        """
        trace = getattr(self, "trace", None)
        if trace is not None:
            time.sleep(1)
            self.trace.send_signal(signal.SIGINT)
            (stdoutdata, _) = self.trace.communicate()
            msg = stdoutdata.decode("utf-8")
            self.assertEqual(
                self.trace.returncode,
                0,
                "Error from monitor_dbus_signals: " + os.linesep + os.linesep + msg,
            )

    def _unittest_set_property(
        self, pool_path, param_iface, dbus_param, dbus_value, exception_name
    ):  # pylint: disable=too-many-arguments
        """
        :param pool_path: path to the pool
        :param param_iface: D-Bus interface to use for parameter
        :param dbus_param: D-Bus parameter to be set
        :param dbus_value: Desired value for the D-Bus parameter
        :param exception_name: Name of exception to expect
        :type exception_name: NoneType or str
        """
        try:
            StratisDbus.pool_set_property(
                pool_path, param_iface, dbus_param, dbus_value
            )

        except dbus.exceptions.DBusException as err:
            self.assertEqual(err.get_dbus_name(), exception_name)

        else:
            self.assertIsNone(exception_name)

    def _test_permissions(self, dbus_method, args, permissions, *, kwargs=None):
        """
        Test running dbus_method with and without root permissions.
        :param dbus_method: D-Bus method to be tested
        :type dbus_method: StratisDbus method
        :param args: the arguments to be passed to the D-Bus method
        :type args: list of objects
        :param bool permissions: True if dbus_method needs root permissions to succeed.
                                False if dbus_method should succeed without root permissions.
        :param kwargs: the keyword arguments to be passed to the D-Bus method
        :type kwargs: dict of objects or NoneType
        """
        kwargs = {} if kwargs is None else kwargs

        _permissions_flag = False

        euid = os.geteuid()
        if euid != _ROOT:
            raise RuntimeError(
                f"This process should be running as root, but the current euid is {euid}."
            )

        os.seteuid(_NON_ROOT)
        StratisDbus.reconnect()

        try:
            dbus_method(*args, **kwargs)
        except dbus.exceptions.DBusException as err:
            if err.get_dbus_name() == "org.freedesktop.DBus.Error.AccessDenied":
                _permissions_flag = True
            else:
                os.seteuid(_ROOT)
                raise err
        except Exception as err:
            os.seteuid(_ROOT)
            raise err

        os.seteuid(_ROOT)
        StratisDbus.reconnect()

        dbus_method(*args, **kwargs)

        self.assertEqual(_permissions_flag, permissions)

    def test_get_managed_objects(self):
        """
        Test that GetManagedObjects returns a dict w/out failure.
        """
        self.assertIsInstance(StratisDbus.get_managed_objects(), type({}))

    def test_get_managed_objects_permissions(self):
        """
        Test that GetManagedObjects succeeds when root permissions are dropped.
        """
        self._test_permissions(StratisDbus.get_managed_objects, [], False)

    def test_stratisd_version(self):
        """
        Test getting the daemon version.
        """
        self._inequality_test(StratisDbus.stratisd_version(), "")

    def test_stratisd_version_permissions(self):
        """
        Test that getting daemon version succeeds when permissions are dropped.
        """
        self._test_permissions(StratisDbus.stratisd_version, [], False)

    def test_pool_list_empty(self):
        """
        Test listing an non-existent pool.
        """
        result = StratisDbus.pool_list()
        self.assertEqual(result, [])

    def test_pool_list_permissions(self):
        """
        Test listing pool succeeds when root permissions are dropped.
        """
        self._test_permissions(StratisDbus.pool_list, [], False)

    def test_blockdev_list(self):
        """
        Test listing a blockdev.
        """
        result = StratisDbus.blockdev_list()
        self.assertEqual(result, [])

    def test_blockdev_list_permissions(self):
        """
        Test that listing blockdevs suceeds when root permissions are dropped.
        """
        self._test_permissions(StratisDbus.blockdev_list, [], False)

    def test_filesystem_list_empty(self):
        """
        Test listing an non-existent filesystem.
        """
        result = StratisDbus.fs_list()
        self.assertEqual(result, {})

    def test_filesystem_list_permissions(self):
        """
        Test that listing filesystem suceeds when root permissions are dropped.
        """
        self._test_permissions(StratisDbus.fs_list, [], False)

    def test_key_set_unset(self):
        """
        Test setting a key.
        """
        key_desc = "test-description"

        with NamedTemporaryFile(mode="w") as temp_file:
            temp_file.write("test-password")
            temp_file.flush()

            self._unittest_command(
                StratisDbus.set_key(key_desc, temp_file), dbus.UInt16(0)
            )

        self._unittest_command(StratisDbus.unset_key(key_desc), dbus.UInt16(0))

    def test_key_set_unset_permissions(self):
        """
        Test setting and unsetting a key fails when root permissions are dropped.
        """
        key_desc = "test-description"

        def set_key():
            """
            Set up a keyfile and set the value of the key in the kernel
            keyring.
            """
            with NamedTemporaryFile(mode="w") as temp_file:
                temp_file.write("test-password")
                temp_file.flush()

                StratisDbus.set_key(key_desc, temp_file)

        self._test_permissions(set_key, [], True)

        self._test_permissions(
            StratisDbus.unset_key, [], True, kwargs={"key_desc": key_desc}
        )

    @skip(_skip_condition(1))
    def test_pool_create(self):
        """
        Test creating a pool.
        """
        pool_name = p_n()

        self._unittest_command(
            StratisDbus.pool_create(pool_name, StratisCertify.DISKS),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(1))
    def test_pool_create_invalid_redundancy(self):
        """
        Test that creating a pool with an invalid redundancy value fails.
        """
        pool_name = p_n()
        redundancy = 20000

        self._unittest_command(
            StratisDbus.pool_create(
                pool_name, StratisCertify.DISKS, redundancy=redundancy
            ),
            dbus.UInt16(1),
        )

    @skip(_skip_condition(1))
    def test_pool_create_permissions(self):
        """
        Test that creating a pool fails when root permissions are dropped.
        """
        pool_name = p_n()
        self._test_permissions(
            StratisDbus.pool_create, [pool_name, StratisCertify.DISKS], True
        )

    @skip(_skip_condition(1))
    def test_pool_create_encrypted(self):
        """
        Test creating an encrypted pool.
        """
        with KernelKey("test-password") as key_desc:
            pool_name = p_n()

            self._unittest_command(
                StratisDbus.pool_create(
                    pool_name, StratisCertify.DISKS, key_desc=key_desc
                ),
                dbus.UInt16(0),
            )

    @skip(_skip_condition(1))
    def test_pool_create_no_overprovisioning(self):
        """
        Test creating a pool with no overprovisioning
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_set_property(
            pool_path,
            StratisDbus.POOL_IFACE,
            "Overprovisioning",
            dbus.Boolean(False),
            None,
        )

    @skip(_skip_condition(1))
    def test_pool_stop_started(self):
        """
        Test stopping a started pool
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(
            StratisDbus.pool_stop(pool_path),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(1))
    def test_pool_stop_stopped(self):
        """
        Test stopping a stopped pool
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(
            StratisDbus.pool_stop(pool_path),
            dbus.UInt16(0),
        )

        self._unittest_command(
            StratisDbus.pool_stop(pool_path),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(1))
    def test_pool_start_stopped(self):
        """
        Test starting a stopped pool
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        pool_uuid = StratisDbus.pool_uuid(pool_path)

        self._unittest_command(
            StratisDbus.pool_stop(pool_path),
            dbus.UInt16(0),
        )

        self._unittest_command(
            StratisDbus.pool_start(pool_uuid, "uuid"),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(1))
    def test_pool_start_by_name(self):
        """
        Test starting a stopped pool by its name
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(
            StratisDbus.pool_stop(pool_path),
            dbus.UInt16(0),
        )

        self._unittest_command(
            StratisDbus.pool_start(pool_name, "name"),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(1))
    def test_pool_start_started(self):
        """
        Test starting a started pool
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        pool_uuid = StratisDbus.pool_uuid(pool_path)

        self._unittest_command(
            StratisDbus.pool_start(pool_uuid, "uuid"),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(3))
    def test_pool_add_cache(self):
        """
        Test adding cache to a pool.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(
            StratisDbus.pool_init_cache(pool_path, StratisCertify.DISKS[1:2]),
            dbus.UInt16(0),
        )
        self._unittest_command(
            StratisDbus.pool_add_cache(pool_path, StratisCertify.DISKS[2:3]),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(2))
    def test_pool_add_cache_permissions(self):
        """
        Test that adding cache to pool fails when root permissions are dropped.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._test_permissions(
            StratisDbus.pool_init_cache,
            [pool_path, StratisCertify.DISKS[1:2]],
            True,
        )
        self._test_permissions(
            StratisDbus.pool_add_cache, [pool_path, StratisCertify.DISKS[2:3]], True
        )

    @skip(_skip_condition(2))
    def test_pool_create_after_cache(self):
        """
        Test creating existing pool after cache was added
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(
            StratisDbus.pool_init_cache(pool_path, StratisCertify.DISKS[1:2]),
            dbus.UInt16(0),
        )
        self._unittest_command(
            StratisDbus.pool_create(pool_name, StratisCertify.DISKS[0:1]),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(2))
    def test_pool_add_data_after_cache(self):
        """
        Test adding a data device after a cache is created.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(
            StratisDbus.pool_init_cache(pool_path, StratisCertify.DISKS[1:2]),
            dbus.UInt16(0),
        )
        self._unittest_command(
            StratisDbus.pool_add_data(pool_path, StratisCertify.DISKS[0:1]),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(3))
    def test_pool_add_different_data_after_cache(self):
        """
        Test adding a different data device after a cache is created.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(
            StratisDbus.pool_init_cache(pool_path, StratisCertify.DISKS[1:2]),
            dbus.UInt16(0),
        )
        self._unittest_command(
            StratisDbus.pool_add_data(pool_path, StratisCertify.DISKS[2:3]),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(2))
    def test_pool_create_with_cache(self):
        """
        Test creating existing pool with device already used by cache fails
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(
            StratisDbus.pool_init_cache(pool_path, StratisCertify.DISKS[1:2]),
            dbus.UInt16(0),
        )
        self._unittest_command(
            StratisDbus.pool_create(pool_name, StratisCertify.DISKS[0:2]),
            dbus.UInt16(1),
        )

    @skip(_skip_condition(3))
    def test_pool_add_data(self):
        """
        Test adding data to a pool.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:2])

        self._unittest_command(
            StratisDbus.pool_add_data(pool_path, StratisCertify.DISKS[2:3]),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(3))
    def test_pool_add_data_relative_path(self):
        """
        Test adding data to a pool with a relative device path.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:2])

        add_device = StratisCertify.DISKS[2]
        relative_device = create_relative_device_path(add_device)
        relative_device_list = [add_device, relative_device]
        self._unittest_command(
            StratisDbus.pool_add_data(pool_path, relative_device_list),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(3))
    def test_pool_add_data_permissions(self):
        """
        Test that adding data to a pool fails when root permissions are dropped.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:2])

        self._test_permissions(
            StratisDbus.pool_add_data, [pool_path, StratisCertify.DISKS[2:3]], True
        )

    @skip(_skip_condition(1))
    def test_pool_list_not_empty(self):
        """
        Test listing an non-existent pool.
        """
        pool_name = p_n()
        make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._inequality_test(StratisDbus.pool_list(), [])

    @skip(_skip_condition(1))
    def test_pool_create_same_name_and_devices(self):
        """
        Test creating a pool that already exists with the same devices.
        """
        pool_name = p_n()
        make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(
            StratisDbus.pool_create(pool_name, StratisCertify.DISKS[0:1]),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(3))
    def test_pool_create_same_name_different_devices(self):
        """
        Test creating a pool that already exists with different devices.
        """
        pool_name = p_n()
        make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(
            StratisDbus.pool_create(pool_name, StratisCertify.DISKS[1:3]),
            dbus.UInt16(1),
        )

    @skip(_skip_condition(1))
    def test_pool_destroy(self):
        """
        Test destroying a pool.
        """
        pool_name = p_n()
        make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(StratisDbus.pool_destroy(pool_name), dbus.UInt16(0))

        self.assertEqual(StratisDbus.fs_list(), {})

    @skip(_skip_condition(1))
    def test_pool_destroy_permissions(self):
        """
        Test that destroying a pool fails when root permissions are dropped.
        """
        pool_name = p_n()
        make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._test_permissions(StratisDbus.pool_destroy, [pool_name], True)

    @skip(_skip_condition(1))
    def test_pool_set_fs_limit_too_low(self):
        """
        Test setting the pool filesystem limit too low fails.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_set_property(
            pool_path,
            StratisDbus.POOL_IFACE,
            "FsLimit",
            dbus.UInt64(0),
            "org.freedesktop.DBus.Error.Failed",
        )

    @skip(_skip_condition(1))
    def test_filesystem_create(self):
        """
        Test creating a filesystem.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()

        self._unittest_command(
            StratisDbus.fs_create(pool_path, fs_name), dbus.UInt16(0)
        )

    @skip(_skip_condition(1))
    def test_filesystem_create_specified_size(self):
        """
        Test creating a filesystem with a specified size.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()

        self._unittest_command(
            StratisDbus.fs_create(pool_path, fs_name, fs_size="8796093022208"),
            dbus.UInt16(0),
        )

    @skip(_skip_condition(1))
    def test_filesystem_create_specified_size_toosmall(self):
        """
        Test creating a filesystem with a specified size that is too small.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()

        self._unittest_command(
            StratisDbus.fs_create(pool_path, fs_name, fs_size="536866816"),
            dbus.UInt16(1),
        )

    @skip(_skip_condition(1))
    def test_filesystem_create_permissions(self):
        """
        Test that creating a filesystem fails when root permissions are dropped.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()

        self._test_permissions(StratisDbus.fs_create, [pool_path, fs_name], True)

    @skip(_skip_condition(1))
    def test_filesystem_udev_symlink_create(self):
        """
        Test the udev symlink creation for filesystem devices.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        filesystem_path = make_test_filesystem(pool_path, fs_name)

        fsdevdest, fsdevmapperlinkdest = acquire_filesystem_symlink_targets(
            pool_name, fs_name, pool_path, filesystem_path
        )
        self.assertEqual(fsdevdest, fsdevmapperlinkdest)

    @skip(_skip_condition(1))
    def test_filesystem_udev_symlink_fsrename(self):
        """
        Test the udev symlink creation for filesystem devices after fs rename.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        filesystem_path = make_test_filesystem(pool_path, fs_name)

        fs_name_rename = fs_n()

        self._unittest_command(
            StratisDbus.fs_rename(pool_name, fs_name, fs_name_rename), dbus.UInt16(0)
        )
        # Settle after rename, to allow udev to recognize the fs rename
        exec_command(["udevadm", "settle"])

        fsdevdest, fsdevmapperlinkdest = acquire_filesystem_symlink_targets(
            pool_name, fs_name_rename, pool_path, filesystem_path
        )
        self.assertEqual(fsdevdest, fsdevmapperlinkdest)

    @skip(_skip_condition(1))
    def test_filesystem_udev_symlink_poolrename(self):
        """
        Test the udev symlink creation for filesystem devices after pool rename.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        filesystem_path = make_test_filesystem(pool_path, fs_name)

        pool_name_rename = p_n()

        self._unittest_command(
            StratisDbus.pool_rename(pool_name, pool_name_rename), dbus.UInt16(0)
        )
        # Settle after rename, to allow udev to recognize the fs rename
        exec_command(["udevadm", "settle"])

        fsdevdest, fsdevmapperlinkdest = acquire_filesystem_symlink_targets(
            pool_name_rename, fs_name, pool_path, filesystem_path
        )
        self.assertEqual(fsdevdest, fsdevmapperlinkdest)

    @skip(_skip_condition(1))
    def test_filesystem_udev_symlink_fsrename_poolrename(self):
        """
        Test the udev symlink creation for filesystem devices after fs and pool rename.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        filesystem_path = make_test_filesystem(pool_path, fs_name)

        fs_name_rename = fs_n()

        self._unittest_command(
            StratisDbus.fs_rename(pool_name, fs_name, fs_name_rename), dbus.UInt16(0)
        )
        # Settle after rename, to allow udev to recognize the filesystem rename
        exec_command(["udevadm", "settle"])

        pool_name_rename = p_n()

        self._unittest_command(
            StratisDbus.pool_rename(pool_name, pool_name_rename), dbus.UInt16(0)
        )
        # Settle after rename, to allow udev to recognize the pool rename
        exec_command(["udevadm", "settle"])

        fsdevdest, fsdevmapperlinkdest = acquire_filesystem_symlink_targets(
            pool_name_rename, fs_name_rename, pool_path, filesystem_path
        )
        self.assertEqual(fsdevdest, fsdevmapperlinkdest)

    @skip(_skip_condition(1))
    def test_filesystem_rename(self):
        """
        Test renaming a filesystem.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        make_test_filesystem(pool_path, fs_name)

        fs_name_rename = fs_n()

        self._unittest_command(
            StratisDbus.fs_rename(pool_name, fs_name, fs_name_rename), dbus.UInt16(0)
        )

    @skip(_skip_condition(1))
    def test_filesystem_rename_permissions(self):
        """
        Test that renaming a filesystem fails when root permissions are dropped.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        make_test_filesystem(pool_path, fs_name)

        fs_name_rename = fs_n()

        self._test_permissions(
            StratisDbus.fs_rename, [pool_name, fs_name, fs_name_rename], True
        )

    @skip(_skip_condition(1))
    def test_filesystem_rename_same_name(self):
        """
        Test renaming a filesystem.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        make_test_filesystem(pool_path, fs_name)

        self._unittest_command(
            StratisDbus.fs_rename(pool_name, fs_name, fs_name), dbus.UInt16(0)
        )

    @skip(_skip_condition(1))
    def test_filesystem_snapshot(self):
        """
        Test snapshotting a filesystem.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        fs_path = make_test_filesystem(pool_path, fs_name)

        snapshot_name = fs_n()

        self._unittest_command(
            StratisDbus.fs_snapshot(pool_path, fs_path, snapshot_name), dbus.UInt16(0)
        )

    @skip(_skip_condition(1))
    def test_filesystem_snapshot_permissions(self):
        """
        Test snapshotting a filesystem fails when root permissions are dropped.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        fs_path = make_test_filesystem(pool_path, fs_name)

        snapshot_name = fs_n()

        self._test_permissions(
            StratisDbus.fs_snapshot, [pool_path, fs_path, snapshot_name], True
        )

    @skip(_skip_condition(1))
    def test_filesystem_list_not_empty(self):
        """
        Test listing an existent filesystem.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        make_test_filesystem(pool_path, fs_name)

        self._inequality_test(StratisDbus.fs_list(), {})

    @skip(_skip_condition(1))
    def test_filesystem_create_same_name(self):
        """
        Test creating a filesystem that already exists.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        make_test_filesystem(pool_path, fs_name)

        self._unittest_command(
            StratisDbus.fs_create(pool_path, fs_name), dbus.UInt16(0)
        )

    @skip(_skip_condition(1))
    def test_filesystem_destroy(self):
        """
        Test destroying a filesystem.
        """
        pool_name = p_n()
        pool_path = make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        fs_name = fs_n()
        make_test_filesystem(pool_path, fs_name)

        self._unittest_command(
            StratisDbus.fs_destroy(pool_name, fs_name), dbus.UInt16(0)
        )

        self.assertEqual(StratisDbus.fs_list(), {})

    def test_get_report(self):
        """
        Test getting a valid and invalid report.
        """
        (result, return_code, _) = StratisDbus.get_report("stopped_pools")
        self._inequality_test(result, dbus.String(""))
        self.assertEqual(return_code, dbus.UInt16(0))
        # Test that we have received valid JSON.
        json.loads(result)

        (result, return_code, _) = StratisDbus.get_report("invalid_report")
        self.assertEqual(result, dbus.String(""))
        self._inequality_test(return_code, dbus.UInt16(0))

    def test_get_report_permissions(self):
        """
        Test that getting a valid report succeeds when root permissions are dropped.
        """
        self._test_permissions(StratisDbus.get_report, ["errored_pool_report"], False)

    def test_engine_state_report(self):
        """
        Test getting a valid engine state report
        """

        (result, return_code, _) = StratisDbus.get_engine_state_report()
        self._inequality_test(result, dbus.String(""))
        self.assertEqual(return_code, dbus.UInt16(0))
        # Test that we have received valid JSON.
        json.loads(result)

    def test_engine_state_report_permissions(self):
        """
        Test that getting a valid engine state report succeeds when root permissions are dropped.
        """
        self._test_permissions(StratisDbus.get_engine_state_report, [], False)

    def test_get_keys(self):
        """
        Test getting the Stratis keys in the kernel keyring.
        """

        (_, return_code, _) = StratisDbus.get_keys()
        self.assertEqual(return_code, dbus.UInt16(0))

    def test_get_keys_permissions(self):
        """
        Test that ListKeys method can be invoked when permissions are dropped.
        """
        self._test_permissions(StratisDbus.get_keys, [], False)


class StratisdManPageCertify(StratisCertify):
    """
    Tests that check that documentation is properly installed.
    """

    def test_access_stratisd_man_page(self):
        """
        Test accessing the stratisd manual page file.
        """
        (return_code, stdout, stderr) = exec_test_command(
            ["man", "--where", "stratisd"]
        )
        self.assertEqual(return_code, 0)
        self.assertEqual(stderr, "")
        self._inequality_test(stdout, "")


class PredictusageCertify(StratisCertify):
    """
    Tests that check that the stratis-predict-usage executable is installed,
    responding to expected commands, and has well formatted output.
    """

    def test_predict_pool_usage(self):
        """
        Test pool subcommand.
        """

        (return_code, stdout, stderr) = exec_test_command(
            ["stratis-predict-usage", "pool", "--device-size=1099511627776"]
        )

        self.assertEqual(return_code, 0)
        self.assertEqual(stderr, "")
        json.loads(stdout)

    def test_predict_filesystem_usage(self):
        """
        Test filesystem subcommand.
        """

        (return_code, stdout, stderr) = exec_test_command(
            ["stratis-predict-usage", "filesystem", "--filesystem-size=1099511627776"]
        )

        self.assertEqual(return_code, 0)
        self.assertEqual(stderr, "")
        json.loads(stdout)


class StratisMinCertify(StratisCertify):
    """
    Tests for stratis-min
    """

    def test_stratis_min_is_installed(self):
        """
        Verify that stratis-min can return a version string.
        """
        (return_code, stdout, stderr) = exec_test_command(["stratis-min", "--version"])
        self.assertEqual(return_code, 0)
        self.assertEqual(stderr, "")
        self._inequality_test(stdout, "")


class StratisdCmdCertify(StratisCertify):
    """
    Tests for the stratisd command-line
    """

    def test_stratisd_version(self):
        """
        Verify that stratisd can return a version string.
        """
        (return_code, stdout, stderr) = exec_test_command(
            ["/usr/libexec/stratisd", "--version"]
        )
        self.assertEqual(return_code, 0)
        self.assertEqual(stderr, "")
        self._inequality_test(stdout, "")


def main():
    """
    The main method.
    """
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument(
        "--disk",
        action="append",
        dest="DISKS",
        default=[],
        help="disks to use, a minimum of 3 in order to run every test",
    )
    argument_parser.add_argument(
        "--monitor-dbus", help="Monitor D-Bus", action="store_true"
    )
    parsed_args, unittest_args = argument_parser.parse_known_args()
    StratisCertify.DISKS = parsed_args.DISKS
    StratisCertify.monitor_dbus = parsed_args.monitor_dbus
    print(f"Using block device(s) for tests: {StratisCertify.DISKS}")
    unittest.main(argv=sys.argv[:1] + unittest_args)


if __name__ == "__main__":
    main()

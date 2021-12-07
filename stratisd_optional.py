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
Tests of stratisd that are optional.
"""

# isort: STDLIB
import argparse
import json
import os
import sys
import time
import unittest
from tempfile import NamedTemporaryFile

# isort: THIRDPARTY
import dbus

# isort: LOCAL
from testlib.dbus import StratisDbus, fs_n, p_n
from testlib.infra import KernelKey, clean_up
from testlib.utils import (
    create_relative_device_path,
    exec_command,
    exec_test_command,
    process_exists,
    resolve_symlink,
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
            "Expected return code of 0; actual return code: %s, error_msg: %s"
            % (return_code, msg)
        )

    if not return_value_exists:
        raise RuntimeError(
            "Result value was default or placeholder value and does not represent a valid result"
        )


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


class StratisdCertify(StratisCertify):  # pylint: disable=too-many-public-methods
    """
    Tests on stratisd, the principal daemon.
    """

    def setUp(self):
        """
        Setup for an individual test.
        * Register a cleanup action, to be run if the test fails.
        * Ensure that stratisd is running via systemd.
        * Use the running stratisd instance to destroy any existing
        Stratis filesystems, pools, etc.
        * Call "udevadm settle" so udev database can be updated with changes
        to Stratis devices.
        :return: None
        """
        self.addCleanup(clean_up)

        if process_exists("stratisd") is None:
            exec_command(["systemctl", "start", "stratisd"])
            time.sleep(20)

        clean_up()

        time.sleep(1)
        exec_command(["udevadm", "settle"])

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
                "This process should be running as root, but the current euid is %d."
                % euid
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

    def test_pool_create(self):
        """
        Test creating a pool.
        """
        pool_name = p_n()

        self._unittest_command(
            StratisDbus.pool_create(pool_name, StratisCertify.DISKS),
            dbus.UInt16(0),
        )

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

    def test_pool_destroy(self):
        """
        Test destroying a pool.
        """
        pool_name = p_n()
        make_test_pool(pool_name, StratisCertify.DISKS[0:1])

        self._unittest_command(StratisDbus.pool_destroy(pool_name), dbus.UInt16(0))

        self.assertEqual(StratisDbus.fs_list(), {})

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
    parsed_args, unittest_args = argument_parser.parse_known_args()
    StratisCertify.DISKS = parsed_args.DISKS
    print("Using block device(s) for tests: %s" % StratisCertify.DISKS)
    unittest.main(argv=sys.argv[:1] + unittest_args)


if __name__ == "__main__":
    main()

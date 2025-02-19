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
Tests of the stratis CLI.
"""
# pylint: disable=too-many-lines

# isort: STDLIB
import argparse
import os
import subprocess
import sys
import unittest

# isort: LOCAL
from testlib.dbus import StratisDbus, fs_n, p_n
from testlib.infra import (
    DbusMonitor,
    KernelKey,
    MountPointManager,
    PostTestCheck,
    RunPostTestChecks,
    StratisdSystemdStart,
    SymlinkMonitor,
    SysfsMonitor,
)
from testlib.utils import (
    RandomKeyTmpFile,
    create_relative_device_path,
    exec_test_command,
    revision_number_type,
    skip,
)

_STRATIS_CLI = os.getenv("STRATIS_CLI", "/usr/bin/stratis")
_ROOT = 0
_NON_ROOT = 1


def _raise_error_exception(return_code, msg):
    """
    Check result of a CLI call in a context where it is in error
    if the call fails.
    :param int return_code: the return code from the D-Bus call
    :param str msg: the message returned on the D-Bus
    """
    if return_code != 0:
        raise RuntimeError(
            f"Expected return code of 0; actual return code: {return_code}, error_msg: {msg}"
        )


def _skip_condition(num_devices_required):
    def the_func():
        if len(StratisCliCertify.DISKS) < num_devices_required:
            raise unittest.SkipTest(
                f"Test requires {num_devices_required} devices; "
                f"only {len(StratisCliCertify.DISKS)} available"
            )

    return the_func


def make_test_pool(pool_disks, key_desc=None):
    """
    Create a test pool that will later get destroyed
    :param list pool_disks: List of disks with which the pool will be created
    :param key_desc: Key description associated with the key with which to encrypt
                     the block devices in the pool
    :type key_desc: str or NoneType
    :return: Name of the created pool
    """
    pool_name = p_n()

    args = [_STRATIS_CLI, "pool", "create"]
    if key_desc is not None:
        args += ["--key-desc", key_desc]
    args += [pool_name] + pool_disks

    (return_code, _, stderr) = exec_test_command(args, settle=True)

    _raise_error_exception(return_code, stderr)
    return pool_name


def make_test_filesystem(pool_name):
    """
    Create a test filesystem that will later get destroyed
    :param pool_name: Name of a test pool
    :return: Name of the created filesystem
    """
    filesystem_name = fs_n()
    (return_code, _, stderr) = exec_test_command(
        [_STRATIS_CLI, "filesystem", "create", pool_name, filesystem_name]
    )

    _raise_error_exception(return_code, stderr)
    return filesystem_name


class StratisCertify(unittest.TestCase):
    """
    Unit test base class.
    """

    def _unittest_command(
        self, args, exp_exit_code, exp_stderr_is_empty, exp_stdout_is_empty
    ):
        """
        Execute a test command and make assertions about the exit code, stderr, and stdout
        :param list args: The arguments needed to execute the Stratis command being tested
        :type args: List of str
        :param exp_exit_code: The expected exit code, 0, 1, or 2
        :param bool exp_stderr_is_empty: True if stderr is expected to be empty, otherwise False
        :param bool exp_stdout_is_empty: True if stdout is expected to be empty, otherwise False
        :return: None
        """
        exit_code, stdout, stderr = exec_test_command(args)

        self.assertEqual(
            exit_code,
            exp_exit_code,
            msg=os.linesep.join(["", f"stdout: {stdout}", f"stderr: {stderr}"]),
        )

        if exp_stderr_is_empty:
            self.assertEqual(stderr, "")
        else:
            self.assertNotEqual(stderr, "")

        if exp_stdout_is_empty:
            self.assertEqual(stdout, "")
        else:
            self.assertNotEqual(stdout, "")


class StratisCliVersionCertify(StratisCertify):
    """
    Tests that check that the stratis command returns a version.
    """

    def test_stratis_command_version(self):
        """
        Test running "stratis --version".
        """
        self._unittest_command(["stratis", "--version"], 0, True, False)


class StratisCliManPageCertify(StratisCertify):
    """
    Tests that check that documentation is properly installed.
    """

    def test_access_stratis_man_page(self):
        """
        Test accessing the stratis-cli manual page file.
        """
        self._unittest_command(["man", "--where", "stratis"], 0, True, False)


class StratisCliCertify(
    StratisdSystemdStart, StratisCertify
):  # pylint: disable=too-many-public-methods
    """
    Unit tests for the stratis-cli package.
    """

    def setUp(self):
        """
        Setup for an individual test.

        :return: None
        """
        super().setUp()

        self._post_test_checks = RunPostTestChecks(test_id=self.id())

    def tearDown(self):
        """
        Tear down an individual test.

        :return: None
        """
        super().tearDown()

        self._post_test_checks.teardown()

    def _test_permissions(self, command_line, permissions, exp_stdout_empty):
        """
        Test running CLI commands with and without root permissions.
        :param list command_line: The arguments needed to execute the Stratis command being tested
        :type command_line: List of str
        :param bool permissions: True if the Stratis command needs root permissions to succeed,
                                    otherwise False.
        :param bool exp_stdout_empty: True if stdout is expected to be empty
                                        when Stratis command succeeds.
        """

        os.seteuid(_NON_ROOT)
        try:
            if permissions:
                self._unittest_command(command_line, 1, False, True)
            else:
                self._unittest_command(command_line, 0, True, exp_stdout_empty)
        except Exception as err:
            os.seteuid(_ROOT)
            raise err

        os.seteuid(_ROOT)

        if permissions:
            self._unittest_command(command_line, 0, True, exp_stdout_empty)

    def test_stratisd_version(self):
        """
        Test getting the daemon version.
        """
        self._unittest_command([_STRATIS_CLI, "daemon", "version"], 0, True, False)

    def test_stratisd_version_permissions(self):
        """
        Test that getting the daemon version succeeds with dropped permissions.
        """
        self._test_permissions([_STRATIS_CLI, "daemon", "version"], False, False)

    def test_stopped_pools_report(self):
        """
        Test getting the stopped_pools report.
        """
        self._unittest_command(
            [_STRATIS_CLI, "report", "stopped_pools"], 0, True, False
        )

    def test_stopped_pools_report_permissions(self):
        """
        Test getting the stopped_pools report succeeds with dropped permissions.
        """
        self._test_permissions([_STRATIS_CLI, "report", "stopped_pools"], False, False)

    def test_engine_state_report(self):
        """
        Test getting the engine_state_report.
        """
        self._unittest_command(
            [_STRATIS_CLI, "report", "engine_state_report"], 0, True, False
        )

    def test_engine_state_report_permissions(self):
        """
        Test getting the engine_state_report succeeds with dropped permissions.
        """
        self._test_permissions(
            [_STRATIS_CLI, "report", "engine_state_report"], False, False
        )

    def test_engine_state_report_default(self):
        """
        Test getting the engine_state_report when a report name is not provided.
        """
        self._unittest_command([_STRATIS_CLI, "report"], 0, True, False)

    def test_invalid_report(self):
        """
        Test getting an invalid report.
        """
        self._unittest_command([_STRATIS_CLI, "report", "invalid_name"], 2, False, True)

    def test_pool_list_empty(self):
        """
        Test listing a non-existent pool.
        """
        self._unittest_command([_STRATIS_CLI, "pool", "list"], 0, True, False)

    def test_filesystem_list_empty(self):
        """
        Test listing an non-existent filesystem.
        """
        self._unittest_command([_STRATIS_CLI, "filesystem", "list"], 0, True, False)

    def test_key_set_unset(self):
        """
        Test setting and unsetting a key.
        """
        with RandomKeyTmpFile() as fname:
            self._unittest_command(
                [_STRATIS_CLI, "key", "set", "testkey1", "--keyfile-path", fname],
                0,
                True,
                True,
            )

        self._unittest_command(
            [_STRATIS_CLI, "key", "unset", "testkey1"], 0, True, True
        )

    def test_key_set_reset_unset(self):
        """
        Test setting, resetting, and unsetting a key.
        """
        with RandomKeyTmpFile() as first_fname, RandomKeyTmpFile() as second_fname:
            self._unittest_command(
                [_STRATIS_CLI, "key", "set", "testkey2", "--keyfile-path", first_fname],
                0,
                True,
                True,
            )
            self._unittest_command(
                [
                    _STRATIS_CLI,
                    "key",
                    "reset",
                    "testkey2",
                    "--keyfile-path",
                    second_fname,
                ],
                0,
                True,
                True,
            )

        self._unittest_command(
            [_STRATIS_CLI, "key", "unset", "testkey2"], 0, True, True
        )

    def test_key_set_reset_unset_permissions(self):
        """
        Test setting, resetting, and unsetting a key fails with dropped permissions.
        """
        with RandomKeyTmpFile() as first_fname, RandomKeyTmpFile() as second_fname:
            self._test_permissions(
                [_STRATIS_CLI, "key", "set", "testkey2", "--keyfile-path", first_fname],
                True,
                True,
            )
            self._test_permissions(
                [
                    _STRATIS_CLI,
                    "key",
                    "reset",
                    "testkey2",
                    "--keyfile-path",
                    second_fname,
                ],
                True,
                True,
            )

        self._test_permissions([_STRATIS_CLI, "key", "unset", "testkey2"], True, True)

    @skip(_skip_condition(1))
    def test_debug_uevent(self):
        """
        Test sending a debug uevent.
        """
        self._unittest_command(
            [_STRATIS_CLI, "debug", "uevent", StratisCliCertify.DISKS[0]],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_create(self):
        """
        Test creating a pool.
        """
        pool_name = p_n()
        self._unittest_command(
            [_STRATIS_CLI, "pool", "create", pool_name, StratisCliCertify.DISKS[0]],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_create_permissions(self):
        """
        Test creating a pool fails with dropped permissions.
        """
        pool_name = p_n()
        self._test_permissions(
            [_STRATIS_CLI, "pool", "create", pool_name, StratisCliCertify.DISKS[0]],
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_create_encrypted(self):
        """
        Test creating an encrypted pool.
        """
        with KernelKey("test-password") as key_desc:
            pool_name = p_n()
            self._unittest_command(
                [
                    _STRATIS_CLI,
                    "pool",
                    "create",
                    "--key-desc",
                    key_desc,
                    pool_name,
                    StratisCliCertify.DISKS[0],
                ],
                0,
                True,
                True,
            )

    @skip(_skip_condition(1))
    def test_pool_create_encrypted_multiple_keys(self):
        """
        Test creating an encrypted pool with multiple keys bound.
        """
        with KernelKey("test-password") as key_desc:
            pool_name = p_n()
            self._unittest_command(
                [
                    _STRATIS_CLI,
                    "pool",
                    "create",
                    "--key-desc",
                    key_desc,
                    pool_name,
                    StratisCliCertify.DISKS[0],
                ],
                0,
                True,
                True,
            )
            self._unittest_command(
                [
                    _STRATIS_CLI,
                    "pool",
                    "bind",
                    "keyring",
                    pool_name,
                    key_desc,
                ],
                0,
                True,
                True,
            )

    @skip(_skip_condition(3))
    def test_pool_create_encrypted_with_cache(self):
        """
        Test creating an encrypted pool with cache.
        """
        with KernelKey("test-password") as key_desc:
            pool_name = make_test_pool(StratisCliCertify.DISKS[0:2], key_desc)
            self._unittest_command(
                [
                    _STRATIS_CLI,
                    "pool",
                    "init-cache",
                    pool_name,
                    StratisCliCertify.DISKS[2],
                ],
                0,
                True,
                True,
            )

    @skip(_skip_condition(1))
    def test_pool_create_no_overprovision(self):
        """
        Test creating a pool with no overprovisioning.
        """
        pool_name = p_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "create",
                pool_name,
                "--no-overprovision",
                StratisCliCertify.DISKS[0],
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_create_integrity_journal_size(self):
        """
        Test creating a pool with an integrity journal size.
        """
        pool_name = p_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "create",
                pool_name,
                "--journal-size=64MiB",
                StratisCliCertify.DISKS[0],
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_create_integrity_tag_spec(self):
        """
        Test creating a pool with an integrity tag specification.
        """
        pool_name = p_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "create",
                pool_name,
                "--tag-spec=32b",
                StratisCliCertify.DISKS[0],
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_create_integrity_no_preallocation(self):
        """
        Test creating a pool with no integrity pre-allocation.
        """
        pool_name = p_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "create",
                pool_name,
                "--integrity=no",
                StratisCliCertify.DISKS[0],
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_list_not_empty(self):
        """
        Test listing an existent pool.
        """
        make_test_pool(StratisCliCertify.DISKS[0:1])
        self._unittest_command([_STRATIS_CLI, "pool", "list"], 0, True, False)

    @skip(_skip_condition(1))
    def test_pool_list_not_empty_permissions(self):
        """
        Test listing an existent pool succeeds with dropped permissions.
        """
        make_test_pool(StratisCliCertify.DISKS[0:1])
        self._test_permissions([_STRATIS_CLI, "pool", "list"], False, False)

    @skip(_skip_condition(1))
    def test_pool_debug_get_metadata(self):
        """
        Test running "stratis pool debug get-metadata" on a pool.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        self._unittest_command(
            [_STRATIS_CLI, "pool", "debug", "get-metadata", f"--name={pool_name}"],
            0,
            True,
            False,
        )

    @skip(_skip_condition(1))
    def test_pool_debug_get_metadata_written(self):
        """
        Test running "stratis pool debug get-metadata" on a pool.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "debug",
                "get-metadata",
                "--written",
                f"--name={pool_name}",
            ],
            0,
            True,
            False,
        )

    def test_blockdev_list(self):
        """
        Test listing a blockdev.
        """
        self._unittest_command([_STRATIS_CLI, "blockdev", "list"], 0, True, False)

    def test_blockdev_list_permissions(self):
        """
        Test listing a blockdev succeeds with dropped permissions.
        """
        self._test_permissions([_STRATIS_CLI, "blockdev", "list"], False, False)

    @skip(_skip_condition(2))
    def test_pool_create_same_name(self):
        """
        Test creating a pool that already exists.
        """
        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "create",
                make_test_pool(StratisCliCertify.DISKS[0:1]),
                StratisCliCertify.DISKS[1],
            ],
            1,
            False,
            True,
        )

    @skip(_skip_condition(3))
    def test_pool_init_cache(self):
        """
        Test initializing the cache for a pool.
        """
        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "init-cache",
                make_test_pool(StratisCliCertify.DISKS[0:2]),
                StratisCliCertify.DISKS[2],
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(3))
    def test_pool_init_cache_permissions(self):
        """
        Test initializing the cache for a pool fails with dropped permissions.
        """
        self._test_permissions(
            [
                _STRATIS_CLI,
                "pool",
                "init-cache",
                make_test_pool(StratisCliCertify.DISKS[0:2]),
                StratisCliCertify.DISKS[2],
            ],
            True,
            True,
        )

    @skip(_skip_condition(3))
    def test_pool_init_cache_add_data(self):
        """
        Test initializing the cache for a pool, then adding a data device.
        """

        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])

        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "init-cache",
                pool_name,
                StratisCliCertify.DISKS[1],
            ],
            0,
            True,
            True,
        )

        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "add-data",
                pool_name,
                StratisCliCertify.DISKS[2],
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(3))
    def test_pool_add_data_init_cache(self):
        """
        Test adding data for a pool, then initializing the cache.
        """

        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = fs_n()

        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "create",
                pool_name,
                filesystem_name,
            ],
            0,
            True,
            True,
        )

        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "add-data",
                pool_name,
                StratisCliCertify.DISKS[1],
            ],
            0,
            True,
            True,
        )

        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "init-cache",
                pool_name,
                StratisCliCertify.DISKS[2],
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_stop_started(self):
        """
        Test stopping a started pool.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        self._unittest_command(
            [_STRATIS_CLI, "pool", "stop", f"--name={pool_name}"],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_start_by_name(self):
        """
        Test starting a stopped pool by its name.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])

        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "stop",
                f"--name={pool_name}",
            ],
            0,
            True,
            True,
        )

        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "start",
                "--name",
                pool_name,
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_destroy(self):
        """
        Test destroying a pool.
        """
        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "destroy",
                make_test_pool(StratisCliCertify.DISKS[0:1]),
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_destroy_permissions(self):
        """
        Test destroying a pool fails with dropped permissions.
        """
        self._test_permissions(
            [
                _STRATIS_CLI,
                "pool",
                "destroy",
                make_test_pool(StratisCliCertify.DISKS[0:1]),
            ],
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_create(self):
        """
        Test creating a filesystem.
        """
        filesystem_name = fs_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "create",
                make_test_pool(StratisCliCertify.DISKS[0:1]),
                filesystem_name,
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_create_specified_size(self):
        """
        Test creating a filesystem with a specified size.
        """
        filesystem_name = fs_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "create",
                make_test_pool(StratisCliCertify.DISKS[0:1]),
                filesystem_name,
                "--size=8TiB",
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_create_specified_size_toosmall(self):
        """
        Test creating a filesystem with a specified size that is too small.
        """
        filesystem_name = fs_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "create",
                make_test_pool(StratisCliCertify.DISKS[0:1]),
                filesystem_name,
                "--size=524284KiB",
            ],
            1,
            False,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_create_specified_size_limit(self):
        """
        Test creating a filesystem with a specified size limit.
        """
        filesystem_name = fs_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "create",
                make_test_pool(StratisCliCertify.DISKS[0:1]),
                filesystem_name,
                "--size=512GiB",
                "--size-limit=1024GiB",
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_create_specified_size_limit_toosmall(self):
        """
        Test creating a filesystem with a specified size limit that is too small.
        """
        filesystem_name = fs_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "create",
                make_test_pool(StratisCliCertify.DISKS[0:1]),
                filesystem_name,
                "--size=524288KiB",
                "--size-limit=524284KiB",
            ],
            1,
            False,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_set_size_limit(self):
        """
        Test setting a size limit on an existing filesystem.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "set-size-limit",
                pool_name,
                filesystem_name,
                "2TiB",
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_set_unset_size_limit(self):
        """
        Test setting and unsetting a size limit on an existing filesystem.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "set-size-limit",
                pool_name,
                filesystem_name,
                "2TiB",
            ],
            0,
            True,
            True,
        )
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "unset-size-limit",
                pool_name,
                filesystem_name,
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_set_size_limit_toosmall(self):
        """
        Test setting a size limit on an existing filesystem that is too small.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "set-size-limit",
                pool_name,
                filesystem_name,
                "1048572MiB",
            ],
            1,
            False,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_create_permissions(self):
        """
        Test creating a filesystem fails with dropped permissions.
        """
        filesystem_name = fs_n()
        self._test_permissions(
            [
                _STRATIS_CLI,
                "filesystem",
                "create",
                make_test_pool(StratisCliCertify.DISKS[0:1]),
                filesystem_name,
            ],
            True,
            True,
        )

    @skip(_skip_condition(2))
    def test_pool_add_data(self):
        """
        Test adding data to a pool.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        self._unittest_command(
            [_STRATIS_CLI, "pool", "add-data", pool_name, StratisCliCertify.DISKS[1]],
            0,
            True,
            True,
        )

    @skip(_skip_condition(2))
    def test_pool_add_data_relative_path(self):
        """
        Test adding data to a pool with a relative device path.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        add_device = StratisCliCertify.DISKS[1]
        relative_device = create_relative_device_path(add_device)
        self._unittest_command(
            [_STRATIS_CLI, "pool", "add-data", pool_name, add_device, relative_device],
            0,
            True,
            True,
        )

    @skip(_skip_condition(2))
    def test_pool_add_data_permissions(self):
        """
        Test adding data to a pool fails with dropped permissions.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        self._test_permissions(
            [_STRATIS_CLI, "pool", "add-data", pool_name, StratisCliCertify.DISKS[1]],
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_pool_set_fs_limit_too_low(self):
        """
        Test setting the pool filesystem limit too low fails.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        self._unittest_command(
            [_STRATIS_CLI, "pool", "set-fs-limit", pool_name, "0"], 1, False, True
        )

    @skip(_skip_condition(1))
    def test_pool_disable_overprovisioning(self):
        """
        Test disabling overprovisioning after the pool is created.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        self._unittest_command(
            [_STRATIS_CLI, "pool", "overprovision", pool_name, "no"], 0, True, True
        )

    @skip(_skip_condition(1))
    def test_filesystem_list_not_empty(self):
        """
        Test listing an existent filesystem.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        make_test_filesystem(pool_name)
        self._unittest_command([_STRATIS_CLI, "filesystem", "list"], 0, True, False)

    @skip(_skip_condition(1))
    def test_filesystem_list_not_empty_permissions(self):
        """
        Test listing an existent filesystem succeeds with dropped permissions.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        make_test_filesystem(pool_name)
        self._test_permissions([_STRATIS_CLI, "filesystem", "list"], False, False)

    @skip(_skip_condition(1))
    def test_filesystem_create_same_name(self):
        """
        Test creating a filesystem that already exists.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        self._unittest_command(
            [_STRATIS_CLI, "filesystem", "create", pool_name, filesystem_name],
            1,
            False,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_rename(self):
        """
        Test renaming a filesystem to a new name.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        fs_name_rename = fs_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "rename",
                pool_name,
                filesystem_name,
                fs_name_rename,
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_rename_permissions(self):
        """
        Test renaming a filesystem fails with dropped permissions.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        fs_name_rename = fs_n()
        self._test_permissions(
            [
                _STRATIS_CLI,
                "filesystem",
                "rename",
                pool_name,
                filesystem_name,
                fs_name_rename,
            ],
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_rename_same_name(self):
        """
        Test renaming a filesystem to the same name.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "rename",
                pool_name,
                filesystem_name,
                filesystem_name,
            ],
            1,
            False,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_snapshot(self):
        """
        Test snapshotting a filesystem.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        snapshot_name = fs_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "snapshot",
                pool_name,
                filesystem_name,
                snapshot_name,
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_snapshot_cancel_revert(self):
        """
        Test canceling a revert of a filesystem snapshot.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        snapshot_name = fs_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "snapshot",
                pool_name,
                filesystem_name,
                snapshot_name,
            ],
            0,
            True,
            True,
        )
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "schedule-revert",
                pool_name,
                snapshot_name,
            ],
            0,
            True,
            True,
        )
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "cancel-revert",
                pool_name,
                snapshot_name,
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_snapshot_schedule_revert(self):
        """
        Test scheduling a revert of a filesystem snapshot.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        snapshot_name = fs_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "snapshot",
                pool_name,
                filesystem_name,
                snapshot_name,
            ],
            0,
            True,
            True,
        )
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "schedule-revert",
                pool_name,
                snapshot_name,
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_snapshot_schedule_revert_noorigin_fail(self):
        """
        Test scheduling a revert of a filesystem with no origin, which should fail.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        snapshot_name = fs_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "snapshot",
                pool_name,
                filesystem_name,
                snapshot_name,
            ],
            0,
            True,
            True,
        )
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "schedule-revert",
                pool_name,
                filesystem_name,
            ],
            1,
            False,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_snapshot_destroy_filesystem(self):
        """
        Test snapshotting a filesystem, then destroying the original filesystem.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        snapshot_name = fs_n()
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "snapshot",
                pool_name,
                filesystem_name,
                snapshot_name,
            ],
            0,
            True,
            True,
        )
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "destroy",
                pool_name,
                filesystem_name,
            ],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_snapshot_permissions(self):
        """
        Test snapshotting a filesystem fails with dropped permissions.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        snapshot_name = fs_n()
        self._test_permissions(
            [
                _STRATIS_CLI,
                "filesystem",
                "snapshot",
                pool_name,
                filesystem_name,
                snapshot_name,
            ],
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_destroy(self):
        """
        Test destroying a filesystem.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        self._unittest_command(
            [_STRATIS_CLI, "filesystem", "destroy", pool_name, filesystem_name],
            0,
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_destroy_permissions(self):
        """
        Test destroying a filesystem fails with dropped permissions.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        self._test_permissions(
            [_STRATIS_CLI, "filesystem", "destroy", pool_name, filesystem_name],
            True,
            True,
        )

    @skip(_skip_condition(1))
    def test_filesystem_mount_and_write(self):
        """
        Test mount and write to filesystem.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)

        mountpoints = MountPointManager().mount(
            [os.path.join("/", "dev", "stratis", pool_name, filesystem_name)]
        )

        subprocess.check_call(
            [
                "dd",
                "if=/dev/urandom",
                f'of={os.path.join(mountpoints[0], "file1")}',
                "bs=4096",
                "count=256",
                "conv=fsync",
            ]
        )

    @skip(_skip_condition(1))
    def test_filesystem_debug_get_metadata(self):
        """
        Test running "stratis filesystem debug get-metadata" on a pool.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        filesystem_name = make_test_filesystem(pool_name)
        self._unittest_command(
            [
                _STRATIS_CLI,
                "filesystem",
                "debug",
                "get-metadata",
                pool_name,
                f"--fs-name={filesystem_name}",
            ],
            0,
            True,
            False,
        )

    @skip(_skip_condition(1))
    def test_pool_stop_stopped(self):
        """
        Test stopping a stopped pool fails.
        """
        pool_name = make_test_pool(StratisCliCertify.DISKS[0:1])
        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "stop",
                f"--name={pool_name}",
            ],
            0,
            True,
            True,
        )
        self._unittest_command(
            [
                _STRATIS_CLI,
                "pool",
                "stop",
                f"--name={pool_name}",
            ],
            1,
            False,
            True,
        )


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
        "--post-test-check",
        action="extend",
        choices=list(PostTestCheck),
        default=[],
        nargs="*",
        type=PostTestCheck,
    )

    argument_parser.add_argument(
        "--verify-sysfs", help="Verify /sys/class/block files", action="store_true"
    )

    argument_parser.add_argument(
        "--monitor-dbus", help="Monitor D-Bus", action="store_true"
    )

    argument_parser.add_argument(
        "--verify-devices", help="Verify /dev/disk/by-id devices", action="store_true"
    )

    argument_parser.add_argument(
        "--highest-revision-number",
        dest="highest_revision_number",
        type=revision_number_type,
        default=StratisDbus.REVISION_NUMBER,
        help=(
            "The highest revision number of Manager interface to be "
            "used when constructing Manager interface names to pass as an "
            "argument to the optionally executed dbus monitor script."
        ),
    )

    parsed_args, unittest_args = argument_parser.parse_known_args()
    StratisCliCertify.DISKS = parsed_args.DISKS
    RunPostTestChecks.set_from_post_test_check_option(parsed_args.post_test_check)
    SysfsMonitor.verify_sysfs = SysfsMonitor.verify_sysfs or parsed_args.verify_sysfs
    DbusMonitor.monitor_dbus = DbusMonitor.monitor_dbus or parsed_args.monitor_dbus
    SymlinkMonitor.verify_devices = (
        SymlinkMonitor.verify_devices or parsed_args.verify_devices
    )

    StratisCertify.maxDiff = None
    DbusMonitor.highest_revision_number = parsed_args.highest_revision_number

    print(f"Using block device(s) for tests: {StratisCliCertify.DISKS}")
    unittest.main(argv=sys.argv[:1] + unittest_args)


if __name__ == "__main__":
    main()

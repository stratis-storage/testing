# Copyright 2020 Red Hat, Inc.
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
Methods and classes that do infrastructure tasks.
"""
# isort: STDLIB
import base64
import fnmatch
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from enum import Enum
from tempfile import NamedTemporaryFile

# isort: THIRDPARTY
import dbus
from justbytes import Range

from .dbus import StratisDbus, manager_interfaces
from .utils import exec_command, process_exists, terminate_traces

_OK = 0

MONITOR_DBUS_SIGNALS = "./scripts/monitor_dbus_signals.py"
DBUS_NAME_HAS_NO_OWNER_ERROR = "org.freedesktop.DBus.Error.NameHasNoOwner"
SYS_CLASS_BLOCK = "/sys/class/block"
DEV_MAPPER = "/dev/mapper"
VAR_TMP = "/var/tmp"
MOUNT_POINT_SUFFIX = "_stratisd_mounts"
UMOUNT = "umount"
MOUNT = "mount"
STRATIS_METADATA_LEN = Range(8192, 512)


def clean_up():  # pylint: disable=too-many-branches,too-many-locals
    """
    Try to clean up after a test failure.

    :return: None
    """

    exec_command(["udevadm", "settle"])

    terminate_traces(MONITOR_DBUS_SIGNALS)

    if process_exists("stratisd") is None:
        raise RuntimeError("stratisd process is not running")

    error_strings = []

    def check_result(result, format_str, format_str_args):
        if result is None:
            return
        (_, code, msg) = result
        if code == 0:
            return
        error_strings.append(f"{format_str % format_str_args}: {msg}")

    # Start any stopped pools
    for uuid in StratisDbus.stopped_pools():
        StratisDbus.pool_start(uuid, "uuid")

    # Unmount FS
    for mountpoint_dir in fnmatch.filter(os.listdir(VAR_TMP), f"*{MOUNT_POINT_SUFFIX}"):
        for (_, name, _), _ in StratisDbus.fs_list().items():
            try:
                subprocess.check_call(
                    [UMOUNT, os.path.join(VAR_TMP, mountpoint_dir, name)]
                )
            except subprocess.CalledProcessError as err:
                error_strings.append(
                    "Failed to umount filesystem at "
                    f"{os.path.join(VAR_TMP, mountpoint_dir, name)}: {err}"
                )

    # Unset MergeScheduled
    for (fs_path, name, (origin_set, _)), pool_name in StratisDbus.fs_list().items():
        if origin_set:
            check_result(
                StratisDbus.set_property(
                    fs_path,
                    StratisDbus.FS_IFACE,
                    "MergeScheduled",
                    dbus.Boolean(False),
                ),
                "failed to set MergeScheduled to False",
                (name, pool_name),
            )

    # Remove FS
    for (_, name, _), pool_name in StratisDbus.fs_list().items():
        check_result(
            StratisDbus.fs_destroy(pool_name, name),
            "failed to destroy filesystem %s in pool %s",
            (name, pool_name),
        )

    # Remove Pools
    for _, name, _ in StratisDbus.pool_list():
        check_result(StratisDbus.pool_destroy(name), "failed to destroy pool %s", name)

    # Unset all Stratis keys
    (keys, return_code, message) = StratisDbus.get_keys()
    if return_code != _OK:
        raise RuntimeError(
            "Obtaining the list of keys using stratisd failed with an error: {message}"
        )

    for key in keys:
        check_result(StratisDbus.unset_key(key), "failed to unset key %s", key)

    # Report an error if any filesystems, pools or keys are found to be
    # still in residence
    remnant_filesystems = StratisDbus.fs_list()
    if remnant_filesystems != {}:
        error_strings.append(
            f"remnant filesystems: "
            f'{", ".join(map(lambda x: f"{x[0]} in pool {x[1]}", remnant_filesystems.items(),))}'
        )

    remnant_pools = StratisDbus.pool_list()
    if remnant_pools != []:
        error_strings.append(
            f'remnant pools: {", ".join(name for _, name, _ in remnant_pools)}'
        )

    (remnant_keys, return_code, message) = StratisDbus.get_keys()
    if return_code != _OK:
        error_strings.append(
            f"failed to obtain information about Stratis keys: {message}"
        )
    else:
        if remnant_keys != []:
            error_strings.append(f'remnant keys: {", ".join(remnant_keys)}')

    for mountpoint_dir in fnmatch.filter(os.listdir(VAR_TMP), f"*{MOUNT_POINT_SUFFIX}"):
        try:
            shutil.rmtree(os.path.join(VAR_TMP, mountpoint_dir))
        except Exception as err:  # pylint: disable=broad-exception-caught
            error_strings.append(
                f"failed to clean up temporary mountpoint dir {mountpoint_dir}: {err}"
            )

    assert isinstance(error_strings, list)
    if error_strings:
        raise RuntimeError(
            f'clean_up may not have succeeded: {"; ".join(error_strings)}'
        )


class StratisdSystemdStart(unittest.TestCase):
    """
    Handles starting and stopping stratisd via systemd.
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

        if process_exists("stratisd") is None:
            raise RuntimeError(
                "stratisd was started by systemd but has since been terminated"
            )

        try:
            StratisDbus.stratisd_version()
        except dbus.exceptions.DBusException as err:
            if process_exists("stratisd") is None:
                raise RuntimeError(
                    "stratisd appears to have terminated while processing a "
                    "D-Bus request"
                ) from err

            if err.get_dbus_name() == DBUS_NAME_HAS_NO_OWNER_ERROR:
                raise RuntimeError(
                    "stratisd is running but D-Bus method call returns "
                    f"{DBUS_NAME_HAS_NO_OWNER_ERROR} indicating that "
                    "stratisd could not connect to the D-Bus"
                ) from err

            raise RuntimeError(
                "stratisd is running but something prevented the test D-Bus "
                "method call from succeeding"
            ) from err

        clean_up()

        time.sleep(1)
        exec_command(["udevadm", "settle"])


def sleep_time(stop_time, wait_time):
    """
    Calculate the time to sleep required so that the check commences
    only after wait_time seconds have passed since the test ended.

    :param int stop_time: time test was completed in nanoseconds
    :param int wait_time: time to wait after test ends in seconds
    :returns: time to sleep so that check does not commence early, seconds
    """
    time_since_test_sec = (time.monotonic_ns() - stop_time) // 10**9

    return (wait_time - time_since_test_sec) if (wait_time > time_since_test_sec) else 0


class PoolMetadataMonitor(unittest.TestCase):
    """
    Manage verification of consistency of pool-level metadata.
    """

    maxDiff = None

    def _check_encryption_information_consistency(self, pool_object_path, metadata):
        """
        Check whether D-Bus and metadata agree about encryption state of pool.
        """
        encrypted = bool(StratisDbus.pool_encrypted(pool_object_path))
        features = metadata.get("features")

        if encrypted:
            self.assertIsNotNone(features)
            self.assertIn("Encryption", metadata["features"])
        elif features is not None:
            self.assertNotIn("Encryption", metadata["features"])

    def run_check(self, stop_time):  # pylint: disable=too-many-locals
        """
        Run the check.

        :param int stop_time: the time the test completed
        """
        stratisd_tools = "stratisd-tools"

        if PoolMetadataMonitor.verify:  # pylint: disable=no-member

            # Wait for D-Bus to settle, so D-Bus and metadata can be compared
            time.sleep(sleep_time(stop_time, 16))

            for object_path, _, _ in StratisDbus.pool_list():
                for _ in range(5):
                    (written_0, written_0_return_code, _) = (
                        StratisDbus.pool_get_metadata(object_path, current=False)
                    )
                    (current, current_return_code, current_message) = (
                        StratisDbus.pool_get_metadata(object_path)
                    )
                    (written_1, written_1_return_code, written_1_message) = (
                        StratisDbus.pool_get_metadata(object_path, current=False)
                    )
                    if (
                        written_0_return_code == _OK
                        and written_1_return_code == _OK
                        and written_0 == written_1
                    ):
                        break

                    time.sleep(1)

                else:
                    if written_0_return_code != _OK or written_1_return_code != _OK:
                        raise RuntimeError("Can not obtain written metadata reliably.")

                    written_0 = json.loads(written_0)
                    written_1 = json.loads(written_1)
                    raise RuntimeError("Metadata written out to disk is not stable")

                (written, written_return_code, written_message) = (
                    written_1,
                    written_1_return_code,
                    written_1_message,
                )

                if current_return_code == _OK and written_return_code == _OK:
                    current = json.loads(current)
                    written = json.loads(written)
                    self.assertEqual(
                        written,
                        current,
                        msg=(
                            "previously written metadata and current metadata "
                            f"are not the same.{os.linesep}Previous:"
                            f"{os.linesep}"
                            f"{json.dumps(written, sort_keys=True, indent=4)}"
                            f"{os.linesep}Current:{os.linesep}"
                            f"{json.dumps(current, sort_keys=True, indent=4)}"
                        ),
                    )

                    self._check_encryption_information_consistency(object_path, written)

                    with NamedTemporaryFile(mode="w") as temp_file:
                        temp_file.write(json.dumps(written))
                        temp_file.flush()

                        try:
                            with subprocess.Popen(
                                [
                                    stratisd_tools,
                                    "stratis-checkmetadata",
                                    temp_file.name,
                                ],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                            ) as proc:
                                (stdoutdata, stderrdata) = proc.communicate()
                                self.assertEqual(
                                    proc.returncode,
                                    0,
                                    (
                                        f'stdout: {stdoutdata.decode("utf-8")}'
                                        "; "
                                        f'stderr: {stderrdata.decode("utf-8")}'
                                    ),
                                )
                        except FileNotFoundError as err:
                            raise RuntimeError(f"{stratisd_tools} not found") from err

                else:
                    current_message = (
                        "" if current_return_code == _OK else current_message
                    )
                    written_message = (
                        "" if written_return_code == _OK else written_message
                    )
                    message = ", ".join(
                        x for x in [current_message, written_message] if x != ""
                    )
                    raise RuntimeError(
                        "One or both versions of metadata could not be "
                        f"obtained for comparison: {message}"
                    )


class SysfsMonitor(unittest.TestCase):
    """
    Manage verification of sysfs files for devices.
    """

    maxDiff = None

    def run_check(self):
        """
        Run the check.
        """
        if SysfsMonitor.verify_sysfs:  # pylint: disable=no-member
            dm_devices = {
                os.path.basename(
                    os.path.realpath(os.path.join(DEV_MAPPER, dmdev))
                ): dmdev
                for dmdev in os.listdir(DEV_MAPPER)
            }

            try:
                misaligned_devices = []
                for dev in fnmatch.filter(os.listdir(SYS_CLASS_BLOCK), "dm-*"):
                    dev_sysfspath = os.path.join(
                        SYS_CLASS_BLOCK, dev, "alignment_offset"
                    )
                    with open(dev_sysfspath, "r", encoding="utf-8") as dev_sysfs:
                        dev_align = dev_sysfs.read().rstrip()
                        if int(dev_align) != 0:
                            misaligned_devices.append(
                                f"Stratis Name: {dm_devices[dev]}, "
                                f" DM name: {dev}, "
                                f" Alignment offset: {dev_align}"
                            )

                self.assertEqual(misaligned_devices, [])
            except FileNotFoundError:
                pass


class SymlinkMonitor(unittest.TestCase):
    """
    Manage verification of device symlinks.
    """

    maxDiff = None

    def run_check(self):
        """
        Run the check.
        """
        if SymlinkMonitor.verify_devices:  # pylint: disable=no-member
            try:
                disallowed_symlinks = []
                for dev in os.listdir("/dev/disk/by-id"):
                    if fnmatch.fnmatch(
                        dev, "*stratis-1-private-*"
                    ) and not fnmatch.fnmatch(dev, "*stratis-1-private-*-crypt"):
                        disallowed_symlinks.append(dev)
                self.assertEqual(disallowed_symlinks, [])
            except FileNotFoundError:
                pass


class FilesystemSymlinkMonitor(unittest.TestCase):
    """
    Verify that devicmapper devices for filesystems have corresponding symlinks.
    """

    maxDiff = None

    def run_check(self, stop_time):
        """
        Check that the filesystem links on the D-Bus and the filesystem links
        expected from looking at filesystem devicemapper paths match exactly.

        :param int stop_time: the time the test completed
        """

        if not FilesystemSymlinkMonitor.verify_devices:  # pylint: disable=no-member
            return

        decode_dm = "stratis-decode-dm"

        time.sleep(sleep_time(stop_time, 16))

        managed_objects = StratisDbus.get_managed_objects()

        filesystems = frozenset(
            [
                obj_data[StratisDbus.FS_IFACE]["Devnode"]
                for obj_data in managed_objects.values()
                if StratisDbus.FS_IFACE in obj_data
            ]
        )

        try:
            found = 0
            for dev in fnmatch.filter(os.listdir(DEV_MAPPER), "stratis-1-*-thin-fs-*"):
                found += 1
                command = [
                    decode_dm,
                    os.path.join(DEV_MAPPER, dev),
                    "--output=symlink",
                ]
                try:
                    with subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    ) as proc:
                        (stdoutdata, stderrdata) = proc.communicate()
                        if proc.returncode == 0:
                            symlink = stdoutdata.decode("utf-8").strip()
                            self.assertTrue(os.path.exists(symlink))
                            self.assertIn(symlink, filesystems)
                        else:
                            raise RuntimeError(
                                f"{decode_dm} invocation failed: "
                                f"{stderrdata.decode('utf-8')}"
                            )
                except FileNotFoundError as err:
                    raise RuntimeError(
                        f"Script '{decode_dm}' missing, test could not be run"
                    ) from err

            if found != len(filesystems):
                raise RuntimeError(
                    f"{len(filesystems)} Stratis filesystems were created by "
                    f'this test but {found} "{DEV_MAPPER}" links were found.'
                )

        except FileNotFoundError as err:
            raise RuntimeError(
                f'Missing directory "{DEV_MAPPER}", test could not be run'
            ) from err


class DbusMonitor(unittest.TestCase):
    """
    Manage starting and stopping the D-Bus monitor script.
    """

    maxDiff = None

    def setUp(self):
        """
        Set up the D-Bus monitor for a test run.
        """
        if DbusMonitor.monitor_dbus:  # pylint: disable=no-member
            command = [
                MONITOR_DBUS_SIGNALS,
                StratisDbus.BUS_NAME,
                StratisDbus.TOP_OBJECT,
            ]
            command.extend(
                f"--top-interface={intf}"
                for intf in manager_interfaces(
                    # pylint: disable=no-member
                    DbusMonitor.highest_revision_number
                    + 1
                )
            )

            only_check = (
                StratisDbus.BUS_NAME.replace(".", r"\.")
                + r"\."
                + ".*"
                + r"\."
                + f"r[0-{StratisDbus.REVISION_NUMBER}]"
            )
            command.append(f"--only-check={only_check}")

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

    def run_check(self, stop_time):
        """
        Stop the D-Bus monitor script and check the results.

        :param int stop_time: the time the test completed
        """
        trace = getattr(self, "trace", None)
        if trace is not None:
            # A sixteen second wait will make it virtually certain that
            # stratisd has a chance to do one of its 10 second timer passes on
            # pools and filesystems _and_ that the D-Bus task has at least one
            # second to send out any resulting signals.
            time.sleep(sleep_time(stop_time, 16))
            self.trace.send_signal(signal.SIGINT)
            (stdoutdata, stderrdata) = self.trace.communicate()

            if self.trace.returncode == 3:
                raise RuntimeError(
                    "Failure while processing D-Bus signals: "
                    f'stderr: {stderrdata.decode("utf-8")}, '
                    f'stdout: {stdoutdata.decode("utf-8")}'
                )

            if self.trace.returncode == 4:
                raise RuntimeError(
                    "Failure while comparing D-Bus states: "
                    f'stderr: {stderrdata.decode("utf-8")}, '
                    f'stdout: {stdoutdata.decode("utf-8")}'
                )

            msg = stdoutdata.decode("utf-8")
            self.assertEqual(
                self.trace.returncode,
                0,
                (
                    stderrdata.decode("utf-8")
                    if len(msg) == 0
                    else (
                        "Error from monitor_dbus_signals: "
                        + os.linesep
                        + os.linesep
                        + msg
                    )
                ),
            )


class KernelKey:  # pylint: disable=attribute-defined-outside-init
    """
    A handle for operating on keys in the kernel keyring. The specified key will
    be available for the lifetime of the test when used with the Python with
    keyword and will be cleaned up at the end of the scope of the with block.
    """

    def __init__(self, key_data):
        """
        Initialize a key with the provided key data (passphrase).
        :param bytes key_data: The desired key contents
        """
        self._key_data = key_data

    def __enter__(self):
        """
        This method allows KernelKey to be used with the "with" keyword.
        :return: The key description that can be used to access the
                 provided key data in __init__.
        :raises RuntimeError: if setting the key using the stratisd D-Bus API
                              returns a non-zero return code
        """
        with open("/dev/urandom", "rb") as urandom_f:
            self._key_desc = base64.b64encode(urandom_f.read(16)).decode("utf-8")

        with NamedTemporaryFile(mode="w") as temp_file:
            temp_file.write(self._key_data)
            temp_file.flush()

            (_, return_code, message) = StratisDbus.set_key(self._key_desc, temp_file)

        if return_code != _OK:
            raise RuntimeError(
                f"Setting the key using stratisd failed with an error: {message}"
            )

        return self._key_desc

    def __exit__(self, exception_type, exception_value, traceback):
        message = None
        try:
            (_, return_code, message) = StratisDbus.unset_key(self._key_desc)

            if return_code != _OK:
                raise RuntimeError(
                    f"Unsetting the key using stratisd failed with an error: {message}"
                )
        except Exception as rexc:
            if exception_value is None:
                raise rexc
            raise rexc from exception_value


class PostTestCheck(Enum):
    """
    What PostTestChecks to run.
    """

    DBUS_MONITOR = "monitor-dbus"
    SYSFS = "verify-sysfs"
    PRIVATE_SYMLINKS = "verify-private-symlinks"
    FILESYSTEM_SYMLINKS = "verify-filesystem-symlinks"
    POOL_METADATA = "verify-pool-metadata"

    def __str__(self):
        return self.value


class RunPostTestChecks:
    """
    Manage running post test checks
    """

    def __init__(self, *, test_id=None):
        """
        Set up checks that need to be started before test is run.
        """
        self.dbus_monitor = DbusMonitor()
        # See: https://github.com/stratis-storage/project/issues/741
        if test_id is None or not test_id.endswith("test_pool_add_data_init_cache"):
            self.dbus_monitor.setUp()

    def teardown(self):
        """
        Run post-test checks after test is completed.
        """
        stop_time = time.monotonic_ns()
        self.dbus_monitor.run_check(stop_time)

        SysfsMonitor().run_check()
        SymlinkMonitor().run_check()
        FilesystemSymlinkMonitor().run_check(stop_time)
        PoolMetadataMonitor().run_check(stop_time)

    @staticmethod
    def set_from_post_test_check_option(post_test_check):
        """
        Set run flags from post_test_check option in parser args.
        """
        SysfsMonitor.verify_sysfs = PostTestCheck.SYSFS in post_test_check
        DbusMonitor.monitor_dbus = PostTestCheck.DBUS_MONITOR in post_test_check
        SymlinkMonitor.verify_devices = (
            PostTestCheck.PRIVATE_SYMLINKS in post_test_check
        )
        FilesystemSymlinkMonitor.verify_devices = (
            PostTestCheck.FILESYSTEM_SYMLINKS in post_test_check
        )
        PoolMetadataMonitor.verify = PostTestCheck.POOL_METADATA in post_test_check


class MountPointManager:  # pylint: disable=too-few-public-methods
    """
    Handle mounting Stratis filesystems in a temp directory.
    """

    def __init__(self):
        """
        Initalizer.

        :rtype: None
        """
        self.mount_root = tempfile.mkdtemp(suffix=MOUNT_POINT_SUFFIX, dir=VAR_TMP)

    def mount(self, fs_paths):
        """
        Generate canonical mountpoints from filesystem paths and mount each
        filesystem.
        :param str fs_paths: the absolute paths to mount
        :rtype: list of str
        """
        mountpoints = []
        for fs_path in fs_paths:
            mountpoint = os.path.join(self.mount_root, os.path.basename(fs_path))
            try:
                os.mkdir(mountpoint)
            except FileExistsError:
                pass
            subprocess.check_call([MOUNT, fs_path, mountpoint])
            mountpoints.append(mountpoint)

        return mountpoints

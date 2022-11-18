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
import time
import unittest
from tempfile import NamedTemporaryFile

# isort: THIRDPARTY
import dbus

from .dbus import StratisDbus
from .utils import exec_command, process_exists, terminate_traces

_OK = 0

MONITOR_DBUS_SIGNALS = "./scripts/monitor_dbus_signals.py"
DBUS_NAME_HAS_NO_OWNER_ERROR = "org.freedesktop.DBus.Error.NameHasNoOwner"


def clean_up():
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

    # Remove FS
    for name, pool_name in StratisDbus.fs_list().items():
        check_result(
            StratisDbus.fs_destroy(pool_name, name),
            "failed to destroy filesystem %s in pool %s",
            (name, pool_name),
        )

    # Remove Pools
    for name in StratisDbus.pool_list():
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
        error_strings.append(f'remnant pools: {", ".join(remnant_pools)}')

    (remnant_keys, return_code, message) = StratisDbus.get_keys()
    if return_code != _OK:
        error_strings.append(
            f"failed to obtain information about Stratis keys: {message}"
        )
    else:
        if remnant_keys != []:
            error_strings.append(f'remnant keys: {", ".join(remnant_keys)}')

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

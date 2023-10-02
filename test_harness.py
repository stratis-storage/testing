# Copyright 2023 Red Hat, Inc.
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
Simple test harness for running test scripts on loopbacked devices.
"""

# isort: STDLIB
import argparse
import os
import subprocess
import tempfile

# isort: LOCAL
from testlib.dbus import StratisDbus

_LOSETUP_BIN = "/usr/sbin/losetup"
_SIZE_OF_DEVICE = 1024**4  # 1 TiB


def _make_loopbacked_devices(num):
    """
    Make the requisite number of loopbacked devices.

    :param int num: number of devices
    """

    tdir = tempfile.mkdtemp("_stratis_test_loopback")

    devices = []
    for index in range(num):
        backing_file = os.path.join(tdir, f"block_device_{index}")

        with open(backing_file, "ab") as dev:
            dev.truncate(_SIZE_OF_DEVICE)

        device = str.strip(
            subprocess.check_output(
                [_LOSETUP_BIN, "-f", "--show", backing_file]
            ).decode("utf-8")
        )

        devices.append(device)

    return devices


def _run_stratisd_cert(namespace, unittest_args):
    # isort: FIRSTPARTY
    # pylint: disable=import-outside-toplevel
    from stratisd_cert import Config, run_tests

    devices = _make_loopbacked_devices(3)

    config = Config(
        devices,
        monitor_dbus=namespace.monitor_dbus,
        verify_filesystem_symlinks=namespace.verify_devices,
    )

    if namespace.highest_revision_number is not None:
        config.highest_revision_number = namespace.highest_revision_number

    run_tests(config, unittest_args + ["-v"])


def _run_stratis_cli_cert(namespace, unittest_args):
    # isort: FIRSTPARTY
    # pylint: disable=import-outside-toplevel
    from stratis_cli_cert import Config, run_tests

    devices = _make_loopbacked_devices(3)

    config = Config(
        devices,
        monitor_dbus=namespace.monitor_dbus,
        verify_filesystem_symlinks=namespace.verify_devices,
    )

    if namespace.highest_revision_number is not None:
        config.highest_revision_number = namespace.highest_revision_number

    run_tests(config, unittest_args + ["-v"])


def _gen_parser():
    """
    Generate the parser.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Run specified test script on loopbacked devices. This script is "
            "intended to be run on a disposable testing machine, it does not "
            "clean up resources, such as temporary directories."
        )
    )

    subparsers = parser.add_subparsers(title="subcommand")

    stratisd_cert_parser = subparsers.add_parser(
        "stratisd_cert", help="Run stratisd_cert.py"
    )
    stratisd_cert_parser.set_defaults(func=_run_stratisd_cert)
    stratisd_cert_parser.add_argument(
        "--monitor-dbus", help="Monitor D-Bus", action="store_true"
    )
    stratisd_cert_parser.add_argument(
        "--verify-devices", help="Verify /dev/disk/by-id devices", action="store_true"
    )

    stratisd_cert_parser.add_argument(
        "--highest-revision-number",
        dest="highest_revision_number",
        default=StratisDbus.REVISION_NUMBER,
        help=("Highest D-Bus revision number to be used by D-Bus monitor tests."),
    )

    stratis_cli_cert_parser = subparsers.add_parser(
        "stratis_cli_cert", help="Run stratis_cli_cert.py"
    )
    stratis_cli_cert_parser.set_defaults(func=_run_stratis_cli_cert)

    return parser


def main():
    """
    The main method.
    """
    parser = _gen_parser()

    namespace, unittest_args = parser.parse_known_args()

    namespace.func(namespace, unittest_args)


if __name__ == "__main__":
    main()

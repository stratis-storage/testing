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
Utility functions for blackbox testing.
"""
# isort: STDLIB
import os
import random
import string
from subprocess import PIPE, Popen, run
from tempfile import NamedTemporaryFile

# isort: THIRDPARTY
import psutil


def random_string(length=4):
    """
    Generates a random string
    :param length: Length of random string
    :return: String
    """
    return "{0}".format(
        "".join(random.choice(string.ascii_uppercase) for _ in range(length))
    )


def resolve_symlink(link):
    """
    Resolves the destination of a symlink
    :param link: filename of the link
    :return: String
    """
    return os.path.abspath(os.path.join(os.path.dirname(link), os.readlink(link)))


def create_relative_device_path(devpath):
    """
    Create a relative device path from an absolute device path
    :param devpath: Device path
    :return: String
    """
    dirname = os.path.dirname(devpath)
    return os.path.join(
        dirname, "..", os.path.basename(dirname), os.path.basename(devpath)
    )


def process_exists(name):
    """
    Look through processes, using their pids, to find one matching 'name'.
    Return None if no such process found, else return the pid.
    :param name: name of process to check
    :type name: str
    :return: pid or None
    :rtype: int or NoneType
    """
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.name() == name:
                return proc.pid
        except psutil.NoSuchProcess:
            pass

    return None


def terminate_traces(name):
    """
    Terminate trace processes with the given filename.  This is
    intended for Python scripts whose name will be in the cmdline, but
    not in the process name.
    :param name: name of script to clean up
    :type name: str
    return: None
    """
    procs = []
    for proc in psutil.process_iter(["cmdline"]):
        try:
            if any(name == param for param in proc.info["cmdline"]):
                procs.append(proc)
        except psutil.NoSuchProcess:
            pass

    if len(procs) > 0:
        for termproc in procs:
            termproc.terminate()


def exec_command(cmd, *, settle=False):
    """
    Executes the specified infrastructure command.

    :param cmd: command to execute
    :type cmd: list of str
    :param settle: whether to settle before running the command, default False
    :type settle: bool
    :returns: standard output
    :rtype: str
    :raises RuntimeError: if exit code is non-zero
    """
    exit_code, stdout_text, stderr_text = exec_test_command(cmd, settle=settle)

    if exit_code != 0:
        raise RuntimeError(
            "exec_command: non-zero exit code: %d\nSTDOUT=%s\nSTDERR=%s"
            % (exit_code, stdout_text, stderr_text)
        )
    return stdout_text


def exec_test_command(cmd, *, settle=False):
    """
    Executes the specified test command
    :param cmd: Command and arguments as list
    :type cmd: list of str
    :param settle: whether to settle before running the command, default False
    :type settle: bool
    :returns: (exit code, std out text, std err text)
    :rtype: triple of int * str * str
    """
    if settle:
        run(["udevadm", "settle"], check=True)

    with Popen(
        cmd, stdout=PIPE, stderr=PIPE, close_fds=True, env=os.environ
    ) as process:
        result = process.communicate()
        return (
            process.returncode,
            bytes(result[0]).decode("utf-8"),
            bytes(result[1]).decode("utf-8"),
        )


class RandomKeyTmpFile:
    """
    Generate a random passphrase and put it in a temporary file.
    """

    # pylint: disable=consider-using-with
    def __init__(self, key_bytes=32):
        """
        Initializer

        :param int key_bytes: the desired length of the key in bytes
        """
        self._tmpfile = NamedTemporaryFile("wb")
        with open("/dev/urandom", "rb") as urandom_f:
            random_bytes = urandom_f.read(key_bytes)
            self._tmpfile.write(random_bytes)
            self._tmpfile.flush()

    def tmpfile_name(self):
        """
        Get the name of the temporary file.
        """
        return self._tmpfile.name

    def close(self):
        """
        Close and delete the temporary file.
        """
        self._tmpfile.close()

    def __enter__(self):
        """
        For use with the "with" keyword.

        :return str: the path of the file with the random key
        """
        return self._tmpfile.name

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self._tmpfile.close()
        except Exception as error:
            if exc_value is None:
                raise error

            raise error from exc_value

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
from functools import wraps
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
    return "".join(random.choice(string.ascii_uppercase) for _ in range(length))


def resolve_symlink(link):
    """
    Resolves the destination of a symlink
    :param link: filename of the link
    :return: String
    """
    return os.path.abspath(os.path.join(os.path.dirname(link), os.readlink(link)))


def revision_number_type(revision_number):
    """
    Raise value error if revision number is not valid.
    :param revision_number: stratisd D-Bus interface revision number
    :rtype: int
    :return: proper revision number
    """
    revision_number = int(revision_number)
    if revision_number < 0:
        raise ValueError(revision_number)
    return revision_number


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
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = proc.info["cmdline"]
            if cmdline is not None and any(name == param for param in cmdline):
                proc.terminate()
        except psutil.NoSuchProcess:
            pass


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
            f"exec_command: non-zero exit code: "
            f"{exit_code}\nSTDOUT={stdout_text}\nSTDERR={stderr_text}"
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


def skip(condition):
    """
    Custom method to allow skipping a test. condition is a method that will
    raise a unittest.SkipTest exception if the condition is false. The
    unittest.skip* decorators are insufficient, since their conditions are
    evaluated at class loading time.
    """

    def func_generator(func):
        """
        A function to be used as a decorator to generate a modified function
        for tests that require devices.
        """

        @wraps(func)
        def modified_func(self):
            """
            The modified function, which checks a condition before the test is
            run.
            """
            condition()
            return func(self)

        return modified_func

    return func_generator

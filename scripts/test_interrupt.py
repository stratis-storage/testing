#!/usr/bin/python

import signal
import subprocess
import time

trace = subprocess.Popen(
    [
        "./monitor_dbus_signals.py",
        "org.storage.stratis3",
        "/org/storage/stratis3",
    ],
    shell=False,
)

print("starting wait...")
time.sleep(30)

# Make changes to pools and filesystems before sending SIGINT

print("finishing wait.")
trace.send_signal(signal.SIGINT)
trace.wait(timeout=3)
print("Return code: %s" % trace.returncode)

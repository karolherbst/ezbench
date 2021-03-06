#!/usr/bin/env python3

"""
Copyright (c) 2015, Intel Corporation

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

    * Redistributions of source code must retain the above copyright notice,
      this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of Intel Corporation nor the names of its contributors
      may be used to endorse or promote products derived from this software
      without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

from ezbench import *
import argparse
import signal
import time
import os

ezbench_dir = os.path.abspath(sys.path[0] + "/../")
stop_requested = False

def stop_handler(signum, frame):
    global stop_requested
    stop_requested = True
    print("-- The user requested to abort! --")
    # TODO: Abort faster than after every run
    return

def reload_conf_handler(signum, frame):
    return

# parse the options
parser = argparse.ArgumentParser()
args = parser.parse_args()

# handle the signals systemd asks us to
signal.signal(signal.SIGTERM, stop_handler)
signal.signal(signal.SIGHUP, reload_conf_handler)

reportStateModDate = dict()

lastPoll = 0
while not stop_requested:
    futureLastPoll = time.time()
    reports = list_smart_ezbench_report_names(ezbench_dir, lastPoll)
    lastPoll = futureLastPoll
    for report_name in reports:
        sbench = SmartEzbench(ezbench_dir, report_name)
        sbench.run()

    # TODO: Replace this by inotify
    time.sleep(1)
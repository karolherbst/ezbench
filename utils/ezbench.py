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

from array import array
from enum import Enum
from numpy import *
import subprocess
import datetime
import atexit
import pprint
import fcntl
import time
import json
import glob
import copy
import csv
import sys
import os
import re

# Ezbench runs
class EzbenchRun:
    def __init__(self, commits, benchmarks, predicted_execution_time, deployed_commit):
        self.commits = commits
        self.benchmarks = benchmarks
        self.predicted_execution_time = predicted_execution_time
        self.deployed_commit = deployed_commit

class Ezbench:
    def __init__(self, ezbench_path, profile = None, repo_path = None,
                 make_command = None, report_name = None, tests_folder = None):
        self.ezbench_path = ezbench_path
        self.profile = profile
        self.repo_path = repo_path
        self.make_command = make_command
        self.report_name = report_name
        self.tests_folder = tests_folder

    def __ezbench_cmd_base(self, benchmarks, benchmark_excludes = [], rounds = None, dry_run = False):
        ezbench_cmd = []
        ezbench_cmd.append(self.ezbench_path)

        if self.profile is not None:
            ezbench_cmd.append("-P"); ezbench_cmd.append(self.profile)

        if self.repo_path is not None:
            ezbench_cmd.append("-p"); ezbench_cmd.append(self.repo_path)

        for benchmark in benchmarks:
            ezbench_cmd.append("-b"); ezbench_cmd.append(benchmark)

        for benchmark_excl in benchmark_excludes:
            ezbench_cmd.append("-B"); ezbench_cmd.append(benchmark_excl)

        if rounds is not None:
            ezbench_cmd.append("-r"); ezbench_cmd.append(str(rounds))

        if self.make_command is not None:
            ezbench_cmd.append("-m"); ezbench_cmd.append(self.make_command)
        if self.report_name is not None:
            ezbench_cmd.append("-N"); ezbench_cmd.append(self.report_name)
        if self.tests_folder is not None:
            ezbench_cmd.append("-T"); ezbench_cmd.append(self.tests_folder)

        if dry_run:
            ezbench_cmd.append("-k")

        return ezbench_cmd

    def __run_ezbench(self, cmd, dry_run = False, verbose = False):
        error = None

        if verbose:
            print(cmd)

        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()
        except subprocess.CalledProcessError as e:
            error = e
            output = e.output.decode()
            pass

        if not dry_run and error is None:
            return True

        if error is not None:
            # Invalid profile
            if error.returncode == 12:
                return False

        # we need to parse the output
        commits= []
        benchmarks = []
        pred_exec_time = 0
        deployed_commit = ""
        re_commit_list = re.compile('^Testing \d+ commits: ')
        re_deployed_version = re.compile('.*, deployed version = ')
        for line in output.split("\n"):
            m_commit_list = re_commit_list.match(line)
            m_deployed_version = re_deployed_version.match(line)
            if line.startswith("Tests that will be run:"):
                benchmarks = line[24:].split(" ")
                if benchmarks[-1] == '':
                    benchmarks.pop(-1)
            elif line.find("estimated finish date:") >= 0:
                pred_exec_time = ""
            elif m_deployed_version is not None:
                deployed_commit = line[m_deployed_version.end():]
            elif m_commit_list is not None:
                commits = line[m_commit_list.end():].split(" ")
                while '' in commits:
                    commits.remove('')
            elif error is not None:
                if error.returncode == 101 and line.endswith("do not exist"):
                    print(line)
                    return False

        if error is not None:
            print("\n\nERROR: The following command '{}' failed with the error code {}. Here is its output:\n\n'{}'".format(" ".join(cmd), error.returncode, output))
            return False

        return EzbenchRun(commits, benchmarks, pred_exec_time, deployed_commit)

    def run_commits(self, commits, benchmarks, benchmark_excludes = [],
                    rounds = None, dry_run = False, verbose = False):
        ezbench_cmd = self.__ezbench_cmd_base(benchmarks, benchmark_excludes, rounds, dry_run)

        for commit in commits:
            ezbench_cmd.append(commit)

        return self.__run_ezbench(ezbench_cmd, dry_run, verbose)

    def run_commit_range(self, head, commit_count, benchmarks, benchmark_excludes = [],
                         rounds = None, dry_run = False, verbose = False):
        ezbench_cmd = self.__ezbench_cmd_base(benchmarks, benchmark_excludes, rounds, dry_run)

        ezbench_cmd.append("-H"); ezbench_cmd.append(head)
        ezbench_cmd.append("-n"); ezbench_cmd.append(commit_count)

        return self.__run_ezbench(ezbench_cmd, dry_run, verbose)

# Smart-ezbench-related classes
class Criticality(Enum):
    II = 0
    WW = 1
    EE = 2
    DD = 3

def list_smart_ezbench_report_names(ezbench_dir, updatedSince = 0):
    log_dir = ezbench_dir + '/logs'
    state_files = glob.glob("{log_dir}/*/smartezbench.state".format(log_dir=log_dir));

    reports = []
    for state_file in state_files:
        if os.path.getmtime(state_file) < updatedSince:
            continue

        start = len(log_dir) + 1
        stop = len(state_file) - 19
        reports.append(state_file[start:stop])

    return reports

class TaskEntry:
    def __init__(self, commit, benchmark, rounds):
        self.commit = commit
        self.benchmark = benchmark
        self.rounds = rounds

class SmartEzbench:
    def __init__(self, ezbench_dir, report_name):
        self.state = dict()
        self.state['ezbench_dir'] = ezbench_dir
        self.state['report_name'] = report_name
        self.state['log_folder'] = ezbench_dir + '/logs/' + report_name
        self.state['smart_ezbench_state'] = self.state['log_folder'] + "/smartezbench.state"
        self.state['smart_ezbench_lock'] = self.state['log_folder'] + "/smartezbench.lock"
        self.state['smart_ezbench_log'] = self.state['log_folder'] + "/smartezbench.log"
        self.state['commits'] = dict()
        self.state['beenRunBefore'] = False

        # Create the log directory
        if not os.path.exists(self.state['log_folder']):
            os.makedirs(self.state['log_folder'])

        # Open the log file as append
        self.log_file = open(self.state['smart_ezbench_log'], "a")

        # Load the state but use the newly-created state if we cannot load it
        if not self.__reload_state():
            self.__save_state()
            self.__log(Criticality.II,
                       "Created report '{report_name}' in {log_folder}".format(report_name=report_name,
                                                                               log_folder=self.state['log_folder']))

    def __log(self, error, msg):
        time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_msg = "{time}: ({error}) {msg}\n".format(time=time, error=error.name, msg=msg)
        print(log_msg, end="")
        self.log_file.write(log_msg)
        self.log_file.flush()

    def __grab_lock(self):
        self.lock_fd = open(self.state['smart_ezbench_lock'], 'w')
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX)
            return True
        except IOError as e:
            self.__log(Criticality.EE, "Could not lock the report: " + str(e))
            return False

    def __release_lock(self):
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            self.lock_fd.close()
        except Exception as e:
            self.__log(Criticality.EE, "Cannot release the lock: " + str(e))
            pass

    def __reload_state_unlocked(self):
        # check if a report already exists
        try:
            with open(self.state['smart_ezbench_state'], 'rt') as f:
                self.state_read_time = time.time()
                try:
                    self.state = json.loads(f.read())
                except Exception as e:
                    self.__log(Criticality.EE, "Exception while reading the state: " + str(e))
                    pass
                return True
        except IOError as e:
            self.__log(Criticality.WW, "Cannot open the state file: " + str(e))
            pass
        return False

    def __reload_state(self, keep_lock = False):
        self.__grab_lock()
        ret = self.__reload_state_unlocked()
        if not keep_lock:
            self.__release_lock()
        return ret

    def __save_state(self):
        try:
            state_tmp = str(self.state['smart_ezbench_state']) + ".tmp"
            with open(state_tmp, 'wt') as f:
                f.write(json.dumps(self.state, sort_keys=True, indent=4, separators=(',', ': ')))
                f.close()
                os.rename(state_tmp, self.state['smart_ezbench_state'])
                return True
        except IOError:
            self.__log(Criticality.EE, "Could not dump the current state to a file!")
            return False

    def __create_ezbench(self, ezbench_path = None, profile = None, report_name = None):
        if ezbench_path is None:
            ezbench_path = self.state['ezbench_dir'] + "/core.sh"
        if profile is None:
            profile = self.profile()
        if report_name is None:
            report_name=self.state['report_name']

        return Ezbench(ezbench_path = ezbench_path, profile = profile,
                       report_name = report_name)

    def profile(self):
        if "profile" in self.state:
            return self.state['profile']
        else:
            return None

    def set_profile(self, profile):
        self.__reload_state(keep_lock=True)
        if 'profile' not in self.state:
            # Check that the profile exists!
            ezbench = self.__create_ezbench(profile = profile)
            run_info = ezbench.run_commits(["HEAD"], [], [], dry_run=True)
            if run_info == False:
                self.__log(Criticality.EE, "Invalid profile name '{profile}'.".format(profile=profile))
                self.__release_lock()
                return

            self.state['profile'] = profile
            self.__log(Criticality.II, "Ezbench profile set to '{profile}'".format(profile=profile))
            self.__save_state()
        else:
            self.__log(Criticality.EE, "You cannot change the profile of a report. Start a new one.")
        self.__release_lock()

    def add_benchmark(self, commit, benchmark, rounds = None):
        self.__reload_state(keep_lock=True)
        if commit not in self.state['commits']:
            self.state['commits'][commit] = dict()
            self.state['commits'][commit]["benchmarks"] = dict()

        if rounds is None:
            rounds = 3

        if benchmark not in self.state['commits'][commit]['benchmarks']:
            self.state['commits'][commit]['benchmarks'][benchmark] = dict()
            self.state['commits'][commit]['benchmarks'][benchmark]['rounds'] = rounds
        else:
            self.state['commits'][commit]['benchmarks'][benchmark]['rounds'] += rounds

        # if the number of rounds is equal to 0 for a benchmark, delete it
        if self.state['commits'][commit]['benchmarks'][benchmark]['rounds'] <= 0:
            del self.state['commits'][commit]['benchmarks'][benchmark]

        # Delete a commit that has no benchmark
        if len(self.state['commits'][commit]['benchmarks']) == 0:
            del self.state['commits'][commit]

        self.__save_state()
        self.__release_lock()

    def __prioritize_runs(self, task_tree, deployed_version):
        task_list = list()

        if deployed_version is not None and deployed_version in task_tree:
            for benchmark in task_tree[deployed_version]["benchmarks"]:
                rounds = task_tree[deployed_version]["benchmarks"][benchmark]["rounds"]
                task_list.append(TaskEntry(deployed_version, benchmark, rounds))
            del task_tree[deployed_version]

        # Add all the remaining tasks in whatever order!
        for commit in task_tree:
            for benchmark in task_tree[commit]["benchmarks"]:
                rounds = task_tree[commit]["benchmarks"][benchmark]["rounds"]
                task_list.append(TaskEntry(commit, benchmark, rounds))

        return task_list

    def run(self):
        self.__log(Criticality.II, "----------------------")
        self.__log(Criticality.II, "Starting a run: {report}".format(report=self.state['report_name']))
        self.__log(Criticality.II, "Checking the dependencies:")

        # check for dependencies
        if 'profile' not in self.state:
            self.__log(Criticality.EE, "    - Ezbench profile: Not set. Abort...")
            return False
        else:
            profile = self.state["profile"]
            self.__log(Criticality.II, "    - Ezbench profile: '{0}'".format(profile))

        # Create the ezbench runner
        ezbench = self.__create_ezbench()
        run_info = ezbench.run_commits(["HEAD"], [], [], dry_run=True)
        if run_info != False:
            deployed_version = run_info.deployed_commit
        else:
            deployed_version = None
        self.__log(Criticality.II, "    - Deployed version: '{0}'".format(deployed_version))
        self.__log(Criticality.II, "All the dependencies are met, generate a report...")

        # Generate a report to compare the goal with the current state
        report = genPerformanceReport(self.state['log_folder'], silentMode = True)
        self.__log(Criticality.II,
                   "The report contains {count} commits".format(count=len(report.commits)))

        # Walk down the report and get rid of every run that has already been made!
        task_tree = copy.deepcopy(self.state['commits'])
        for commit in report.commits:
            for result in commit.results:
                self.__log(Criticality.DD,
                           "Found {count} runs for benchmark {benchmark} using commit {commit}".format(count=len(result.data),
                                                                                                       commit=commit.sha1,
                                                                                                       benchmark=result.benchmark.full_name))
                if commit.sha1 not in task_tree:
                    continue
                if result.benchmark.full_name not in task_tree[commit.sha1]["benchmarks"]:
                    continue

                task_tree[commit.sha1]["benchmarks"][result.benchmark.full_name]['rounds'] -= len(result.data)

                if task_tree[commit.sha1]["benchmarks"][result.benchmark.full_name]['rounds'] <= 0:
                    del task_tree[commit.sha1]["benchmarks"][result.benchmark.full_name]

                if len(task_tree[commit.sha1]["benchmarks"]) == 0:
                    del task_tree[commit.sha1]

        if len(task_tree) == 0:
            self.__log(Criticality.II, "Nothing left to do, exit")
            return

        task_tree_str = pprint.pformat(task_tree)
        self.__log(Criticality.II, "Task list: {tsk_str}".format(tsk_str=task_tree_str))



        # Prioritize --> return a list of commits to do in order
        task_list = self.__prioritize_runs(task_tree, deployed_version)

        # Let's start!
        self.state['beenRunBefore'] = True

        # Start generating ezbench calls
        for e in task_list:
            self.__log(Criticality.DD,
                       "make {count} runs for benchmark {benchmark} using commit {commit}".format(count=e.rounds,
                                                                                                  commit=e.commit,
                                                                                                  benchmark=e.benchmark))
            ezbench.run_commits([e.commit], [e.benchmark + '$'], rounds=e.rounds)

        self.__log(Criticality.II, "Done")

# Report parsing
class Benchmark:
    def __init__(self, full_name, unit="undefined"):
        self.full_name = full_name
        self.prevValue = -1
        self.unit_str = unit

class BenchResult:
    def __init__(self, commit, benchmark, data_raw_file):
        self.commit = commit
        self.benchmark = benchmark
        self.data_raw_file = data_raw_file
        self.data = []
        self.runs = []
        self.unit_str = None

class Commit:
    def __init__(self, sha1, full_name, compile_log, patch, label):
        self.sha1 = sha1
        self.full_name = full_name
        self.compile_log = compile_log
        self.patch = patch
        self.results = []
        self.geom_mean_cache = -1
        self.label = label

    def geom_mean(self):
        if self.geom_mean_cache >= 0:
            return self.geom_mean_cache

        # compute the variance
        s = 1
        n = 0
        for result in self.results:
            if len(result.data) > 0:
                s *= array(result.data).mean()
                n = n + 1
        if n > 0:
            value = s ** (1 / n)
        else:
            value = 0

        geom_mean_cache = value
        return value

class Report:
    def __init__(self, benchmarks, commits, notes):
        self.benchmarks = benchmarks
        self.commits = commits
        self.notes = notes

def readCsv(filepath, wantFrametime = False):
    data = []

    h1 = re.compile('^# (.*) of \'(.*)\' using commit (.*)$')
    h2 = re.compile('^# (.*) \\((.*) is better\\) of \'(.*)\' using commit (.*)$')

    with open(filepath, 'rt') as f:
        reader = csv.reader(f)
        unit = None
        try:
            for row in reader:
                if row is None or len(row) == 0:
                    continue

                # try to extract information from the header
                m1 = h1.match(row[0])
                m2 = h2.match(row[0])
                if m2 is not None:
                    # groups: unit type, more|less qualifier, benchmark, commit_sha1
                    unit = m2.groups()[0]
                elif m1 is not None:
                    # groups: unit type, benchmark, commit_sha1
                    unit = unit = m1.groups()[0]

                # Read the actual data
                if len(row) > 0 and not row[0].startswith("# "):
                    try:
                        data.append(float(row[0]))
                    except ValueError as e:
                        sys.stderr.write('Error in file %s, line %d: %s\n' % (filepath, reader.line_num, e))
        except csv.Error as e:
            sys.stderr.write('file %s, line %d: %s\n' % (filepath, reader.line_num, e))
            return [], "none"

    # Convert to frametime if needed
    if wantFrametime and unit == "FPS":
        unit = "ms"
        for i in range(0, len(data)):
            data[i] = 1000.0 / data[i]

    return data, unit

def readCommitLabels():
    labels = dict()
    try:
        f = open( "commit_labels", "r")
        try:
            labelLines = f.readlines()
        finally:
            f.close()
    except IOError:
        return labels

    for labelLine in labelLines:
        fields = labelLine.split(" ")
        sha1 = fields[0]
        label = fields[1].split("\n")[0]
        labels[sha1] = label

    return labels

def readNotes():
    try:
        with open("notes", 'rt') as f:
            return f.readlines()
    except:
        return []

def genPerformanceReport(log_folder, wantFrametime = False, silentMode = False):
    benchmarks = []
    commits = []
    labels = dict()
    notes = []

    # Save the current working directory and switch to the log folder
    cwd = os.getcwd()
    os.chdir(log_folder)

    # Look for the commit_list file
    try:
        f = open( "commit_list", "r")
        try:
            commitsLines = f.readlines()
        finally:
            f.close()
    except IOError:
        if not silentMode:
            sys.stderr.write("The log folder '{0}' does not contain a commit_list file\n".format(log_folder))
        return Report(benchmarks, commits, notes)

    # Read all the commits' labels
    labels = readCommitLabels()

    # Check that there are commits
    if (len(commitsLines) == 0):
        if not silentMode:
            sys.stderr.write("The commit_list file is empty\n")
        return Report(benchmarks, commits, notes)

    # Gather all the information from the commits and generate the images
    if not silentMode:
        print ("Reading the results for {0} commits".format(len(commitsLines)))
    commits_txt = ""
    table_entries_txt = ""
    for commitLine in commitsLines:
        full_name = commitLine.strip(' \t\n\r')
        sha1 = commitLine.split()[0]
        compile_log = sha1 + "_compile_log"
        patch = sha1 + ".patch"
        label = labels.get(sha1, sha1)
        commit = Commit(sha1, full_name, compile_log, patch, label)

        # find all the benchmarks
        benchFiles = glob.glob("{sha1}_bench_*".format(sha1=commit.sha1));
        benchs_txt = ""
        for benchFile in benchFiles:
            # Skip when the file is a run file (finishes by #XX)
            if re.search(r'#\d+$', benchFile) is not None:
                continue

            # Skip on error files
            if re.search(r'\.(stderr|stdout|errors)$', benchFile) is not None:
                continue

            # Get the bench name
            bench_name = benchFile.replace("{sha1}_bench_".format(sha1=commit.sha1), "")

            # Find the right Benchmark or create one if none are found
            try:
                benchmark = next(b for b in benchmarks if b.full_name == bench_name)
            except StopIteration:
                benchmark = Benchmark(bench_name)
                benchmarks.append(benchmark)

            if wantFrametime:
                unit = "ms"
            else:
                unit = "FPS"

            # Create the result object
            result = BenchResult(commit, benchmark, benchFile)

            # Read the data and abort if there is no data
            result.data, result.unit_str = readCsv(benchFile, wantFrametime)
            if len(result.data) == 0:
                continue

            # Check that the result file has the same default v
            if benchmark.unit_str != result.unit_str:
                if benchmark.unit_str != "undefined":
                    msg = "The unit used by the benchmark '{bench}' changed from '{unit_old}' to '{unit_new}' in commit {commit}"
                    print(msg.format(bench=bench_name,
                                     unit_old=benchmark.unit_str,
                                     unit_new=unit,
                                     commit=commit.sha1))
                benchmark.unit_str = result.unit_str

            # Look for the runs
            runsFiles = glob.glob("^{benchFile}#[0-9]+".format(benchFile=benchFile));
            for runFile in runsFiles:
                data = readCsv(runFile, wantFrametime)
                if len(data) > 0:
                    result.runs.append(data)

            # Add the result to the commit's results
            commit.results.append(result)

        # Add the commit to the list of commits
        commit.results = sorted(commit.results, key=lambda res: res.benchmark.full_name)
        commits.append(commit)

    # Sort the list of benchmarks
    benchmarks = sorted(benchmarks, key=lambda bench: bench.full_name)

    # Read the notes before going back to the original folder
    notes = readNotes()

    # Go back to the original folder
    os.chdir(cwd)

    return Report(benchmarks, commits, notes)

def getPerformanceResultsCommitBenchmark(commit, benchmark, wantFrametime = False):
    for result in commit.results:
        if result.benchmark != benchmark:
            continue

        return array(result.data)

    return array([])

def getResultsBenchmarkDiffs(commits, benchmark, wantFrametime = False):
    results = []

    # Compute a report per application
    i = 0
    origValue = -1
    for commit in commits:
        resultFound = False
        for result in commit.results:
            if result.benchmark != benchmark:
                continue

            value = array(result.data).mean()
            if origValue > -1:
                if wantFrametime:
                    diff = (origValue * 100.0 / value) - 100.0
                else:
                    diff = (value * 100.0 / origValue) - 100.0
            else:
                origValue = value
                diff = 0

            results.append([i, diff])
            resultFound = True

        if not resultFound:
            results.append([i, NaN])
        i = i + 1

    return results

def getResultsGeomDiffs(commits, wantFrametime = False):
    results = []

    # Compute a report per application
    i = 0
    origValue = -1
    for commit in commits:
        value = commit.geom_mean()
        if origValue > -1:
            if wantFrametime:
                diff = (origValue * 100.0 / value) - 100.0
            else:
                diff = (value * 100.0 / origValue) - 100.0
        else:
            origValue = value
            diff = 0

        results.append([i, diff])
        i = i + 1

    return results

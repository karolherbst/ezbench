#!/bin/bash

# Thanks to stack overflow for writing most of this script! It has been tested with bash and zsh only!
# Author: Martin Peres <martin.peres@free.fr>

#set -o xtrace

#Default values
rounds=3
avgBuildTime=30
makeCommand="make -j8 install"
lastNCommits=
gitRepoDir=''
ezBenchDir=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )

# Default user options
source "$ezBenchDir/test_options.sh"

# initial cleanup
rm $ezBenchDir/results 2> /dev/null > /dev/null
mkdir $ezBenchDir/logs/ 2> /dev/null

# Generate the list of available tests
typeset -A availTests
i=0
for test_file in $ezBenchDir/tests.d/*.test
do
    unset test_name
    unset test_exec_time

    source $test_file || continue
    if [ -z "$test_name" ]; then continue; fi
    if [ -z "$test_exec_time" ]; then continue; fi
    availTests[$i]=$test_name
    i=$(($i+1))
done

# parse the options
function show_help {
    echo "    ezbench.sh -p <path_git_repo> -n <last n commits>"
    echo ""
    echo "    Optional arguments:"
    echo "        -r <benchmarking rounds> (default: 3)"
    echo "        -b benchmark1 benchmark2 ..."
    echo "        -m <make command> (default: 'make -j8 install')"
    echo ""
    echo "    Other actions:"
    echo "        -h/?: Show this help message"
    echo "        -l: List the available tests"
}
function available_tests {
    printf "Available tests: "
    for (( t=0; t<${#availTests[@]}; t++ ));
    do
        printf "${availTests[$t]} "
    done
    printf "\n"
    
}
while getopts "h?p:n:r:b:m:l" opt; do
    case "$opt" in
    h|\?)
        show_help 
        exit 0
        ;;
    p)  gitRepoDir=$OPTARG
        ;;
    n)  lastNCommits=$OPTARG
        ;;
    r)  rounds=$OPTARG
        ;;
    b)  testsList=$OPTARG
        ;;
    m)  makeCommand=$OPTARG
        ;;
    l)
        available_tests
        exit 0
        ;;
    :)
      echo "Option -$OPTARG requires an argument." >&2
      exit 1
      ;;
    esac
done

# Check that the list of wanted benchmarks is OK
testsListOK=1
for test in $testsList
do
    if [[ ! " ${availTests[@]} " =~ " ${test} " ]]; then
        echo "The test '$test' does not exist."
        testsListOK=0
    fi
done
if [[ $testsListOK == 0 ]]; then
    available_tests
    exit 1
fi

# redirect the output to both a log file and stdout
exec > >(tee $ezBenchDir/results)
exec 2>&1

# function to call on exit
function finish {
    # to be executed on exit, possibly twice!
    git reset --hard $commit_head 2> /dev/null
    cp $ezBenchDir/results $ezBenchDir/logs/`date +"%y-%m-%d-%T"`_results

    # Execute the user-defined post hook
    callIfDefined ezbench_post_hook
}
trap finish EXIT
trap finish INT # Needed for zsh

# Check the git repo
cd $gitRepoDir
tmp=$(git log HEAD...HEAD~ 2> /dev/null > /dev/null)
if [ $? -ne 0 ]
then
    printf "ERROR: The path '$gitRepoDir' does not contain a valid git repository. Aborting...\n"
    exit 1
fi

# Save and display the HEAD commit
commit_head=$(git show HEAD | grep commit | cut -d ' ' -f 2)
echo "Original commit = $commit_head"

# Generate the actual list of tests
typeset -A testNames
typeset -A testPrevFps
i=0
total_round_time=0
printf "Tests that will be run: "
for test_file in $ezBenchDir/tests.d/*.test
do
    unset test_name
    unset test_exec_time

    source $test_file || continue
    
    # Check that the user wants this test or not
    if [ -n "$testsList" ]; then
        if [[ "$testsList" != *"$test_name"* ]]; then
            continue
        fi
    fi
    
    testNames[$i]=$test_name 
    testPrevFps[$i]=-1
    
    echo -n "${testNames[$i]} "
    
    total_round_time=$(( $total_round_time + $test_exec_time ))
    i=$(($i+1))
done
printf "\n"

# Estimate the execution time
secs=$(( ($total_round_time * $rounds + $avgBuildTime) * $lastNCommits))
printf "Estimated run time: %02dh:%02dm:%02ds\n\n" $(($secs/3600)) $(($secs%3600/60)) $(($secs%60))
startTime=`date +%s`

# Execute the user-defined pre hook
function callIfDefined() {
    if [ "`type -t $1`" == 'function' ]; then
        local funcName=$1
        shift
        $funcName $@
    fi
}
callIfDefined ezbench_pre_hook

# Iterate through the commits
for commit in $(git log --oneline --reverse -$lastNCommits | cut -d ' ' -f1)
do
    # Make sure we are in the right folder
    cd $gitRepoDir

    # Select the commit of interest
    git reset --hard $commit > /dev/null
    commitName=$(git show HEAD | head -n 5 | tail -n 1 | cut -d ' ' -f 5-)
    printf "$commit: $commitName\n"

    # Call the user-defined pre-compile hook
    callIfDefined compile_pre_hook

    # Compile the commit and check for failure. If it failed, go to the next commit.
    date=`date +"%y-%m-%d-%T"`
    compile_logs=$ezBenchDir/logs/${date}_compile_log_${commit}
    eval $makeCommand > $compile_logs 2>&1
    if [ $? -ne 0 ]
    then
        printf "    ERROR: Compilation failed, log saved in $compile_logs. Continue\n\n"
        git reset --hard HEAD~ > /dev/null 2> /dev/null
        continue
    fi

    # Call the user-defined post-compile hook
    callIfDefined compile_post_hook

    # Iterate through the tests
    for (( t=0; t<${#testNames[@]}; t++ ));
    do
        # Run the benchmark
        runFuncName=${testNames[$t]}_run
        preHookFuncName=${testNames[$t]}_run_pre_hook
        postHookFuncName=${testNames[$t]}_run_post_hook
        callIfDefined $preHookFuncName
        fpsTest=$($runFuncName $rounds)
        callIfDefined $postHookFuncName

        # Save the raw data
        date=`date +"%y-%m-%d-%T"`
        fps_logs=$ezBenchDir/logs/${date}_commit_${commit}_bench_${testNames[$t]}
        echo "FPS of '${testNames[$t]}' using commit ${commit}" > $fps_logs
        echo "$fpsTest" >> $fps_logs

        # Process the data ourselves
        statsTest=$(echo "$fpsTest" | $ezBenchDir/fps_stats.awk)
        fpsTest=$(echo $statsTest | cut -d ' ' -f 1)
        if (( $(echo "${testPrevFps[$t]} == -1" | bc -l) ))
        then
                testPrevFps[$t]=$fpsTest
        fi
        fpsDiff=$(echo "scale=3;100.0 - (${testPrevFps[$t]} * 100.0 / $fpsTest)" | bc)
        testPrevFps[$t]=$fpsTest

        printf "	${testNames[$t]} : (diff = $fpsDiff%%) $statsTest\n"
    done

    printf "\n"

done

endTime=`date +%s`
runtime=$((endTime-startTime))
printf "Actual run time: %02dh:%02dm:%02ds\n\n" $(($runtime/3600)) $(($runtime%3600/60)) $(($runtime%60))

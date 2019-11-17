#!/usr/bin/env python3
import os
import re
import sys
import subprocess


def main():
    pid = sys.argv[1]
    max_stack_depth = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    show_inactive = int(bool(sys.argv[3])) if len(sys.argv) > 3 else False
    print("Generating thread stats for Java Process {}".format(pid))
    calculate_thread_stats(pid, max_stack_depth, show_inactive)


def calculate_thread_stats(pid, max_stack_depth, show_inactives):
    stats_tid = {}
    out = subprocess.Popen(["pidstat", "-u", "-H", "-t", "-h", "-p", pid], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    stdout, stderr = out.communicate()
    lines = stdout.decode().split(os.linesep)
    inactive_threads = []
    for line in lines:
        values = re.split("(\s+)", line)
        # for debugging:
        # print("Parsed values: " + "|".join(values))
        if len(values) >= 10 and values[6].isdigit():
            thread_id = int(values[6])
            stats = {
                'user_cpu': float(values[8]),
                'system_cpu': float(values[10]),
                'guest_cpu': float(values[12]),
                'wait_cpu': float(values[14]),
                'total_cpu': float(values[16])
            }
            if show_inactives:
                stats_tid[thread_id] = stats
            else:
                if stats['total_cpu'] > 0.0:
                    stats_tid[thread_id] = stats
                else:
                    inactive_threads.append(str(thread_id))
    thread_by_tid = stack_info(pid, max_stack_depth)
    stats_sorted_by_total = sorted(stats_tid.items(), key=lambda x: x[1]['total_cpu'], reverse=True)
    for tid, stats in stats_sorted_by_total:
        print("Thread {} CPU Total: {}% [%usr: {}%, %system: {}%, %guest: {}%, %wait: {}%] CPU. Info:\n{}\n"
              .format(tid, stats['total_cpu'], stats['user_cpu'], stats['system_cpu'], stats['guest_cpu'],
                      stats['wait_cpu'], thread_by_tid.get(tid, "- No info provided -")))
    if show_inactives is False:
        print("Inactive threads: {}".format(", ".join(inactive_threads)))


def stack_info(pid, max_stack_depth):
    thread_by_tid = {}
    out = subprocess.Popen(["jstack", pid], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    stdout, stderr = out.communicate()
    thread_dumps = stdout.decode().split(os.linesep + os.linesep)
    for thread_dump in thread_dumps:
        if "tid=" in thread_dump:
            nic_result = re.search("nid=(\w*)", thread_dump)
            if nic_result is not None:
                thread_id = int(nic_result.group(1), 16)
                thread_by_tid[thread_id] = os.linesep.join(thread_dump.split(os.linesep)[0:(2+max_stack_depth)])
            # print(os.linesep.join(lines[0:5]))
        # else:
            # print("Line with no thread info:\n{}".format(thread_dump))
    return thread_by_tid


if __name__ == '__main__':
    main()

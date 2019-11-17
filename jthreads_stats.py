#!/usr/bin/env python3
import os
import re
import sys

import subprocess


def main():
    if len(sys.argv) < 2:
        print("Missing parameters.\nUsage: {} {{pid}} [max_stack_depth] [top number]\n"
              "Only parameter [pid] is mandatory.".format(sys.argv[0]))
        exit(1)
    pid = sys.argv[1]
    max_stack_depth = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    top_num = int(int(sys.argv[3])) if len(sys.argv) > 3 else 10
    print("Generating thread stats for Java Process {}\n\n".format(pid))
    calculate_thread_stats(pid, max_stack_depth, top_num)


def calculate_thread_stats(pid, max_stack_depth, top_num):
    stats_tid, inactive_threads = process_stat(pid)
    thread_by_tid = stack_info(pid, max_stack_depth)
    stats_sorted_by_cpu = sorted(stats_tid.items(), key=lambda x: x[1]['total_cpu'], reverse=True)
    top_n_stats = stats_sorted_by_cpu if top_num < 0 else stats_sorted_by_cpu[0:top_num]
    for tid, stats in top_n_stats:
        print(
            "Thread {} attached in CPU #{}\n"
            "{:3.2f}% CPU [%usr: {:3.2f}, %system: {:3.2f}, %guest: {:3.2f}, %wait: {:3.2f}]\n"
            "I/O [kB_rd/s: {}, kB_wr/s: {}]\n"
            "{}\n\n"
            .format(tid, stats['cpu'], stats['total_cpu'], stats['user_cpu'], stats['system_cpu'],
                    stats['guest_cpu'], stats['wait_cpu'], stats['kb_rd_per_sec'], stats['kb_wr_per_sec'],
                    thread_by_tid.get(tid, "- No info provided -")))
    print("These threads seem to be inactive (CPU 0%): {}".format(", ".join(inactive_threads)))


def process_stat(pid):
    stats_tid = {}
    pidstat_env = os.environ.copy()
    pidstat_env['S_COLORS'] = "never"
    out = subprocess.Popen(["pidstat", "-u", "-d", "-H", "-t", "-h", "-p", pid, "1", "1"],
                           stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT,
                           env=pidstat_env)
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
                'total_cpu': float(values[16]),
                'cpu': values[18].rjust(2),
                'kb_rd_per_sec': float(values[20]),
                'kb_wr_per_sec': float(values[22]),
            }
            if stats['total_cpu'] > 0.0:
                stats_tid[thread_id] = stats
            else:
                inactive_threads.append(str(thread_id))
    return stats_tid, inactive_threads


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
                thread_by_tid[thread_id] = os.linesep.join(thread_dump.split(os.linesep)[0:(2 + max_stack_depth)])
    return thread_by_tid


if __name__ == '__main__':
    main()

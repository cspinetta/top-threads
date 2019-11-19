#!/usr/bin/env python3
import os
import re
import sys

import subprocess
import curses
from curses import wrapper


TITLE_ROW = "Generating thread stats for Java Process {}".format(sys.argv[1] if len(sys.argv) > 1 else "-")
log = []


# def main_2():
#
#     if len(sys.argv) < 2:
#         print("Missing parameters.\nUsage: {} {{pid}} [max_stack_depth] [top number]\n"
#               "Only parameter [pid] is mandatory.".format(sys.argv[0]))
#         exit(1)
#     pid = sys.argv[1]
#
#     pidstat_env = os.environ.copy()
#     pidstat_env['S_COLORS'] = "never"
#     process = subprocess.Popen(["pidstat", "-u", "-d", "-H", "-t", "-h", "-p", pid, "1"],
#                                stdout=subprocess.PIPE,
#                                stderr=subprocess.STDOUT,
#                                env=pidstat_env)
#     lines = []
#     for output in iter(lambda: process.stdout.readline(), b''):
#         line = output.decode().strip()
#         if len(line) > 10:
#             print('Append nice line: {}'.format(line))
#         else:
#             print('Receive ugly line: {}'.format(line))
#             if len(lines) > 0:
#                 print('going to processing')
#                 # stats_display.process_stats(lines)
#             lines.clear()
def main():
    if len(sys.argv) < 2:
        print("Missing parameters.\nUsage: {} {{pid}} [max_stack_depth] [top number]\n"
              "Only parameter [pid] is mandatory.".format(sys.argv[0]))
        exit(1)
    pid = sys.argv[1]
    max_stack_depth = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    top_num = int(int(sys.argv[3])) if len(sys.argv) > 3 else 10

    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_CYAN, curses.COLOR_BLACK)

    exc = None
    try:
        # print("Generating thread stats for Java Process {}\n\n".format(pid))
        call_pidstat(pid, StatsDisplay(pid, max_stack_depth, top_num, stdscr))
    except Exception as e:
        exc = e
    finally:
        curses.echo()
        curses.nocbreak()
        curses.endwin()
        print("\n".join(log))
        if exc is not None:
            print(exc)


def call_pidstat(pid, stats_display):
    # stats_tid = {}
    pidstat_env = os.environ.copy()
    pidstat_env['S_COLORS'] = "never"
    process = subprocess.Popen(["pidstat", "-u", "-d", "-H", "-t", "-h", "-p", pid, "1"],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               env=pidstat_env)
    lines = []
    for output in iter(lambda: process.stdout.readline(), b''):
        line = output.decode().strip()
        if len(line) > 10:
            lines.append(line)  # .strip()
        else:
            if len(lines) > 0:
                stats_display.process_stats(pid, lines, calculate_sched_stats)
            lines.clear()


def calculate_sched_stats(pid, tid):
    # stats_tid = {}
    with open("/proc/{}/task/{}/schedstat".format(pid, tid)) as schedule_stats:
        values = schedule_stats.read()
        return int(values.split(" ")[1])


class StatsDisplay:

    def __init__(self, pid, max_stack_depth, top_num, stdscr):
        self.pid = pid
        self.max_stack_depth = max_stack_depth
        self.top_num = top_num
        self.stdscr = stdscr

    def process_stats(self, pid, stat_lines, schedule_stat_fun):
        stats_by_tid, inactive_threads = self.scrape_stat(stat_lines)
        thread_by_tid = self.stack_info()
        stats_sorted_by_cpu = sorted(stats_by_tid.items(), key=lambda x: x[1]['total_cpu'], reverse=True)
        top_n_stats = stats_sorted_by_cpu if self.top_num < 0 else stats_sorted_by_cpu[0:self.top_num]
        run_queue_latency_by_tid = {tid: schedule_stat_fun(pid, tid) for tid in stats_by_tid.keys()}
        self.display(top_n_stats, thread_by_tid, run_queue_latency_by_tid)

    def display(self, thread_stats, thread_by_tid, run_queue_latency_by_tid):
        stdscr = self.stdscr
        stdscr.scrollok(1)
        stdscr.idlok(1)
        stdscr.scroll(100)
        stdscr.addstr(0, 0, TITLE_ROW, curses.A_BOLD)

        stdscr.move(2, 0)

        max_y, max_x = stdscr.getmaxyx()
        max_lines = max_y - 8

        current_position = 2

        for tid, stats in thread_stats:
            current_position = self.next_line(current_position, max_lines, tid, stats, thread_by_tid,
                                              run_queue_latency_by_tid)
            if current_position >= max_lines:
                break
        stdscr.refresh()

    def next_line(self, position, max_lines, tid, stats, thread_by_tid, run_queue_latency_by_tid):
        stdscr = self.stdscr

        if position >= max_lines:
            return position

        thread_info = thread_by_tid.get(tid, {})
        thread_name = thread_info.get('name', "-name not found-")
        dump = thread_info.get('dump', "- No info provided -")

        stdscr.addstr("Thread [tid {} CPU #{}, Run Queue Latency {}] \"{}\""
                      .format(tid, stats['cpu'], self.nanos_fmt(run_queue_latency_by_tid[tid]),
                              thread_name), curses.A_BOLD)
        stdscr.addstr(os.linesep)

        position += 1
        if position >= max_lines:
            return position

        stdscr.addstr("{:3.2f}%".format(stats['total_cpu']), self.cpu_color(stats['total_cpu']))
        stdscr.addstr(" CPU [%usr: ")
        stdscr.addstr("{:3.2f}".format(stats['user_cpu']), self.cpu_color(stats['user_cpu']))
        stdscr.addstr(", %system: ")
        stdscr.addstr("{:3.2f}".format(stats['system_cpu']), self.cpu_color(stats['system_cpu']))
        stdscr.addstr(", %guest: ")
        stdscr.addstr("{:3.2f}".format(stats['guest_cpu']), self.cpu_color(stats['guest_cpu']))
        stdscr.addstr(", %wait: ")
        stdscr.addstr("{:3.2f}".format(stats['wait_cpu']), self.cpu_color(stats['wait_cpu']))
        stdscr.addstr(os.linesep)

        position += 1
        if position >= max_lines:
            return position

        stdscr.addstr("I/O [kB_rd/s: ")
        stdscr.addstr("{}".format(stats['kb_rd_per_sec']), self.io_color(stats['kb_rd_per_sec']))
        stdscr.addstr(", kB_wr/s: ")
        stdscr.addstr("{}".format(stats['kb_wr_per_sec']), self.io_color(stats['kb_wr_per_sec']))
        stdscr.addstr("]")
        stdscr.addstr(os.linesep)

        for line in dump.split(os.linesep):
            stdscr.addstr(line)
            stdscr.addstr(os.linesep)
            position += 1
            if position >= max_lines:
                return position

        stdscr.addstr(os.linesep)

        position += 1
        return position

    @staticmethod
    def cpu_color(value):
        if value < 20:
            return curses.color_pair(1)
        elif value < 60:
            return curses.color_pair(3)
        else:
            return curses.color_pair(2)

    @staticmethod
    def io_color(value):
        if value < 20:
            return curses.color_pair(1)
        elif value < 100:
            return curses.color_pair(3)
        else:
            return curses.color_pair(2)

    @staticmethod
    def scrape_stat(lines):
        stats_by_tid = {}
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
                stats_by_tid[thread_id] = stats
                if stats['total_cpu'] <= 0.0:
                    inactive_threads.append(str(thread_id))
        return stats_by_tid, inactive_threads

    def stack_info(self):
        thread_by_tid = {}
        out = subprocess.Popen(["jstack", self.pid], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        stdout, stderr = out.communicate()
        thread_dumps = stdout.decode().split(os.linesep + os.linesep)
        for thread_dump in thread_dumps:
            if "tid=" in thread_dump:
                tid_result = re.search("nid=(\w*)", thread_dump)
                if tid_result is not None:
                    thread_id = int(tid_result.group(1), 16)
                    name_search = thread_dump.split('"')
                    name = name_search[1] if len(name_search) > 0 else "-name not found-"
                    dump = os.linesep.join(thread_dump.split(os.linesep)[0:(2 + self.max_stack_depth)])
                    thread_by_tid[thread_id] = {
                        'name': name,
                        'dump': dump,
                    }
        return thread_by_tid

    @staticmethod
    def sizeof_fmt(num, suffix='B'):
        for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
            if abs(num) < 1024.0:
                return "%3.1f%s%s" % (num, unit, suffix)
            num /= 1024.0
        return "%.1f%s%s" % (num, 'Yi', suffix)

    @staticmethod
    def nanos_fmt(num):
        for unit in [' nanos', ' micros', ' millis']:
            if abs(num) < 1000.0:
                return "%3.1f%s" % (num, unit)
            num /= 1000.0
        return "%.1f%s" % (num, ' seconds')


if __name__ == '__main__':
    wrapper(main())

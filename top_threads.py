#!/usr/bin/env python3

import argparse
import curses
import datetime
import logging
import os
import re
import subprocess
import sys
from collections import deque
from functools import reduce

SYSTAT_VERSION_OLD = 0
SYSTAT_VERSION_NEW = 1

log = []
script_pid = os.getpid()
pid = 0
debug_enabled = False
systat_version = None
kind_systat_version = SYSTAT_VERSION_NEW


def main():
    global pid
    global debug_enabled
    try:
        parser = create_parser()
        args = parser.parse_args()
        pid = args.pid
        if not check_pid(pid):
            sys.exit("PID {} not exist".format(pid))
        load_systat_version()
        params = Params(args.stack_size, args.number, args.sort_field, args.jstack_enabled, args.debug_enabled)
        java_handler = JavaHotSpotHandler(params.jstack_enabled)
        sort_description, stats_sorter = StatsSorter.by_field(params.field_sort)
        title = title_row(java_handler.is_instrumented_java, sort_description)
        debug_enabled = params.debug_enabled
        debug_log = "Debug is enabled" if debug_enabled else "Debug is disabled"
        systat_log = "Systat version {} (output with {} version)".format(systat_version,
                                                                         "New" if kind_systat_version == SYSTAT_VERSION_NEW else "Old")
        filename = os.path.basename(__file__)
        log_info("Running {} with pid {}.\n{}\n{}".format(filename, os.getpid(), debug_log, systat_log))
        log_debug("Sys info: {}".format(sys.version))
        if args.display_type == 'refresh':
            run_refresh_view(params, stats_sorter, java_handler, title)
        else:
            run_terminal_view(params, stats_sorter, java_handler, title)
    except KeyboardInterrupt:
        pass
    except Exception as exp:
        # it's safe to use logging here because the curses window has been closed
        logging.exception("unexpected exception")
    finally:
        if len(log) > 0:
            print("\nExecution log:\n")
        print("\n".join(log))


def load_systat_version():
    global systat_version
    global kind_systat_version
    systat_version = get_systat_version()
    parsable_version = systat_version.split('.')
    if len(parsable_version) > 1 and (
            (int(parsable_version[0]) == 11 and int(parsable_version[1]) >= 6) or int(parsable_version[0]) > 11):
        kind_systat_version = SYSTAT_VERSION_NEW
    else:
        kind_systat_version = SYSTAT_VERSION_OLD


def create_parser():
    parser = argparse.ArgumentParser(description='Tool for analysing active Threads')
    parser.add_argument('-p', required=True,
                        type=int, dest='pid',
                        help='Process ID')
    parser.add_argument('-n', nargs='?', dest='number',
                        type=int, default=10,
                        help='Number of threads to show per sample. Default: 10')
    parser.add_argument('--max-stack-depth', '-m', nargs='?',
                        type=int, default=1, dest='stack_size',
                        help='Max number of stack frames (only when jstack can be used). Default: 1')
    parser.add_argument('--sort', '-s', nargs='?', dest='sort_field',
                        choices=['cpu', 'rq', 'disk', 'disk-rd', 'disk-wr'], default='cpu',
                        help='Field used for sorting. Default: cpu')
    parser.add_argument('--display', '-d', nargs='?', dest='display_type',
                        choices=['terminal', 'refresh'], default='refresh',
                        help='Select the way to display the info: terminal or refresh. Default: refresh')
    parser.add_argument('--no-jstack', dest='jstack_enabled',
                        action="store_false",
                        help='Turn off usage of jstack to retrieve thread info like name and stack')
    parser.add_argument('--debug', dest='debug_enabled',
                        action="store_true",
                        help='Turn on logs for debugging purposes')
    return parser


def title_row(is_instrumented_java, sort_description):
    if is_instrumented_java:
        return "Generating thread stats for Process {} (Instrumented Java HotSpot) - {}".format(pid, sort_description)
    else:
        return "Generating thread stats for Process {} - {}".format(pid, sort_description)


def run_terminal_view(params, stats_sorter, java_handler, title):
    call_pidstat(StatsProcessor(params, StatsTerminalPrinter(title), stats_sorter, java_handler))


def run_refresh_view(params, stats_sorter, java_handler, title):
    with StatsRefreshPrinter(title) as printer:
        call_pidstat(StatsProcessor(params, printer, stats_sorter, java_handler))


def log_info(msg):
    log.append("{} | INFO | {}".format(datetime.datetime.now(), msg))


def log_debug(msg):
    if debug_enabled:
        log.append("{} | DEBUG | {}".format(datetime.datetime.now(), msg))


def get_systat_version():
    return subprocess.getoutput("pidstat -V | cut -d ' ' -f 3 | head -1")


def check_pid(pid):
    """ Check For the existence of a unix pid. """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def call_pidstat(stats_processor):
    pidstat_env = os.environ.copy()
    pidstat_env['S_COLORS'] = "never"
    fix_time_display = []
    if kind_systat_version == SYSTAT_VERSION_NEW:
        fix_time_display.append("-H")
    args = ["pidstat", "-u", "-d", "-t", "-h"] + fix_time_display + ["-p", str(pid), "1"]
    process = subprocess.Popen(args,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               env=pidstat_env)
    try:
        lines = []
        iter_num = 0
        for output in iter(lambda: process.stdout.readline(), b''):
            line = output.decode().strip()
            if len(line) > 10:
                lines.append(line)  # .strip()
            else:
                if len(lines) > 0:
                    iter_num += 1
                    stats_processor.process_stats(lines, iter_num)
                lines.clear()
    finally:
        lines = []
        for output in iter(lambda: process.stderr.readline(), b''):
            lines.append(output.decode().strip())
        if len(lines) > 0:
            log_info("Error from pidstat: {}".format("".join(lines)))


class Params:

    def __init__(self, max_stack_depth, top_num, field_sort, jstack_enabled, debug_enabled):
        self.max_stack_depth = max_stack_depth
        self.top_num = top_num
        self.field_sort = field_sort
        self.jstack_enabled = jstack_enabled
        self.debug_enabled = debug_enabled


class StatsSorter:

    @staticmethod
    def by_field(field):
        if field == "cpu":
            msg = 'Sorting by CPU'
            return msg, lambda x: x.thread_stats.cpu.total_cpu
        elif field == "rq":
            msg = 'Sorting by run-queue latency'
            return msg, lambda x: x.thread_stats.scheduler_stats.delta_run_queue_latency
        elif field == "disk":
            msg = 'Sorting by Disk (read/sec + write/sec)'
            return msg, lambda x: x.thread_stats.disk.kb_rd_per_sec + x.thread_stats.disk.kb_wr_per_sec
        elif field == "disk-rd":
            msg = 'Sorting by Disk (read/sec)'
            return msg, lambda x: x.thread_stats.disk.kb_rd_per_sec
        elif field == "disk-wr":
            msg = 'Sorting by Disk (write/sec)'
            return msg, lambda x: x.thread_stats.disk.kb_wr_per_sec
        else:
            msg = 'Sorting by default (CPU)'
            return msg, lambda x: x.thread_stats.cpu.total_cpu


class ThreadInfo:

    def __init__(self, tid, name="", dump="", thread_stats=None):
        self.tid = tid
        self.name = name
        self.dump = dump
        self.thread_stats = thread_stats if thread_stats is not None else ThreadStats(tid)

    def update_name(self, name):
        self.name = name

    def update_dump(self, dump):
        self.dump = dump


class ThreadStats:

    def __init__(self, tid, cpu=None, disk=None, scheduler_stats=None):
        self.tid = tid
        self.cpu = cpu if cpu is not None else ThreadCPUStats(tid)
        self.disk = disk if disk is not None else ThreadDiskStats(tid)
        self.scheduler_stats = scheduler_stats if scheduler_stats is not None else SchedulerStats(tid)


class ThreadCPUStats:

    def __init__(self, tid, cpu=0, total_cpu=0, user_cpu=0, system_cpu=0, guest_cpu=0, wait_cpu=0):
        self.tid = tid
        self.cpu = cpu
        self.total_cpu = total_cpu
        self.user_cpu = user_cpu
        self.system_cpu = system_cpu
        self.guest_cpu = guest_cpu
        self.wait_cpu = wait_cpu


class ThreadDiskStats:

    def __init__(self, tid, kb_rd_per_sec=0, kb_wr_per_sec=0):
        self.tid = tid
        self.kb_rd_per_sec = kb_rd_per_sec
        self.kb_wr_per_sec = kb_wr_per_sec


class SchedulerStats:

    def __init__(self, tid):
        self.tid = tid
        self.spent_on_cpu = 0
        self.run_queue_latency = 0
        self.timeslices_on_current_cpu = 0
        self.delta_spent_on_cpu = 0
        self.delta_run_queue_latency = 0
        self.delta_timeslices_on_current_cpu = 0

    def update(self, on_cpu, on_runqueue, timeslices):
        if on_runqueue < self.run_queue_latency:
            log_debug("TID: {}, on_runqueue {} -> {} (received: {})"
                      .format(self.tid,
                              StatsRefreshPrinter.nanos_fmt(self.run_queue_latency),
                              StatsRefreshPrinter.nanos_fmt(on_runqueue - self.run_queue_latency),
                              StatsRefreshPrinter.nanos_fmt(on_runqueue)))
        self.delta_spent_on_cpu = on_cpu - self.spent_on_cpu
        self.spent_on_cpu = on_cpu
        self.delta_run_queue_latency = on_runqueue - self.run_queue_latency
        self.run_queue_latency = on_runqueue
        self.timeslices_on_current_cpu = timeslices


class PidStatsParser:

    @staticmethod
    def extract(lines):
        if kind_systat_version == SYSTAT_VERSION_NEW:
            return PidStatsParser.extract_with_new_version(lines)
        else:
            return PidStatsParser.extract_with_old_version(lines)

    @staticmethod
    def extract_with_new_version(lines):
        stats_by_tid = {}
        for line in lines:
            values = re.split("(\s+)", line)
            # for debugging:
            # log_debug("Parsed values: " + "|".join(values))
            if len(values) >= 29 and values[6].isdigit():
                thread_id = int(values[6])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.cpu = values[18].rjust(2)
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.user_cpu = float(values[8])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.system_cpu = float(values[10])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.guest_cpu = float(values[12])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.wait_cpu = float(values[14])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.total_cpu = float(values[16])
                StatsProcessor.get_thread(thread_id).thread_stats.disk.kb_rd_per_sec = float(values[20])
                StatsProcessor.get_thread(thread_id).thread_stats.disk.kb_wr_per_sec = float(values[22])
                StatsProcessor.get_thread(thread_id).update_name(str(values[28]))
        return stats_by_tid

    @staticmethod
    def extract_with_old_version(lines):
        stats_by_tid = {}
        for line in lines:
            values = re.split("(\s+)", line)
            # for debugging:
            # log_debug("Parsed values: " + "|".join(values))
            if len(values) >= 27 and values[6].isdigit():
                thread_id = int(values[6])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.cpu = values[18].rjust(2)
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.user_cpu = float(values[8])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.system_cpu = float(values[10])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.guest_cpu = float(values[12])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.wait_cpu = 0.0
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.total_cpu = float(values[14])
                StatsProcessor.get_thread(thread_id).thread_stats.disk.kb_rd_per_sec = float(values[18])
                StatsProcessor.get_thread(thread_id).thread_stats.disk.kb_wr_per_sec = float(values[20])
                StatsProcessor.get_thread(thread_id).update_name(str(values[26]))
        return stats_by_tid


class StatsProcessor:
    threads = {}

    def __init__(self, params, stats_printer, stats_sorter, java_hotspot_handler):
        self.max_stack_depth = params.max_stack_depth
        self.top_num = params.top_num
        self.stats_printer = stats_printer
        self.stats_sorter = stats_sorter
        self.java_hotspot_handler = java_hotspot_handler

    @staticmethod
    def get_thread(tid):
        if tid not in StatsProcessor.threads:
            StatsProcessor.threads[tid] = ThreadInfo(tid)
        return StatsProcessor.threads[tid]

    @staticmethod
    def get_all_threads():
        return StatsProcessor.threads

    def process_stats(self, stat_lines, iter_num):
        PidStatsParser.extract(stat_lines)
        self.update_counters()
        top_n_threads = self.threads_for_sampling(self.top_num)
        self.load_stack_info(top_n_threads, self.max_stack_depth)
        self.stats_printer.display(top_n_threads, iter_num)

    @staticmethod
    def update_counters():
        for thread_info in StatsProcessor.get_all_threads().values():
            on_cpu, on_runqueue, timeslices = StatsProcessor.calculate_scheduler_stats(thread_info.tid)
            if on_cpu is not None and on_runqueue is not None and timeslices is not None:
                thread_info.thread_stats.scheduler_stats.update(on_cpu, on_runqueue, timeslices)

    def load_stack_info(self, thread_ids, max_stack_depth):
        thread_info_by_id = self.java_hotspot_handler.stack_info(thread_ids, max_stack_depth)
        for tid in thread_ids:
            thread_dump = thread_info_by_id.get(tid, {})
            name = thread_dump.get('name', None)
            if name is not None:
                StatsProcessor.get_thread(tid).update_name(name)
            dump = thread_dump.get('dump', 'no dump provided')
            StatsProcessor.get_thread(tid).update_dump(dump)

    def threads_for_sampling(self, top_num):
        t_sorted = sorted(StatsProcessor.threads.values(), key=self.stats_sorter, reverse=True)
        t_top = t_sorted if top_num < 0 else t_sorted[0:top_num]
        return [t.tid for t in t_top]

    @staticmethod
    def calculate_scheduler_stats(tid):
        try:
            with open("/proc/{}/task/{}/schedstat".format(pid, tid)) as schedule_stats:
                values = schedule_stats.read().split(" ")
                return int(values[0]), int(values[1]), int(values[2])
        except FileNotFoundError:
            return None, None, None


class JavaHotSpotHandler:

    def __init__(self, jstack_enabled):
        self.is_instrumented_java = self.check_is_instrumented_java()
        self.jstack_enabled = jstack_enabled

    @staticmethod
    def check_is_instrumented_java():
        result = False
        jps_exists = any(os.access(os.path.join(path, "jps"), os.X_OK) for path in os.environ["PATH"].split(os.pathsep))
        if jps_exists:
            # jps - q | grep - sq {pid}
            exit_code, data = subprocess.getstatusoutput("jps -q | grep -sq {}".format(str(pid)))
            # p1 = subprocess.Popen(["jps", "-q"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            # p2 = subprocess.Popen(["grep", "-s", "-q", str(pid)], stdin=p1.stdout, stderr=subprocess.STDOUT)
            # result = True if p2.returncode == 0 else False
            result = True if exit_code == 0 else False
        return result

    def stack_info(self, thread_ids, max_stack_depth):
        thread_by_tid = {}
        if self.jstack_enabled and self.is_instrumented_java:
            thread_set = set(thread_ids)
            out = subprocess.Popen(["jstack", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            stdout, stderr = out.communicate()
            thread_dumps = stdout.decode().split(os.linesep + os.linesep)
            for thread_dump in thread_dumps:
                if "tid=" in thread_dump:
                    tid_result = re.search("nid=(\w*)", thread_dump)
                    if tid_result is not None:
                        thread_id = int(tid_result.group(1), 16)
                        if thread_id in thread_set:
                            name_search = thread_dump.split('"')
                            name = name_search[1] if len(name_search) > 0 else "-name not found-"
                            dump = os.linesep.join(thread_dump.split(os.linesep)[0:(2 + max_stack_depth)])
                            thread_by_tid[thread_id] = {
                                'name': name,
                                'dump': dump,
                            }
        return thread_by_tid


class StatsRefreshPrinter:

    def __init__(self, title):
        self.stdscr = None
        self.title = title

    def __enter__(self):
        log_debug("Initializing StatsRefreshPrinter({})".format(self.title))
        self.stdscr = curses.initscr()
        curses.noecho()
        curses.cbreak()
        curses.start_color()
        curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_CYAN, curses.COLOR_BLACK)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        log_debug("Closing StatsRefreshPrinter({})".format(self.title))
        curses.echo()
        curses.nocbreak()
        curses.endwin()

    def display(self, top_n_threads, iter_num):
        stdscr = self.stdscr
        stdscr.scrollok(1)
        stdscr.idlok(1)

        max_lines, max_x = stdscr.getmaxyx()

        current_position = 2

        lines = []
        for tid in top_n_threads:
            thread_lines, current_position = StatsRefreshPrinter.next_line(current_position, max_lines,
                                                                           StatsProcessor.get_thread(tid))
            lines.extend(thread_lines)
            if current_position >= max_lines:
                break
        StatsRefreshPrinter.display_lines(self.stdscr, self.title, lines, iter_num=iter_num)
        stdscr.refresh()

    @staticmethod
    def display_lines(stdscr, header_text, lines, iter_num, encoding="utf-8"):
        try:
            rows, columns = stdscr.getmaxyx()
            stdscr.border()
            bottom_menu = "Iteration #{} | Press Ctrl + C to quit (pid {})".format(iter_num, script_pid).encode(
                encoding).center(columns - 4)
            stdscr.addstr(rows - 1, 2, bottom_menu, curses.A_REVERSE)
            out = stdscr.subwin(rows - 2, columns - 2, 1, 1)
            out_rows, out_columns = out.getmaxyx()
            out_rows -= 1
            stdscr.refresh()
            line = 0
            lines = StatsRefreshPrinter.prepare_lines(lines, out_columns)
            stdscr.addstr(0, 2, header_text, curses.A_REVERSE)
            for line in lines[line:line + out_rows]:
                for chunk in line:
                    out.addstr(chunk.text, chunk.attr)
            stdscr.refresh()
            out.refresh()
            return out
        except curses.error as exc:
            log_debug("Error raised by curses: {} (an error here is expected when the terminal resize)".format(exc))
            pass

    @staticmethod
    def prepare_lines(lines, max_columns):
        new_lines = list(map(lambda line: StatsRefreshPrinter.prepare_line(line, max_columns), lines))
        return reduce(lambda x, y: x + y, new_lines) if len(new_lines) > 1 else new_lines

    @staticmethod
    def prepare_line(line, max_columns):
        result_lines = []
        queue = deque(line)
        carry = 0
        new_line = []
        while queue:
            next_chunk = queue.popleft()
            # log_info("chunk: {}".format(next_chunk))
            if len(next_chunk.text) + carry > max_columns:
                new_line.append(ChunkText(next_chunk.text[0:max_columns - carry], next_chunk.attr))
                result_lines.append(new_line)
                carry = 0
                new_line = []
                queue.appendleft(ChunkText(next_chunk.text[max_columns - carry:], next_chunk.attr))
            else:
                new_line.append(next_chunk)
                carry += len(next_chunk.text)
        if len(new_line) > 0:
            new_line_len = reduce(lambda total_len, curr_len: total_len + curr_len,
                                  map(lambda chunk: len(chunk.text), new_line))
            left_space = max_columns - new_line_len
            if left_space > 0:
                new_line.append(ChunkText(" " * left_space))
            result_lines.append(new_line)
        return result_lines

    @staticmethod
    def next_line(position, max_lines, thread_info):
        """
        It returns an array that represent a list of lines.
        It a list with two level of depth for performance reason.

        1st level: line
        2nd level: chunk of text
        chunk level: ChunkText(text, attributes)

        Example:
          [
              [ChunkText("first chunk on first line", A_BOLD), ChunkText("second chunk on first line", 0)],
              [[ChunkText("second line", A_UNDERLINE)]]
          ]
        """

        lines = []

        if position >= max_lines:
            return lines, position

        lines.append([ChunkText("Thread [tid {} CPU #{}] \"{}\""
                                .format(thread_info.tid, thread_info.thread_stats.cpu.cpu, thread_info.name),
                                curses.A_BOLD)])

        position += 1
        if position >= max_lines:
            return lines, position

        new_line = []
        new_line.append(ChunkText("CPU "))
        new_line.append(ChunkText("{:3.2f}%".format(thread_info.thread_stats.cpu.total_cpu),
                                  StatsRefreshPrinter.cpu_color(thread_info.thread_stats.cpu.total_cpu)))
        new_line.append(ChunkText(" [%usr: "))
        new_line.append(ChunkText("{:3.2f}".format(thread_info.thread_stats.cpu.user_cpu),
                                  StatsRefreshPrinter.cpu_color(thread_info.thread_stats.cpu.user_cpu)))
        new_line.append(ChunkText(", %system: "))
        new_line.append(ChunkText("{:3.2f}".format(thread_info.thread_stats.cpu.system_cpu),
                                  StatsRefreshPrinter.cpu_color(thread_info.thread_stats.cpu.system_cpu)))
        new_line.append(ChunkText(", %guest: "))
        new_line.append(ChunkText("{:3.2f}".format(thread_info.thread_stats.cpu.guest_cpu),
                                  StatsRefreshPrinter.cpu_color(thread_info.thread_stats.cpu.guest_cpu)))
        new_line.append(ChunkText(", %wait: "))
        new_line.append(ChunkText("{:3.2f}".format(thread_info.thread_stats.cpu.wait_cpu),
                                  StatsRefreshPrinter.cpu_color(thread_info.thread_stats.cpu.wait_cpu)))
        new_line.append(ChunkText("] [spent in CPU: "))
        new_line.append(
            ChunkText("{}".format(
                StatsRefreshPrinter.nanos_fmt(thread_info.thread_stats.scheduler_stats.delta_spent_on_cpu))))
        new_line.append(ChunkText(", run-queue latency: "))
        new_line.append(
            ChunkText("{}".format(
                StatsRefreshPrinter.nanos_fmt(thread_info.thread_stats.scheduler_stats.delta_run_queue_latency)),
                StatsRefreshPrinter.latency_color(
                    thread_info.thread_stats.scheduler_stats.delta_run_queue_latency)))
        new_line.append(ChunkText(", # of timeslices run in current CPU: "))
        new_line.append(ChunkText("{}".format(thread_info.thread_stats.scheduler_stats.timeslices_on_current_cpu)))
        new_line.append(ChunkText("]"))

        lines.append(new_line)

        position += 1
        if position >= max_lines:
            return lines, position

        new_line = []
        new_line.append(ChunkText("I/O disk [kB_rd/s: "))
        new_line.append(ChunkText("{}".format(thread_info.thread_stats.disk.kb_rd_per_sec),
                                  StatsRefreshPrinter.io_color(thread_info.thread_stats.disk.kb_rd_per_sec)))
        new_line.append(ChunkText(", kB_wr/s: "))
        new_line.append(ChunkText("{}".format(thread_info.thread_stats.disk.kb_wr_per_sec),
                                  StatsRefreshPrinter.io_color(thread_info.thread_stats.disk.kb_wr_per_sec)))
        new_line.append(ChunkText("]"))
        lines.append(new_line)

        for line in thread_info.dump.split(os.linesep):
            lines.append([ChunkText(line)])
            position += 1
            if position >= max_lines:
                return lines, position

        lines.append([ChunkText("")])
        position += 1

        return lines, position

    @staticmethod
    def cpu_color(value):
        if value < 20:
            return curses.color_pair(1)
        elif value < 60:
            return curses.color_pair(3)
        else:
            return curses.color_pair(2)

    @staticmethod
    def latency_color(value):
        if value < 10000:  # 10 microseconds
            return curses.color_pair(1)
        elif value < 1000000:  # 1 millis
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


class ChunkText:

    def __init__(self, text, attr=0):
        self.text = text
        self.attr = attr


class BColors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class StatsTerminalPrinter:

    def __init__(self, title):
        self.title = title

    def display(self, top_n_threads, iter_num):
        print(StatsTerminalPrinter.colored('-------------------------- Iteration #{:5d}'.format(iter_num),
                                           BColors.HEADER))
        print(StatsTerminalPrinter.colored(self.title, BColors.HEADER))

        for tid in top_n_threads:
            self.next_line(StatsProcessor.get_thread(tid))

    def next_line(self, thread_info):
        print(
            StatsTerminalPrinter.colored(
                "Thread [tid {} CPU #{}] \"{}\""
                    .format(thread_info.tid, thread_info.thread_stats.cpu.cpu, thread_info.name), BColors.BOLD))

        print("CPU ", end='')
        print(StatsTerminalPrinter.colored("{:3.2f}%".format(thread_info.thread_stats.cpu.total_cpu),
                                           self.cpu_color(thread_info.thread_stats.cpu.total_cpu)), end='')
        print(" [%usr: ", end='')
        print(StatsTerminalPrinter.colored("{:3.2f}".format(thread_info.thread_stats.cpu.user_cpu),
                                           self.cpu_color(thread_info.thread_stats.cpu.user_cpu)), end='')
        print(", %system: ", end='')
        print(StatsTerminalPrinter.colored("{:3.2f}".format(thread_info.thread_stats.cpu.system_cpu),
                                           self.cpu_color(thread_info.thread_stats.cpu.system_cpu)), end='')
        print(", %guest: ", end='')
        print(StatsTerminalPrinter.colored("{:3.2f}".format(thread_info.thread_stats.cpu.guest_cpu),
                                           self.cpu_color(thread_info.thread_stats.cpu.guest_cpu)), end='')
        print(", %wait: ", end='')
        print(StatsTerminalPrinter.colored("{:3.2f}".format(thread_info.thread_stats.cpu.wait_cpu),
                                           self.cpu_color(thread_info.thread_stats.cpu.wait_cpu)), end='')
        print("] [spent in CPU: ", end='')
        print("{}".format(self.nanos_fmt(thread_info.thread_stats.scheduler_stats.delta_spent_on_cpu)), end='')
        print(", run-queue latency: ", end='')
        print(StatsTerminalPrinter.colored(
            "{}".format(self.nanos_fmt(thread_info.thread_stats.scheduler_stats.delta_run_queue_latency)),
            self.latency_color(thread_info.thread_stats.scheduler_stats.delta_run_queue_latency)), end='')
        print(", # of timeslices run in current CPU: ", end='')
        print("{}".format(thread_info.thread_stats.scheduler_stats.timeslices_on_current_cpu), end='')
        print("]")

        print("I/O disk [kB_rd/s: ", end='')
        print(StatsTerminalPrinter.colored("{}".format(thread_info.thread_stats.disk.kb_rd_per_sec),
                                           self.io_color(thread_info.thread_stats.disk.kb_rd_per_sec)), end='')
        print(", kB_wr/s: ", end='')
        print(StatsTerminalPrinter.colored("{}".format(thread_info.thread_stats.disk.kb_wr_per_sec),
                                           self.io_color(thread_info.thread_stats.disk.kb_wr_per_sec)), end='')
        print("]")

        for line in thread_info.dump.split(os.linesep):
            print(line)

        print('')
        return

    @staticmethod
    def colored(text, color):
        return "{}{}{}".format(color, text, BColors.ENDC)

    @staticmethod
    def cpu_color(value):
        if value < 20:
            return BColors.OKGREEN
        elif value < 60:
            return BColors.WARNING
        else:
            return BColors.FAIL

    @staticmethod
    def latency_color(value):
        if value < 10000:  # 10 microseconds
            return BColors.OKGREEN
        elif value < 1000000:  # 1 millis
            return BColors.WARNING
        else:
            return BColors.FAIL

    @staticmethod
    def io_color(value):
        if value < 20:
            return BColors.OKGREEN
        elif value < 100:
            return BColors.WARNING
        else:
            return BColors.FAIL

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
    main()

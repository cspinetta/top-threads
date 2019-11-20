#!/usr/bin/env python3
import os
import re
import sys

import subprocess
import curses
import traceback
from curses import wrapper


TITLE_ROW = "Generating thread stats for Java Process {}".format(sys.argv[1] if len(sys.argv) > 1 else "-")
log = []
pid = 0


def main():
    global pid
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
        call_pidstat(StatsProcessor(max_stack_depth, top_num, StatsPrinter(stdscr)))
    except Exception as e:
        exc = traceback.format_exc()
    finally:
        curses.echo()
        curses.nocbreak()
        curses.endwin()
        print("\n".join(log))
        if exc is not None:
            print(exc)


def log_info(msg):
    log.append(msg)


def call_pidstat(stats_processor):
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
                stats_processor.process_stats(lines)
            lines.clear()


class ThreadInfo:

    def __init__(self, tid, name="", dump="", thread_stats=None):
        self.tid = tid
        self.name = name
        self.dump = dump
        self.thread_stats = thread_stats if thread_stats is not None else ThreadStats(tid)

    def update_dump(self, name, dump):
        self.name = name
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
            log_info("TID: {}, on_runqueue {} -> {} (received: {})"
                     .format(self.tid,
                             StatsPrinter.nanos_fmt(self.run_queue_latency),
                             StatsPrinter.nanos_fmt(on_runqueue - self.run_queue_latency),
                             StatsPrinter.nanos_fmt(on_runqueue)))
        self.delta_spent_on_cpu = on_cpu - self.spent_on_cpu
        self.spent_on_cpu = on_cpu
        self.delta_run_queue_latency = on_runqueue - self.run_queue_latency
        self.run_queue_latency = on_runqueue
        self.timeslices_on_current_cpu = timeslices


class PidStatsParser:

    @staticmethod
    def extract(lines):
        stats_by_tid = {}
        for line in lines:
            values = re.split("(\s+)", line)
            # for debugging:
            # log_info("Parsed values: " + "|".join(values))
            if len(values) >= 18 and values[6].isdigit():
                thread_id = int(values[6])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.cpu = values[18].rjust(2)
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.user_cpu = float(values[8])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.system_cpu = float(values[10])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.guest_cpu = float(values[12])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.wait_cpu = float(values[14])
                StatsProcessor.get_thread(thread_id).thread_stats.cpu.total_cpu = float(values[16])
        return stats_by_tid


class StatsProcessor:

    threads = {}

    def __init__(self, max_stack_depth, top_num, stats_printer):
        self.max_stack_depth = max_stack_depth
        self.top_num = top_num
        self.stats_printer = stats_printer

    @staticmethod
    def get_thread(tid):
        if tid not in StatsProcessor.threads:
            StatsProcessor.threads[tid] = ThreadInfo(tid)
        return StatsProcessor.threads[tid]

    @staticmethod
    def get_all_threads():
        return StatsProcessor.threads

    def process_stats(self, stat_lines):
        PidStatsParser.extract(stat_lines)
        self.update_counters()
        top_n_threads = self.threads_for_sampling(self.top_num)
        self.load_stack_info(top_n_threads, self.max_stack_depth)
        self.stats_printer.display(top_n_threads)

    @staticmethod
    def update_counters():
        for thread_info in StatsProcessor.get_all_threads().values():
            on_cpu, on_runqueue, timeslices = StatsProcessor.calculate_scheduler_stats(thread_info.tid)
            if on_cpu is not None and on_runqueue is not None and timeslices is not None:
                thread_info.thread_stats.scheduler_stats.update(on_cpu, on_runqueue, timeslices)

    @staticmethod
    def load_stack_info(thread_ids, max_stack_depth):
        thread_info_by_id = StatsProcessor.stack_info(thread_ids, max_stack_depth)
        for tid in thread_ids:
            thread_dump = thread_info_by_id.get(tid, {})
            name = thread_dump.get('name', 'no name provided')
            dump = thread_dump.get('dump', 'no dump provided')
            StatsProcessor.get_thread(tid).update_dump(name, dump)

    @staticmethod
    def threads_for_sampling(top_num):
        t_sorted = sorted(StatsProcessor.threads.values(), key=lambda x: x.thread_stats.cpu.total_cpu, reverse=True)
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

    @staticmethod
    def stack_info(thread_ids, max_stack_depth):
        thread_set = set(thread_ids)
        thread_by_tid = {}
        out = subprocess.Popen(["jstack", pid], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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


class StatsPrinter:

    def __init__(self, stdscr):
        self.stdscr = stdscr

    def display(self, top_n_threads):
        stdscr = self.stdscr
        stdscr.scrollok(1)
        stdscr.idlok(1)
        stdscr.scroll(100)
        stdscr.addstr(0, 0, TITLE_ROW, curses.A_BOLD)

        stdscr.move(2, 0)

        max_y, max_x = stdscr.getmaxyx()
        max_lines = max_y - 10

        current_position = 2

        for tid in top_n_threads:
            current_position = self.next_line(current_position, max_lines, StatsProcessor.get_thread(tid))
            if current_position >= max_lines:
                break
        stdscr.refresh()

    def next_line(self, position, max_lines, thread_info):
        stdscr = self.stdscr

        if position >= max_lines:
            return position

        stdscr.addstr("Thread [tid {} CPU #{}] \"{}\""
                      .format(thread_info.tid, thread_info.thread_stats.cpu.cpu, thread_info.name),
                      curses.A_BOLD)
        stdscr.addstr(os.linesep)

        position += 1
        if position >= max_lines:
            return position

        stdscr.addstr("CPU ")
        stdscr.addstr("{:3.2f}%".format(thread_info.thread_stats.cpu.total_cpu),
                      self.cpu_color(thread_info.thread_stats.cpu.total_cpu))
        stdscr.addstr(" [%usr: ")
        stdscr.addstr("{:3.2f}".format(thread_info.thread_stats.cpu.user_cpu),
                      self.cpu_color(thread_info.thread_stats.cpu.user_cpu))
        stdscr.addstr(", %system: ")
        stdscr.addstr("{:3.2f}".format(thread_info.thread_stats.cpu.system_cpu),
                      self.cpu_color(thread_info.thread_stats.cpu.system_cpu))
        stdscr.addstr(", %guest: ")
        stdscr.addstr("{:3.2f}".format(thread_info.thread_stats.cpu.guest_cpu),
                      self.cpu_color(thread_info.thread_stats.cpu.guest_cpu))
        stdscr.addstr(", %wait: ")
        stdscr.addstr("{:3.2f}".format(thread_info.thread_stats.cpu.wait_cpu),
                      self.cpu_color(thread_info.thread_stats.cpu.wait_cpu))
        stdscr.addstr("] [spent in CPU: ")
        stdscr.addstr("{}".format(self.nanos_fmt(thread_info.thread_stats.scheduler_stats.delta_spent_on_cpu)))
        stdscr.addstr(", run-queue latency: ")
        stdscr.addstr("{}".format(self.nanos_fmt(thread_info.thread_stats.scheduler_stats.delta_run_queue_latency)),
                      self.latency_color(thread_info.thread_stats.scheduler_stats.delta_run_queue_latency))
        stdscr.addstr(", timeslices in current CPU: ")
        stdscr.addstr("{}".format(self.nanos_fmt(thread_info.thread_stats.scheduler_stats.timeslices_on_current_cpu)))
        stdscr.addstr("]")
        stdscr.addstr(os.linesep)

        position += 1
        if position >= max_lines:
            return position

        stdscr.addstr("I/O [kB_rd/s: ")
        stdscr.addstr("{}".format(thread_info.thread_stats.disk.kb_rd_per_sec),
                      self.io_color(thread_info.thread_stats.disk.kb_rd_per_sec))
        stdscr.addstr(", kB_wr/s: ")
        stdscr.addstr("{}".format(thread_info.thread_stats.disk.kb_wr_per_sec),
                      self.io_color(thread_info.thread_stats.disk.kb_wr_per_sec))
        stdscr.addstr("]")
        stdscr.addstr(os.linesep)

        for line in thread_info.dump.split(os.linesep):
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


if __name__ == '__main__':
    wrapper(main())

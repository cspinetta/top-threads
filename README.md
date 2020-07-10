# Top Threads

A tiny command line tool that provides a dynamic real-time view of the active threads for a given process with stats of CPU, disk and scheduling.
The view is similar to `top`, but the information comes from [pidstat] (provided by [systat]) and [/proc/<pid>/schedstat] (and from the [jstack] Oracle tool in case an attachable java process).

On each iteration, the following stats are displayed:

* CPU usage: _total_, _%usr_, _%system_, _%guest_ and _%wait_
* Disk usege: kB read per second and kB written per second
* Scheduler stats: time spent on the cpu, time spent waiting on a run queue (_runqueue latency_) and number of timeslices run on the current CPU.
* Java details: in case a the target is a Java process that can be attached with `jstack`, some extra details is showed such as thread name and stack traces.

### Requirements

* `Python 3`
* [systat]

It's can be used only on `linux` platform.

### Quick start

```bash
wget -O top_threads.py 'https://raw.githubusercontent.com/cspinetta/top-threads/master/top_threads.py' \
  && chmod +x top_threads.py
```

### Usage

```bash
usage: top_threads.py [-h] -p PID [-n [NUMBER]]
                      [--max-stack-depth [STACK_SIZE]]
                      [--sort [{cpu,rq,disk,disk-rd,disk-wr}]]
                      [--display [{terminal,refresh}]] [--no-jstack]

Process for analysing active Threads

optional arguments:
  -h, --help            show this help message and exit
  -p PID                Process ID
  -n [NUMBER]           Number of threads to show per sample
  --max-stack-depth [STACK_SIZE], -m [STACK_SIZE]
                        Max number of stack frames
  --sort [{cpu,rq,disk,disk-rd,disk-wr}], -s [{cpu,rq,disk,disk-rd,disk-wr}]
                        field used for sorting
  --display [{terminal,refresh}], -d [{terminal,refresh}]
                        Select the way to display the info: terminal or
                        refresh
  --no-jstack           Turn off usage of jstack to retrieve thread info like
                        name and stack

```

**Notes:**
* The first sample is with stats from the first execution of the process.
* `--display refresh` provides a view similar to `top` while `terminal` (the default) prints the data on the terminal like `pidstat`.

### Motivation

This tool comes from the need to want to see the time each thread spends in the runqueue waiting to be able to start running.
That is a really useful metric to understand if the process is being slow down because the CPU is saturated.
For this reason this tool emerged. It gets information from [pidstat], [/proc/<pid>/schedstat] and [jstack]:

* [pidstat] to get cpu and disk usage metrics from each thread in time interval.
* [/proc/<pid>/schedstat] to gets metrics from the runqueue. 
* [jstack] is used in case the process that is beaing monitored is an attachable java process, to obtain information such as thread name and stack traces.

### What is a good use case for this tool?

I often use this script when I have to analyze a performance problem at thread level and I want to inspect the dynamic usage of the cpu or the disk.

Some questions this tool help me to answer:

* Which thread is eating the entire CPU?
* How long are the threads waiting to take the CPU?
* What threads are using the disk right now?

The *run queue latency* is the metric I usually look at first because it's difficult to get from other traditional system tools and this script displays it at thread level.

The importance of this metric comes from the fact that the run queue latency is an excellent metric to identify CPU saturation.

In case you suspect you are being limited by a CPU saturation, you may look for a tool that help you to analyze the runqueue in more details. If you have root privileges you can try with BCC: [linux-bcc-runqlat](http://www.brendangregg.com/blog/2016-10-08/linux-bcc-runqlat.html)

### Example in pictures

* With `--display terminal` (the default):

![Top Java Threads in Terminal](docs/top_java_threads_terminal.png)

* With `--display refresh`:

![Top Java Threads Refresh](docs/top_java_threads_refresh.png)

[/proc/<pid>/schedstat]: https://www.kernel.org/doc/html/latest/scheduler/sched-stats.html#proc-pid-schedstat
[systat]: https://github.com/sysstat/sysstat
[pidstat]: https://linux.die.net/man/1/pidstat
[jstack]: https://docs.oracle.com/javase/9/tools/jstack.htm#JSWOR748

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日定时守护进程

用法：
    python3 daemon.py [-t HHMM] [-arg "ARGs"]

选项：
    -t HHMM      每天执行时间。省略时在 14:30 和 14:50 各执行一次；
                 指定后仅在指定时间执行一次
    -arg ARGs    传递给 main.py 的参数，默认
                 "analyze -sf input/big.txt -g boll,macd"

示例：
    python3 daemon.py
    python3 daemon.py -t 1450
    python3 daemon.py -t 1445 -arg "scan --random -g all --notify"
"""

import argparse
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
MAIN_PATH = os.path.join(PROJECT_ROOT, 'main.py')
DEFAULT_RUN_TIMES = ('1430', '1450')
DEFAULT_ARGS = 'analyze -sf input/big.txt -g boll,macd --notify'
POLL_SECONDS = 20


def _parse_run_time(value):
    value = str(value).strip()
    if len(value) != 4 or not value.isdigit():
        raise argparse.ArgumentTypeError('时间格式应为 HHMM，例如 1445')
    hour, minute = int(value[:2]), int(value[2:])
    if hour > 23 or minute > 59:
        raise argparse.ArgumentTypeError('时间超出范围，HH 为 00-23，MM 为 00-59')
    return value


def _log(message):
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] {message}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='每天定时调用 main.py')
    parser.add_argument('-t', dest='run_time', type=_parse_run_time,
                        default=None,
                        help='执行时间 HHMM；省略时在 14:30、14:50 各执行一次')
    parser.add_argument('-arg', dest='main_args', default=DEFAULT_ARGS,
                        help=f'传递给 main.py 的参数（默认 "{DEFAULT_ARGS}"）')
    args = parser.parse_args()

    os.chdir(PROJECT_ROOT)
    run_times = (args.run_time,) if args.run_time else DEFAULT_RUN_TIMES
    main_args = shlex.split(args.main_args)
    run_time_labels = '、'.join(f'{t[:2]}:{t[2:]}' for t in run_times)
    run_times_set = set(run_times)
    ran_date = None
    ran_today = set()
    _log(f'守护进程已启动，每天 {run_time_labels} 执行: '
         f'{sys.executable} main.py {" ".join(main_args)}'.rstrip())

    while True:
        now = datetime.now()
        if now.date() != ran_date:
            ran_date = now.date()
            ran_today = set()
        now_hhmm = now.strftime('%H%M')
        if now_hhmm in run_times_set and now_hhmm not in ran_today:
            cmd = [sys.executable, MAIN_PATH] + main_args
            _log(f'到达执行时间 {now_hhmm[:2]}:{now_hhmm[2:]}，开始运行: ' + ' '.join(cmd))
            try:
                proc = subprocess.run(cmd, cwd=PROJECT_ROOT)
                _log(f'运行完成，退出码={proc.returncode}')
            except Exception as exc:
                _log(f'运行异常: {exc}')
            ran_today.add(now_hhmm)
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()

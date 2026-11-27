#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日定时守护进程

用法：
    python3 daemon.py [-t HHMM[,HHMM...]] [-arg "ARGs"]

选项：
    -t HHMM[,HHMM...]
                 每天执行时间，多个用逗号分隔（如 0830,1430）；
                 省略时在 08:30 和 14:35 各执行一次；
                 周六、周日不执行
    -arg ARGs    传递给 analyze 的参数，默认
                 "analyze -sf input/pool.txt -g boma --maxdrawdown 5 --notify"
                 到达执行时间后，先运行 analyze，再运行
                 "scan -sf input/pool.txt -g boma --notify"

运行过程中，在终端输入 q 并回车即可退出守护进程（正在运行的子任务会被终止）。

示例：
    python3 daemon.py
    python3 daemon.py -t 0830,1430
    python3 daemon.py -t 1445 -arg "analyze -sf input/pool.txt -g all --notify"
"""

import argparse
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime


def _resolve_project_root():
    if not getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(__file__))
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for path in (os.getcwd(), exe_dir, os.path.dirname(exe_dir)):
        if path and os.path.isfile(os.path.join(path, 'squant.toml')):
            return path
    return exe_dir


PROJECT_ROOT = _resolve_project_root()
MAIN_PATH = os.path.join(PROJECT_ROOT, 'main.py')
DEFAULT_RUN_TIMES = ('0830', '1435')
DEFAULT_ARGS = ('analyze -sf input/pool.txt -g boma '
                '--maxdrawdown 5 --notify')
SCAN_ARGS = ('scan', '-sf', 'input/pool.txt', '-g', 'boma',
             '--notify')
POLL_SECONDS = 20


def _find_squant():
    """预编译 daemon 查找同目录/项目 bin 目录下的 squant。"""
    if not getattr(sys, 'frozen', False):
        return None
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for path in (os.path.join(exe_dir, 'squant'),
                 os.path.join(PROJECT_ROOT, 'bin', 'squant'),
                 os.path.join(PROJECT_ROOT, 'squant')):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _build_main_cmd(main_args):
    if getattr(sys, 'frozen', False):
        squant = _find_squant()
        if not squant:
            raise RuntimeError(
                '未找到 squant 可执行文件（应位于 daemon 同目录或项目 bin/ 目录）')
        return [squant] + main_args
    return [sys.executable, MAIN_PATH] + main_args


def _parse_run_time(value):
    value = str(value).strip()
    if len(value) != 4 or not value.isdigit():
        raise argparse.ArgumentTypeError('时间格式应为 HHMM，例如 1445')
    hour, minute = int(value[:2]), int(value[2:])
    if hour > 23 or minute > 59:
        raise argparse.ArgumentTypeError('时间超出范围，HH 为 00-23，MM 为 00-59')
    return value


def _parse_run_times(value):
    parts = [p.strip() for p in str(value).split(',') if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError('至少指定一个执行时间')
    times = [_parse_run_time(p) for p in parts]
    return tuple(dict.fromkeys(times))


def _log(message):
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] {message}', flush=True)


_QUIT = threading.Event()


def _start_quit_watcher():
    """后台读取 stdin，输入 q 并回车时设置退出事件。"""
    def _watch():
        try:
            while not _QUIT.is_set():
                line = sys.stdin.readline()
                if not line:
                    break
                if line.strip().lower() == 'q':
                    _log('收到退出指令 (q)，准备退出...')
                    _QUIT.set()
                    break
        except Exception:
            pass

    threading.Thread(target=_watch, name='quit-watcher', daemon=True).start()


def _terminate(proc, label):
    """终止正在运行的子进程（POSIX 下连同其进程组/子进程一起终止）。"""
    _log(f'正在终止 {label} (pid={proc.pid})...')

    def _signal(sig):
        if os.name == 'posix':
            os.killpg(proc.pid, sig)
        elif sig == signal.SIGTERM:
            proc.terminate()
        else:
            proc.kill()

    try:
        _signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _signal(signal.SIGKILL)
            proc.wait()
    except Exception:
        pass


def _run_cmd(cmd, label, now_hhmm):
    """运行子命令，期间持续检查退出事件；收到 q 时终止子进程并返回。"""
    _log(f'到达执行时间 {now_hhmm[:2]}:{now_hhmm[2:]}，开始运行 {label}: '
         + ' '.join(cmd))
    try:
        proc = subprocess.Popen(
            cmd, cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL,
            start_new_session=(os.name == 'posix'))
    except Exception as exc:
        _log(f'{label} 启动异常: {exc}')
        return
    try:
        while proc.poll() is None:
            if _QUIT.is_set():
                _terminate(proc, label)
                return
            time.sleep(0.2)
    except KeyboardInterrupt:
        _terminate(proc, label)
        raise
    _log(f'{label} 完成，退出码={proc.returncode}')


def main():
    parser = argparse.ArgumentParser(description='每天定时调用 main.py')
    parser.add_argument('-t', dest='run_time', type=_parse_run_times,
                        default=None,
                        help='执行时间 HHMM，多个用逗号分隔（如 0830,1430）；'
                             '省略时在 08:30、14:35 各执行一次')
    parser.add_argument('-arg', dest='main_args', default=DEFAULT_ARGS,
                        help=f'传递给 analyze 的参数（默认 "{DEFAULT_ARGS}"）')
    args = parser.parse_args()

    os.chdir(PROJECT_ROOT)
    run_times = args.run_time if args.run_time else DEFAULT_RUN_TIMES
    main_args = shlex.split(args.main_args)
    try:
        base_cmd = _build_main_cmd([])
    except Exception as exc:
        _log(f'无法启动: {exc}')
        return
    analyze_cmd = base_cmd + main_args
    scan_cmd = base_cmd + list(SCAN_ARGS)
    run_time_labels = '、'.join(f'{t[:2]}:{t[2:]}' for t in run_times)
    run_times_set = set(run_times)
    ran_date = None
    ran_today = set()
    _log(f'守护进程已启动，每天 {run_time_labels} 执行:')
    _log(f'  1) {" ".join(analyze_cmd)}')
    _log(f'  2) {" ".join(scan_cmd)}')
    _log('提示: 在终端输入 q 并回车可退出守护进程')

    _start_quit_watcher()

    try:
        while not _QUIT.is_set():
            now = datetime.now()
            if now.date() != ran_date:
                ran_date = now.date()
                ran_today = set()
            if now.weekday() >= 5:
                _QUIT.wait(POLL_SECONDS)
                continue
            now_hhmm = now.strftime('%H%M')
            if now_hhmm in run_times_set and now_hhmm not in ran_today:
                for label, cmd in (('analyze', analyze_cmd), ('scan', scan_cmd)):
                    if _QUIT.is_set():
                        break
                    _run_cmd(cmd, label, now_hhmm)
                ran_today.add(now_hhmm)
            _QUIT.wait(POLL_SECONDS)
    except KeyboardInterrupt:
        _QUIT.set()
        _log('收到中断 (Ctrl+C)，准备退出...')

    _log('守护进程已停止')


if __name__ == '__main__':
    main()

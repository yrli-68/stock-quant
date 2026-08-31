#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock Quant 日常监控服务

每日在指定时间（默认 15:00）运行一次分析，对股票池运行全部策略并发送钉钉通知
（--notify）。循环执行：--plan-time 会阻塞至当天/次日的设定时刻，从而形成
"每天固定时间运行一次"的节奏。

可通过环境变量覆盖：
    SQ_RUN_TIME   每日执行时刻（hhmm，默认 1500）
    SQ_STRATEGY   -g 策略（默认 all）
    SQ_WATCHLIST  股票池文件（默认 input/favs.txt）
"""
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_TIME = os.environ.get('SQ_RUN_TIME', '1500')
STRATEGY = os.environ.get('SQ_STRATEGY', 'all')
WATCHLIST = os.environ.get('SQ_WATCHLIST', os.path.join(PROJECT_ROOT, 'input', 'favs.txt'))


def main():
    os.chdir(PROJECT_ROOT)
    while True:
        try:
            cmd = [sys.executable, 'main.py', 'analyze',
                   '-sf', WATCHLIST, '-g', STRATEGY,
                   '--notify', '--plan-time', RUN_TIME]
            print(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] 运行: {" ".join(cmd)}', flush=True)
            proc = subprocess.run(cmd, cwd=PROJECT_ROOT)
            print(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] 本轮完成，退出码={proc.returncode}', flush=True)
        except Exception as e:
            print(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] 运行异常: {e}', flush=True)
        time.sleep(1800)  # 安全缓冲，避免异常导致的忙循环


if __name__ == '__main__':
    main()

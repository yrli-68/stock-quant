#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock Quant 日常监控服务

每日在指定时刻（默认 14:50）后运行一次分析，对股票池运行全部策略并发送钉钉通知
（--notify）。

执行机制：不再向 cmd 传 --plan-time，而是在本服务的 main() 循环内检查时间——
当当前时刻已超过 SQ_RUN_TIME（hhmm）时执行一次 cmd；通过 last_run_date 保证
每天只执行一次（当天已执行过则不再重复，直到次日再超过 SQ_RUN_TIME 才执行）。

可通过环境变量覆盖：
    SQ_RUN_TIME   每日执行时刻（hhmm，默认 1450）
    SQ_STRATEGY   -g 策略（默认 all）
    SQ_WATCHLIST  股票池文件（默认 input/favs.txt）
"""
import os
import subprocess
import sys
import time
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.config import load_config

SERVICE_CFG = load_config().get('service', {})
RUN_TIME = os.environ.get('SQ_RUN_TIME', SERVICE_CFG.get('SQ_RUN_TIME', '1445'))
STRATEGY = os.environ.get('SQ_STRATEGY', SERVICE_CFG.get('SQ_STRATEGY', 'boll,macd'))
WATCHLIST = os.environ.get('SQ_WATCHLIST', os.path.join(PROJECT_ROOT, 'input', 'big.txt'))
POLL_SECONDS = 60  # 时间检查间隔（秒）


def _time_exceeded(run_time):
    """当前时刻是否已超过 run_time（hhmm），如 '1450' 表示 14:50 之后"""
    run_time = str(run_time).strip()
    try:
        hour, minute = int(run_time[:2]), int(run_time[2:4])
    except (ValueError, IndexError):
        print(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] SQ_RUN_TIME 格式错误: {run_time}，期望 hhmm', flush=True)
        return False
    return (datetime.now().hour, datetime.now().minute) >= (hour, minute)


def main():
    os.chdir(PROJECT_ROOT)
    last_run_date = None
    while True:
        try:
            now = datetime.now()
            if _time_exceeded(RUN_TIME) and last_run_date != now.date():
                #cmd = [sys.executable, 'main.py', 'scan',
                #       '--random', '-g', STRATEGY, '--notify']
                #print(f'[{now.strftime("%Y-%m-%d %H:%M:%S")}] 已超过 {RUN_TIME}，运行: {" ".join(cmd)}', flush=True)
                #proc = subprocess.run(cmd, cwd=PROJECT_ROOT)

                cmd = [sys.executable, 'main.py', 'analyze',
                       '-sf', WATCHLIST, '-g', STRATEGY, '--notify']
                print(f'[{now.strftime("%Y-%m-%d %H:%M:%S")}] 已超过 {RUN_TIME}，运行: {" ".join(cmd)}', flush=True)
                proc = subprocess.run(cmd, cwd=PROJECT_ROOT)

                print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] 本轮完成，退出码={proc.returncode}', flush=True)
                last_run_date = now.date()
        except Exception as e:
            print(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] 运行异常: {e}', flush=True)
        time.sleep(POLL_SECONDS)  # 周期检查时间


if __name__ == '__main__':
    main()

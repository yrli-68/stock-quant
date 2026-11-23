#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证单持仓回测的大盘下跌控制（--trendctl）。

对比 --trendctl 1（下跌禁止买入，默认）与 --trendctl 0（不控制）的回测结果：
    backtest -sf input/pool.txt -st <start> -e <end> -g boll,macd --trendctl 1
    backtest -sf input/pool.txt -st <start> -e <end> -g boll,macd --trendctl 0

测试周期：2020、2021、2022、2023、2024、2025、2026。
"""

import contextlib
import io
import os
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import pandas as pd

import main

POOL_PATH = os.path.join(_ROOT, 'input', 'pool.txt')
STRATEGIES = ['boll', 'macd']
STRATEGY_ENHANCE = {'boll': 0, 'macd': 0}
CAPITAL = 1_000_000
YEARS = range(2020, 2027)


def _read_pool():
    symbols = main._read_symbols_file(POOL_PATH)
    if not symbols:
        raise RuntimeError(f'股票池为空或不存在: {POOL_PATH}')
    return symbols


def _year_range(year):
    start = f'{year}-01-01'
    end = f'{year}-12-31'
    today = datetime.now().strftime('%Y-%m-%d')
    end = min(end, today)
    return start, end


def _run_case(symbols, start_date, end_date, label, trendctl=1):
    main._set_seqnum(0)
    main._set_fix_capital(False)
    main._set_trendctl(trendctl)

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        result = main._run_onestock_backtest(
            symbols,
            start_date,
            end_date,
            STRATEGIES,
            STRATEGY_ENHANCE,
            CAPITAL,
            './output',
            random_mode=False,
            random_count=100,
            fix_capital=False,
            file_part=label,
            replace_on_buy=0,
        )

    if result is None:
        raise RuntimeError(f'{label} 回测失败:\n{stdout.getvalue()}')
    return result['result']


def _pct(v):
    return f'{v * 100:.2f}%' if v is not None else 'N/A'


def main_test():
    symbols = _read_pool()
    print(f'股票池: {len(symbols)} 只')
    print(f'策略: {", ".join(STRATEGIES)}')
    print()

    rows = []
    for year in YEARS:
        start, end = _year_range(year)
        print(f'正在回测 {year}: {start} ~ {end} ...')
        blocked = _run_case(symbols, start, end, f'trendctl1_{year}', trendctl=1)
        free = _run_case(symbols, start, end, f'trendctl0_{year}', trendctl=0)
        rows.append((year, start, end, blocked, free))

    print()
    print('=' * 78)
    print('单持仓回测（trendctl=1 下跌禁买 vs trendctl=0 不控制）')
    print('=' * 78)
    print(f'{"年份":<6} {"区间":<24} {"N=1收益":>12} {"N=0收益":>12} {"N=1交易":>8} {"N=0交易":>8}')
    print('-' * 78)
    for year, start, end, blocked, free in rows:
        print(f'{year:<6} {start} ~ {end:<10} '
              f'{_pct(blocked.get("total_return")):>12} '
              f'{_pct(free.get("total_return")):>12} '
              f'{str(blocked.get("total_trades")):>8} '
              f'{str(free.get("total_trades")):>8}')
    print('=' * 78)

    return rows


if __name__ == '__main__':
    main_test()

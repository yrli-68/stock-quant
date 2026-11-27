#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOMA 分时段回测统计。

用法：
    python test/test_boma_periods.py
    python test/test_boma_periods.py -sf input/favs.txt -g boma
    python test/test_boma_periods.py -sf input/pool.txt -g boma,macd

等价 CLI 参数：
    backtest -g <strategy> -sf <file> --maxdrawdown 2 \
             --verbose --fixcapital -st <start> -e <end>

统计：
- 总收益率、最大回撤、交易次数、胜率、盈利因子；
- 买入原因分类：次数、平均单笔收益率、胜率、总盈亏；
- 卖出原因分类：次数、平均单笔收益率、胜率、总盈亏。
"""

import argparse
import contextlib
import io
import os
import sys
from collections import defaultdict
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import pandas as pd

import main

DEFAULT_POOL_PATH = os.path.join(_ROOT, 'input', 'pool.txt')
CAPITAL = 1_000_000
PERIODS = [
    ('2020~2022', '2020-01-01', '2022-12-31'),
    ('2023~2024', '2023-01-01', '2024-12-31'),
    ('2025~2026', '2025-01-01', datetime.now().strftime('%Y-%m-%d')),
]


def _summary(trades, reason_key):
    groups = defaultdict(list)
    for t in trades:
        groups[t.get(reason_key) or '—'].append(t)
    rows = []
    total = len(trades)
    for reason, items in groups.items():
        rets = [float(t.get('return') or 0.0) for t in items]
        pnl = sum((float(t['exit_price']) - float(t['entry_price']))
                  * float(t['position']) for t in items)
        wins = sum(1 for r in rets if r > 0)
        rows.append({
            'reason': reason,
            'count': len(items),
            'pct': len(items) / total * 100 if total else 0.0,
            'avg_return': sum(rets) / len(rets) if rets else 0.0,
            'win_rate': wins / len(rets) * 100 if rets else 0.0,
            'pnl': pnl,
        })
    rows.sort(key=lambda x: (-x['count'], x['reason']))
    return rows


def _print_analysis(label, result, trades):
    ret = result.get('total_return')
    dd = result.get('max_drawdown')
    wr = result.get('win_rate')
    pf = result.get('profit_factor')
    print(f'\n{"=" * 96}')
    print(f'{label}  总收益率: {ret * 100:.2f}%  最大回撤: {dd * 100:.2f}%  '
          f'交易: {len(trades)}  胜率: {wr * 100:.2f}%  盈利因子: {pf:.2f}')
    print(f'{"=" * 96}')

    for title, key in [('买入原因分类', 'entry_reason_detail'),
                       ('卖出原因分类', 'exit_reason_detail')]:
        print(f'\n{title}:')
        rows = _summary(trades, key)
        print(f'{"原因":<22} {"次数":>6} {"占比":>8} {"平均收益率":>12} '
              f'{"胜率":>8} {"总盈亏":>14}')
        print('-' * 78)
        for r in rows:
            print(f'{r["reason"]:<22} {r["count"]:>6d} {r["pct"]:>7.1f}% '
                  f'{r["avg_return"] * 100:>11.2f}% {r["win_rate"]:>7.1f}% '
                  f'{r["pnl"]:>14,.0f}')


def _parse_args():
    parser = argparse.ArgumentParser(description='BOMA 分时段回测统计')
    parser.add_argument('-sf', '--symbol-file', default=DEFAULT_POOL_PATH,
                        help='股票池文件（默认 input/pool.txt）')
    parser.add_argument('-g', '--strategy', default='boma',
                        help='策略，多个用逗号分隔（默认 boma）')
    return parser.parse_args()


def _build_strategies(strategy_arg):
    specs = main._split_strategies(strategy_arg)
    keys = []
    enhance = {}
    for spec in specs:
        base, _ = main._parse_strategy_spec(spec)
        if base == 'all':
            for key in main.ALL_SELECTION:
                if key not in keys:
                    keys.append(key)
                    enhance[key] = 0
            continue
        if base not in main.STRATEGY_MAP:
            raise SystemExit(
                f'未知策略: {base}，可选: {", ".join(main.STRATEGY_MAP.keys())}')
        if base not in keys:
            keys.append(base)
            enhance[base] = 0
    if not keys:
        raise SystemExit('至少需要一个策略')
    return keys, enhance


def main_test():
    args = _parse_args()
    pool_path = args.symbol_file
    strat_keys, strat_enhance = _build_strategies(args.strategy)
    symbols = main._read_symbols_file(pool_path)
    if not symbols:
        raise RuntimeError(f'股票池为空或不存在: {pool_path}')

    main.set_primary_source('auto')
    main._set_max_hold_days(None)
    main._set_fix_capital(True)
    main._set_maxdrawdown(2.0)
    main._ensure_symbol_cap_map(symbols)

    strat_tag = '_'.join(strat_keys)
    print(f'股票池: {pool_path}（{len(symbols)} 只）')
    print(f'策略: {", ".join(main._strat_label(k) for k in strat_keys)}')
    print('参数: --maxdrawdown 2 --verbose --fixcapital')

    for label, start, end in PERIODS:
        print(f'\n正在回测 {label}: {start} ~ {end} ...')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = main._run_onestock_backtest(
                symbols, start, end, strat_keys, strat_enhance,
                CAPITAL, './output', fix_capital=True,
                file_part=f'{strat_tag}_{start[:4]}_{end[:4]}',
                verbose=True,
            )
        if res is None:
            print(f'{label} 回测失败:\n{buf.getvalue()}')
            continue
        _print_analysis(label, res['result'], res['trades'])
        if res.get('chart_path'):
            print(f'\n权益图表: {res["chart_path"]}')
        if res.get('html_path'):
            print(f'HTML报告: {res["html_path"]}')


if __name__ == '__main__':
    main_test()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""INDEX_CHECK 年度回测对比测试

15 个用例 = 5 个年度（2021~2025）× 3 个 INDEX_CHECK 取值（0/1/2）。
每个用例运行 -g boll,macd，并追加由两者合成的「复合策略」，
股票池来自 input/random1.txt，数据源 eastmoney。

最终输出：
    - report/index_check_yearly_compare_<mmddHHMM>.html
    - report/index_check_yearly_compare_<mmddHHMM>.csv

年度收益率为股票等权平均总收益率；5 年累计收益率为年度收益率复利累乘；
年化总收益率 = (1 + 5年累计收益率) ** (1/5) - 1。
"""

import argparse
import csv
import html
import math
import os
import re
import sys
from datetime import datetime

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import core.thresholds as thresholds
import main as sq
from _version import __version__
from core.data_fetcher import DataFetcher, set_primary_source
from core.indicators import add_all_indicators

YEARS = [2021, 2022, 2023, 2024, 2025]
MODES = [0, 1, 2]
BASE_STRATEGIES = ['boll', 'macd']
STRATEGIES = BASE_STRATEGIES + ['combo']
def _read_symbols(path):
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    symbols = []
    seen = set()
    for s in re.split(r'[\s,;|]+', text):
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            symbols.append(s)
    return symbols


def _pct(v):
    return 'N/A' if v is None else f'{v * 100:.2f}%'


def _mode_label(mode):
    return {0: '0（关闭）', 1: '1（指数MA5）', 2: '2（指数或个股MA5）'}.get(mode, str(mode))


def _run_cases(symbols, capital, limit=None):
    if limit:
        symbols = symbols[:limit]
    set_primary_source('eastmoney')
    thresholds.load_thresholds(force=True)
    cache = thresholds._cache
    fetcher = DataFetcher()

    full_data = {}
    for i, sym in enumerate(symbols, 1):
        try:
            df = fetcher.get_stock_data(sym, '2020-09-01', '2025-12-31')
        except Exception as exc:
            print(f'  [{i}/{len(symbols)}] {sym} 获取失败: {exc}')
            continue
        if df is None or len(df) < 60:
            print(f'  [{i}/{len(symbols)}] {sym} 数据不足，跳过')
            continue
        full_data[sym] = df
        print(f'  [{i}/{len(symbols)}] {sym} 数据 {len(df)} 条')

    if not full_data:
        raise RuntimeError('没有可用股票数据')

    print(f'\n有效股票: {len(full_data)} 只')

    year_returns = {}
    year_counts = {}
    for year in YEARS:
        start = f'{year}-01-01'
        end = f'{year}-12-31'
        warm = (pd.Timestamp(start) - pd.Timedelta(days=90)).strftime('%Y-%m-%d')
        frames = {}
        for sym, df in full_data.items():
            sub = df[(df.index >= warm) & (df.index <= end)].copy()
            if len(sub) < 60:
                continue
            sub = add_all_indicators(sub)
            sub = sub[sub.index >= pd.Timestamp(start)]
            if len(sub) < 20:
                continue
            sub.attrs['symbol'] = sym
            frames[sym] = sub

        for mode in MODES:
            cache['INDEX_CHECK'] = float(mode)
            print(f'\n  用例 {year} / INDEX_CHECK={mode}  ({len(frames)} 只股票)')
            rets = {sk: [] for sk in STRATEGIES}
            for sym, sub in frames.items():
                sig_map = {}
                for sk in BASE_STRATEGIES:
                    try:
                        result = sq._run_strategy(sk, sub, capital=capital)
                    except Exception as exc:
                        print(f'    {sym} {sk} 回测失败: {exc}')
                        continue
                    sig_map[sk] = result.get('signals')
                    tr = result.get('total_return')
                    if tr is not None:
                        rets[sk].append(float(tr))
                try:
                    combo_sig = sq._build_combo_signals(sub, sig_map)
                    if combo_sig is not None:
                        combo_res = sq._run_signals_backtest(
                            sub, combo_sig, capital, sq._COMBO_TOKEN, sq._COMBO_NAME)
                        tr = combo_res.get('total_return')
                        if tr is not None:
                            rets['combo'].append(float(tr))
                except Exception as exc:
                    print(f'    {sym} 复合策略回测失败: {exc}')
            for sk in STRATEGIES:
                year_returns[(sk, mode, year)] = (
                    sum(rets[sk]) / len(rets[sk]) if rets[sk] else None)
                year_counts[(sk, mode, year)] = len(rets[sk])
                print(f'    {sq._strat_label(sk):9s} 样本={len(rets[sk]):3d} '
                      f'平均总收益率={_pct(year_returns[(sk, mode, year)])}')

    groups = {}
    for sk in STRATEGIES:
        for mode in MODES:
            yearly = [year_returns.get((sk, mode, y)) for y in YEARS]
            valid = [v for v in yearly if v is not None]
            if len(valid) == len(YEARS):
                cum = math.prod(1.0 + v for v in valid) - 1.0
                annual = (1.0 + cum) ** (1.0 / len(YEARS)) - 1.0
            else:
                cum = None
                annual = None
            counts = [year_counts.get((sk, mode, y), 0) for y in YEARS]
            groups[(sk, mode)] = {
                'strategy': sq._strat_label(sk),
                'strategy_key': sk,
                'mode': mode,
                'yearly': yearly,
                'sum': sum(valid) if valid else None,
                'cum': cum,
                'annual': annual,
                'counts': counts,
            }
    return groups


def _write_csv(groups, path):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['策略', 'INDEX_CHECK'] + [str(y) for y in YEARS] +
                        ['年度收益率合计', '5年累计总收益率', '年化总收益率', '年度样本数'])
        for g in sorted(groups.values(), key=lambda x: (x['cum'] is None, -(x['cum'] or 0))):
            writer.writerow([
                g['strategy'], g['mode'],
                *[_pct(v) for v in g['yearly']],
                _pct(g['sum']), _pct(g['cum']), _pct(g['annual']),
                '/'.join(str(c) for c in g['counts']),
            ])


def _table_html(rows, sort_label):
    headers = ['排名', '策略', 'INDEX_CHECK'] + [str(y) for y in YEARS] + [
        '年度收益率合计', '5年累计总收益率', '年化总收益率', '年度样本数']
    head = ''.join(f'<th>{html.escape(h)}</th>' for h in headers)
    body = []
    for i, g in enumerate(rows, 1):
        cells = [str(i), g['strategy'], _mode_label(g['mode'])]
        cells += [_pct(v) for v in g['yearly']]
        cells += [_pct(g['sum']), _pct(g['cum']), _pct(g['annual']),
                  '/'.join(str(c) for c in g['counts'])]
        body.append('<tr>' + ''.join(f'<td>{html.escape(c)}</td>' for c in cells) + '</tr>')
    return (f'<h2>{html.escape(sort_label)}</h2>'
            f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>')


def _write_html(groups, path, symbol_count, case_options):
    by_cum = sorted(groups.values(),
                    key=lambda x: (x['cum'] is None, -(x['cum'] if x['cum'] is not None else 0)))
    by_annual = sorted(groups.values(),
                       key=lambda x: (x['annual'] is None,
                                      -(x['annual'] if x['annual'] is not None else 0)))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    html_text = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>INDEX_CHECK 年度回测对比报告</title>
<style>
body {{ font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; max-width: 1400px; margin: 0 auto; padding: 20px; background: #fafafa; color: #2c3e50; }}
h1 {{ border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
h2 {{ border-bottom: 2px solid #bdc3c7; padding-bottom: 6px; margin-top: 30px; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 13px; }}
th, td {{ border: 1px solid #ddd; padding: 7px 9px; text-align: center; }}
th {{ background: #3498db; color: white; }}
tr:nth-child(even) {{ background: #f2f2f2; }}
.info {{ background: #ecf0f1; padding: 15px; border-radius: 5px; margin: 15px 0; line-height: 1.8; }}
.note {{ color: #7f8c8d; font-size: 13px; }}
</style></head><body>
<h1>INDEX_CHECK 年度回测对比报告</h1>
<div class="info">
<span><strong>用例数:</strong> {len(YEARS) * len(MODES)}（5 年度 × 3 个 INDEX_CHECK）</span><br>
<span><strong>命令选项:</strong> {html.escape(case_options)}</span><br>
<span><strong>软件版本:</strong> {__version__}</span><br>
<span><strong>股票数:</strong> {symbol_count}</span><br>
<span><strong>生成时间:</strong> {now}</span>
</div>
<p class="note">年度收益率为当年股票等权平均总收益率；年度收益率合计为 5 年算术和；
5 年累计总收益率为年度收益率的复利累乘；年化总收益率 = (1 + 5年累计总收益率)<sup>1/5</sup> - 1。<br>
「复合策略」由 BOLL 与 MACD 的买入信号合成：任一策略买入即持有，直到被记录策略出现卖出。</p>
{_table_html(by_cum, '按 5 年累计总收益率排序')}
{_table_html(by_annual, '按年化总收益率排序')}
</body></html>'''
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html_text)


def _print_report(groups):
    by_cum = sorted(groups.values(),
                    key=lambda x: (x['cum'] is None, -(x['cum'] if x['cum'] is not None else 0)))
    by_annual = sorted(groups.values(),
                       key=lambda x: (x['annual'] is None,
                                      -(x['annual'] if x['annual'] is not None else 0)))
    print('\n按 5 年累计总收益率排序')
    print('=' * 110)
    print(f'{"策略":<10}{"INDEX_CHECK":<14}{"5年累计":>10}{"年化":>10}  年度收益率')
    for g in by_cum:
        yearly = '  '.join(_pct(v) for v in g['yearly'])
        print(f'{g["strategy"]:<10}{_mode_label(g["mode"]):<14}'
              f'{_pct(g["cum"]):>10}{_pct(g["annual"]):>10}  {yearly}')
    print('\n按年化总收益率排序')
    print('=' * 110)
    print(f'{"策略":<10}{"INDEX_CHECK":<14}{"年化":>10}{"5年累计":>10}')
    for g in by_annual:
        print(f'{g["strategy"]:<10}{_mode_label(g["mode"]):<14}'
              f'{_pct(g["annual"]):>10}{_pct(g["cum"]):>10}')


def main():
    os.chdir(ROOT)
    parser = argparse.ArgumentParser(description='INDEX_CHECK 年度回测对比测试')
    parser.add_argument('--symbol-file', default='input/random1.txt')
    parser.add_argument('--capital', type=float, default=100, help='初始资金（万元）')
    parser.add_argument('--limit', type=int, default=None, help='仅用于冒烟测试，限制股票数')
    parser.add_argument('--output-dir', default='report')
    args = parser.parse_args()

    symbols = _read_symbols(args.symbol_file)
    if not symbols:
        raise SystemExit(f'股票列表为空: {args.symbol_file}')
    print(f'股票列表: {args.symbol_file}（{len(symbols)} 只）')
    groups = _run_cases(symbols, args.capital * 10000, limit=args.limit)

    os.makedirs(args.output_dir, exist_ok=True)
    tag = datetime.now().strftime('%m%d%H%M')
    file_stem = os.path.splitext(os.path.basename(args.symbol_file))[0]
    base = os.path.join(args.output_dir, f'index_check_yearly_compare_{file_stem}_{tag}')
    case_options = f'-g boll,macd -sf {args.symbol_file} --src eastmoney'
    _write_csv(groups, base + '.csv')
    _write_html(groups, base + '.html', args.limit or len(symbols), case_options)
    _print_report(groups)
    print(f'\nHTML报告: {base}.html')
    print(f'CSV报告:  {base}.csv')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比 --maxholddays N 与不限制持股天数的单持仓回测效果。

用法：
    python3 test/test_maxholddays_onestock.py          # 默认股票池/区间/N=30
    python3 test/test_maxholddays_onestock.py -d 20    # 指定 N=20
    python3 test/test_maxholddays_onestock.py \\
        -sf input/pool.txt -st 20200101 -e 20251231 -d 30

等价命令：
    backtest -sf <file> -st <start> -e <end> -g boll,macd
    backtest -sf <file> -st <start> -e <end> -g boll,macd --maxholddays N

说明：
    - maxholddays 条件：持仓超过 N 个交易日、收盘价低于 BOLL 中线且回撤 >10% 才卖出；
    - 直接调用 backtest 的单持仓轮动回测函数，避免解析控制台表格；
    - 截获 _print_table 两次输出：第一张是策略分类统计，第二张是交易明细；
    - 需要可用的 MySQL/网络数据源，首次运行会补齐历史行情。
"""

import argparse
import contextlib
from datetime import datetime
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import main


START_DATE = '2020-01-01'
END_DATE = '2025-12-31'
STRATEGIES = ['boll', 'macd']
STRATEGY_ENHANCE = {'boll': 0, 'macd': 0}
CAPITAL = 1_000_000
POOL_PATH = os.path.join(_ROOT, 'input', 'pool.txt')

METRIC_NAMES = [
    '总收益率', '年化收益率', '最大回撤', '夏普比率', '交易次数',
    '胜率', '盈利因子', '持股天数', '总盈亏额',
]


def _positive_int(value):
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f'无效天数: {value}')
    if days < 1:
        raise argparse.ArgumentTypeError('天数必须 >= 1')
    return days


def _normalize_date(value):
    text = str(value).strip().replace('/', '-')
    for fmt in ('%Y%m%d', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f'无效日期: {value}（支持 YYYYMMDD 或 YYYY-MM-DD）')


def _parse_args():
    parser = argparse.ArgumentParser(
        description='对比 --maxholddays N 与不限制持股天数的 onestock 回测效果')
    parser.add_argument(
        '-sf', '--symbol-file', dest='symbol_file', default=POOL_PATH,
        help='股票池文件（默认 input/pool.txt）')
    parser.add_argument(
        '-st', '--start', dest='start_date', type=_normalize_date,
        default=START_DATE, help=f'开始日期（默认 {START_DATE}）')
    parser.add_argument(
        '-e', '--end', dest='end_date', type=_normalize_date,
        default=END_DATE, help=f'结束日期（默认 {END_DATE}）')
    parser.add_argument(
        '-d', '--days', type=_positive_int, default=30,
        help='maxholddays 天数（默认 30）')
    return parser.parse_args()


def _read_pool(path):
    symbols = main._read_symbols_file(path)
    if not symbols:
        raise RuntimeError(f'股票池为空或不存在: {path}')
    return symbols


def _capture_table(headers, rows, tablefmt='grid'):
    """替代 main._print_table，收集表格内容。"""
    _capture_table.tables.append((list(headers), [list(r) for r in rows]))


def _run_case(symbols, start_date, end_date, max_hold_days, label):
    main.set_primary_source('auto')
    main._set_fix_capital(False)
    main._set_max_hold_days(max_hold_days)

    _capture_table.tables = []
    original = main._print_table
    main._print_table = _capture_table
    stdout = io.StringIO()
    result_data = None
    try:
        with contextlib.redirect_stdout(stdout):
            result_data = main._run_onestock_backtest(
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
    finally:
        main._print_table = original

    output = stdout.getvalue()
    if '错误:' in output:
        raise RuntimeError(f'{label} 回测失败:\n{output}')

    if not _capture_table.tables:
        raise RuntimeError(f'{label} 未捕获到回测结果表')

    breakdown_headers, breakdown_rows = _capture_table.tables[0]
    trades = [int(t['holding_days'])
              for t in (result_data or {}).get('trades', [])]

    metrics = {}
    for row in breakdown_rows:
        if not row:
            continue
        name = str(row[0])
        if name in METRIC_NAMES and len(row) > 1:
            metrics[name] = row[1]

    return {
        'label': label,
        'max_hold_days': max_hold_days,
        'metrics': metrics,
        'hold_days': trades,
        'headers': breakdown_headers,
        'rows': breakdown_rows,
    }


def _to_number(text):
    text = str(text).strip()
    if text in ('—', '-', '', 'N/A'):
        return None
    if text == 'inf':
        return float('inf')
    if text.endswith('%'):
        try:
            return float(text[:-1]) / 100.0
        except ValueError:
            return None
    try:
        return float(text.replace(',', ''))
    except ValueError:
        return None


def _fmt_diff(name, left, right):
    a, b = _to_number(left), _to_number(right)
    if a is None or b is None:
        return ''
    diff = b - a
    if name in ('总收益率', '年化收益率', '最大回撤', '胜率'):
        return f'{diff * 100:+.2f} pct'
    if name in ('交易次数',):
        return f'{diff:+.0f}'
    if name in ('总盈亏额',):
        return f'{diff:+,.0f}'
    return f'{diff:+.2f}'


def _print_comparison(base, capped, symbols, start_date, end_date, max_days):
    capped_label = f'maxholddays={max_days}'
    print()
    print('=' * 78)
    print('  最大持股天数回测对比（onestock）')
    print(f'  区间: {start_date} ~ {end_date} | 策略: {", ".join(STRATEGIES)}')
    print(f'  股票池: {len(symbols)} 只 | 初始资金: {CAPITAL:,.0f}')
    print('=' * 78)
    print(f'{"指标":<14} {"不限制":>16} {capped_label:>18} {"差异":>14}')
    print('-' * 78)
    for name in METRIC_NAMES:
        left = base['metrics'].get(name, 'N/A')
        right = capped['metrics'].get(name, 'N/A')
        diff = _fmt_diff(name, left, right)
        print(f'{name:<14} {str(left):>16} {str(right):>18} {diff:>14}')

    base_hd = base['hold_days']
    capped_hd = capped['hold_days']
    print('-' * 78)
    print(f'交易笔数: 不限制={len(base_hd)}, {capped_label}={len(capped_hd)}')
    print(f'最大单笔持仓天数: 不限制={max(base_hd) if base_hd else 0}, '
          f'{capped_label}={max(capped_hd) if capped_hd else 0}')
    print(f'>{max_days} 天交易数: 不限制={sum(1 for x in base_hd if x > max_days)}, '
          f'{capped_label}={sum(1 for x in capped_hd if x > max_days)}')
    print(f'注: 超过 maxholddays 后还需收盘价低于 BOLL 中线且回撤 >10% 才卖出，因此持仓可能略超 {max_days} 天。')
    print('=' * 78)


def main_test(args):
    max_days = args.days
    start_date = args.start_date
    end_date = args.end_date
    symbols = _read_pool(args.symbol_file)
    capped_label = f'maxholddays={max_days}'
    print(f'股票池: {len(symbols)} 只 -> {" ".join(symbols)}')
    print('正在运行：不限制持股天数...')
    base = _run_case(symbols, start_date, end_date, None, 'maxhold_off')
    print(f'正在运行：{capped_label}...')
    capped = _run_case(symbols, start_date, end_date, max_days, f'maxhold_on_{max_days}')
    _print_comparison(base, capped, symbols, start_date, end_date, max_days)

    base_trades = len(base['hold_days'])
    capped_trades = len(capped['hold_days'])
    base_max_hold = max(base['hold_days']) if base['hold_days'] else 0
    capped_max_hold = max(capped['hold_days']) if capped['hold_days'] else 0
    base_avg_hold = _to_number(str(base['metrics'].get('持股天数', '0')).split('/')[0])
    capped_avg_hold = _to_number(str(capped['metrics'].get('持股天数', '0')).split('/')[0])

    assert capped_trades > 0, f'{capped_label} 未产生任何交易'
    assert capped_trades > base_trades, \
        f'{capped_label} 交易数({capped_trades}) 未增加（不限制={base_trades}）'
    assert capped_max_hold < base_max_hold, \
        f'{capped_label} 最大持仓({capped_max_hold}) 未低于不限制({base_max_hold})'
    assert base_avg_hold is not None and capped_avg_hold is not None \
        and capped_avg_hold < base_avg_hold, \
        f'{capped_label} 平均持仓({capped_avg_hold}) 未低于不限制({base_avg_hold})'

    # maxhold 还需收盘价低于 BOLL 中线才卖出，因此允许部分交易超过 N 天；
    # 只要最大/平均持仓下降且交易次数增加，即认为限制生效。
    print(f'验证通过：{capped_label} 缩短了最大/平均持仓，并增加了交易次数。')
    return base, capped


if __name__ == '__main__':
    main_test(_parse_args())

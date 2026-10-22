#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略效果排名测试脚本

通过反复执行

    python3 main.py backtest --random -g all -st <yyyymmdd> -e <yyyymmdd>

统计各策略（含回测自动追加的「复合策略」）在不同区间长度下的效果：
    - 长期：默认 3 年
    - 短期：默认 6 个月
每类区间重复 N 次（默认 3 次），随机生成起始日期，取各次平均值后排名。

排名依据：策略收益分析表中的「平均总收益率」与「持股年化收益率」。

注意：`main.py` 依赖 mplfinance/pandas 等，需用能运行它的解释器启动本脚本，
例如：  venv/bin/python test/strategy_rank_test.py

用法示例：
    venv/bin/python test/strategy_rank_test.py
    venv/bin/python test/strategy_rank_test.py --runs 5 --short-months 6
    venv/bin/python test/strategy_rank_test.py --task long
"""

import argparse
import calendar
import os
import random
import re
import subprocess
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, 'main.py')


def _pick_python():
    """选择用于运行 main.py 的解释器：环境变量 PYTHON > venv/bin/python > 当前解释器"""
    env = os.environ.get('PYTHON')
    if env:
        return env
    venv_py = os.path.join(ROOT, 'venv', 'bin', 'python')
    if os.path.exists(venv_py):
        return venv_py
    return sys.executable


PYTHON = _pick_python()

ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
PERCENT_COLS = ('平均总收益率', '持股年化收益率')

# --auto 模式下的固定场景（标签, 起始日, 结束日）；每个场景运行 AUTO_RUNS 次
AUTO_RUNS = 6
AUTO_SCENARIOS = (
    ('全场景', date(2018, 1, 1), date(2026, 10, 15)),
    ('牛市', date(2018, 1, 1), date(2026, 3, 1)),
    ('熊市', date(2018, 1, 1), date(2024, 3, 1)),
)


# ----------------------------------------------------------------------------
# 日期工具
# ----------------------------------------------------------------------------
def add_months(d, months):
    """在日期上加减月份（自动处理月末越界）"""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_years(d, years):
    """在日期上加减年份（2/29 越界时回退到 28 日）"""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def random_start(window, earliest, today):
    """随机生成起始日：满足 start >= earliest 且 start + window <= today"""
    latest_start = add_years(today, -window['years']) if window['years'] \
        else add_months(today, -window['months'])
    if latest_start < earliest:
        latest_start = earliest
    span = (latest_start - earliest).days
    if span <= 0:
        return earliest
    return earliest + timedelta(days=random.randint(0, span))


def window_end(start, window):
    if window['years']:
        return add_years(start, window['years'])
    return add_months(start, window['months'])


def parse_date_arg(s):
    return datetime.strptime(s, '%Y%m%d').date()


# ----------------------------------------------------------------------------
# 运行回测并解析结果
# ----------------------------------------------------------------------------
def run_backtest(start, end, src='sina'):
    """执行一次 backtest --random，返回生成的 HTML 报告路径"""
    cmd = [PYTHON, MAIN, 'backtest', '--random', '-g', 'boll,macd,ma520,hold',
           '--src', src,
           '-st', start.strftime('%Y%m%d'), '-e', end.strftime('%Y%m%d')]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    out = ANSI_RE.sub('', (proc.stdout or '') + '\n' + (proc.stderr or ''))

    m = re.search(r'HTML报告已保存到:\s*(\S+)', out)
    if not m:
        tail = '\n'.join(out.strip().splitlines()[-20:])
        raise RuntimeError(f'未找到 HTML 报告路径（返回码 {proc.returncode}）：\n{tail}')

    path = m.group(1)
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
    return path


def _to_ratio(text):
    text = (text or '').strip()
    if text in ('', 'N/A', '-'):
        return None
    if text.endswith('%'):
        text = text[:-1]
    try:
        return float(text) / 100.0
    except ValueError:
        return None


def parse_strategy_table(html_path):
    """从回测 HTML 的「策略收益分析」表提取 {策略: (平均总收益率, 持股年化收益率)}"""
    with open(html_path, encoding='utf-8') as f:
        html = f.read()
    m = re.search(r'策略收益分析</h2><table>(.*?)</table>', html, re.S)
    if not m:
        raise RuntimeError(f'报告缺少「策略收益分析」表: {html_path}')

    rows = re.findall(r'<tr>(.*?)</tr>', m.group(1), re.S)
    parsed = []
    for r in rows:
        cells = [re.sub(r'<.*?>', '', c).strip()
                 for c in re.findall(r'<t[hd]>(.*?)</t[hd]>', r, re.S)]
        parsed.append(cells)
    if not parsed:
        return {}

    headers = parsed[0]

    def col(name):
        try:
            return headers.index(name)
        except ValueError:
            raise RuntimeError(f'「策略收益分析」表缺少列 "{name}"：{headers}')

    i_name, i_ret, i_hy = col('策略'), col(PERCENT_COLS[0]), col(PERCENT_COLS[1])
    result = {}
    for cells in parsed[1:]:
        if len(cells) <= max(i_ret, i_hy):
            continue
        name = cells[i_name]
        if name == '全部':       # "全部"为汇总行，不参与排名
            continue
        result[name] = (_to_ratio(cells[i_ret]), _to_ratio(cells[i_hy]))
    return result


def aggregate(runs):
    """对多次运行结果按策略求平均：{策略: {'平均总收益率': x, '持股年化收益率': y}}"""
    buckets = {}
    for run in runs:
        for name, (ret, hy) in run.items():
            b = buckets.setdefault(name, {'ret': [], 'hy': []})
            if ret is not None:
                b['ret'].append(ret)
            if hy is not None:
                b['hy'].append(hy)

    def _mean(vals):
        return sum(vals) / len(vals) if vals else None

    return {name: {PERCENT_COLS[0]: _mean(b['ret']),
                   PERCENT_COLS[1]: _mean(b['hy'])}
            for name, b in buckets.items()}


def _fmt(v):
    return f'{v * 100:+.2f}%' if v is not None else 'N/A'


def print_ranking(title, agg, key):
    """按 key（PERCENT_COLS 之一）降序打印排名"""
    print(f'\n{title}（按{key}降序）')
    print(f'  {"排名":<4}{"策略":<12}{"平均总收益率":>14}{"持股年化收益率":>16}')
    print('  ' + '-' * 48)
    items = list(agg.items())
    items.sort(key=lambda kv: (kv[1][key] if kv[1][key] is not None else -1e9), reverse=True)
    for i, (name, v) in enumerate(items, 1):
        print(f'  {i:<4}{name:<12}{_fmt(v[PERCENT_COLS[0]]):>14}{_fmt(v[PERCENT_COLS[1]]):>16}')


def _src_attempts(src, retries):
    """尝试序列：src 与备用通道交替，共 retries 次（至少 1 次）"""
    alt = 'baostock' if src == 'sina' else 'sina'
    return [src if i % 2 == 0 else alt for i in range(max(1, retries))]


def _run_with_retry(start, end, args):
    """执行一次回测：失败时在 --src 与另一通道间交替重试；返回 HTML 路径或 None"""
    html_path = None
    last_err = None
    for use_src in _src_attempts(args.src, args.retries):
        try:
            html_path = run_backtest(start, end, src=use_src)
            break
        except Exception as e:
            last_err = e
            first_line = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
            print(f'           -> --src {use_src} 失败，改用另一通道重试: {first_line}', flush=True)
            html_path = None
    if html_path is None:
        print(f'           -> {args.retries} 次尝试均失败，跳过该次: {last_err}', flush=True)
    return html_path


def _collect_once(label, start, end, args, used=None):
    """运行一次并解析；成功返回 {策略: (平均总收益率, 持股年化收益率)}，失败返回 None"""
    print(f' {start} ~ {end} ...', flush=True)
    if used is not None:
        used.append((start, end))
    html_path = _run_with_retry(start, end, args)
    if html_path is None:
        return None
    try:
        table = parse_strategy_table(html_path)
    except Exception as e:
        print(f'           -> 报告解析失败，跳过该次: {e}', flush=True)
        return None
    print(f'           -> {os.path.relpath(html_path, ROOT)}（{len(table)} 个策略）', flush=True)
    return table


def run_horizon(label, window, args, today):
    """随机起始日区间：运行 N 次并返回聚合结果与使用的区间"""
    runs = []
    used = []
    for t in range(1, args.runs + 1):
        start = random_start(window, args.earliest, today)
        end = window_end(start, window)
        print(f'  [{label}] 第 {t}/{args.runs} 次:', end='', flush=True)
        table = _collect_once(label, start, end, args, used)
        if table is not None:
            runs.append(table)

    if not runs:
        print(f'  [{label}] 无有效运行结果', flush=True)
        return None, used
    return aggregate(runs), used


def run_fixed(label, start, end, runs_n, args):
    """固定区间：运行 runs_n 次（每次随机抽样股票）并返回聚合结果"""
    runs = []
    for t in range(1, runs_n + 1):
        print(f'  [{label}] 第 {t}/{runs_n} 次:', end='', flush=True)
        table = _collect_once(label, start, end, args)
        if table is not None:
            runs.append(table)

    if not runs:
        print(f'  [{label}] 无有效运行结果', flush=True)
        return None
    return aggregate(runs)


def report_cross(results, comparison_title, ranking_title, metric=PERCENT_COLS[1]):
    """跨场景对比：展示各场景 metric，并按跨场景均值（综合年化）排名"""
    labels = list(results)
    names = sorted(set().union(*[set(results[k]) for k in labels]))

    combo = {}
    for name in names:
        vals = [results[k].get(name, {}).get(metric) for k in labels]
        vals = [v for v in vals if v is not None]
        combo[name] = sum(vals) / len(vals) if vals else None

    print(f'\n===== {comparison_title} =====')
    hdr = f'  {"策略":<12}' + ''.join(f'{k:>16}' for k in labels) + f'{"综合年化":>12}'
    print(hdr)
    print('  ' + '-' * (len(hdr) - 2))
    for name in names:
        cells = ''.join(f'{_fmt(results[k].get(name, {}).get(metric)):>16}' for k in labels)
        print(f'  {name:<12}{cells}{_fmt(combo[name]):>12}')

    print(f'\n===== {ranking_title} =====')
    ranked = sorted(combo.items(),
                    key=lambda kv: (kv[1] if kv[1] is not None else -1e9), reverse=True)
    hdr2 = f'  {"排名":<4}{"策略":<12}' + ''.join(f'{k:>16}' for k in labels) + f'{"综合年化":>12}'
    print(hdr2)
    print('  ' + '-' * (len(hdr2) - 2))
    for i, (name, v) in enumerate(ranked, 1):
        cells = ''.join(f'{_fmt(results[k].get(name, {}).get(metric)):>16}' for k in labels)
        print(f'  {i:<4}{name:<12}{cells}{_fmt(v):>12}')


def _run_auto(args):
    """--auto：对固定的全场景/牛市/熊市三个区间各运行 AUTO_RUNS 次并排名"""
    print(f'解释器: {PYTHON}')
    print(f'命令:    {PYTHON} main.py backtest -sf input/big.txt -g all --src {args.src} -st <start> -e <end>')
    print(f'模式: --auto（固定场景，每场景 {AUTO_RUNS} 次）   数据源: {args.src}')

    results = {}
    for label, st, en in AUTO_SCENARIOS:
        print(f'\n===== {label} 场景（{st} ~ {en}） =====')
        agg = run_fixed(label, st, en, AUTO_RUNS, args)
        if agg is None:
            continue
        results[label] = agg
        print_ranking(f'{label} 策略排名', agg, PERCENT_COLS[0])
        print_ranking(f'{label} 策略排名', agg, PERCENT_COLS[1])

    if len(results) >= 2:
        report_cross(results, '场景对比：持股年化收益率',
                     '综合排名（各场景 持股年化收益率 平均）')


def main():
    ap = argparse.ArgumentParser(description='策略效果排名测试（基于 backtest --random -g all）')
    ap.add_argument('--runs', type=int, default=5, help='每类区间重复次数（默认 5）')
    ap.add_argument('--long-years', type=int, default=5, help='长期区间年数（默认 5）')
    ap.add_argument('--short-months', type=int, default=12, help='短期区间月数（默认 12）')
    ap.add_argument('--earliest', type=parse_date_arg, default=date(2020, 1, 1),
                    help='随机起始日不早于该日期，格式 YYYYMMDD（默认 20200101）')
    ap.add_argument('--seed', type=int, default=None, help='随机种子（可选）')
    ap.add_argument('--task', choices=['both', 'long', 'short'], default='both',
                    help='只测长期/短期/两者（默认 both）')
    ap.add_argument('--src', choices=['sina', 'baostock', 'eastmoney'], default='sina',
                    help='透传给 backtest 的首选数据源 [sina|baostock|eastmoney]（默认 sina）')
    ap.add_argument('--retries', type=int, default=3,
                    help='每次运行失败时在 --src 与另一通道间交替重试的最大次数（默认 3）')
    ap.add_argument('--auto', action='store_true',
                    help=f'自动回测 3 个固定场景（全场景/牛市/熊市），每个场景运行 {AUTO_RUNS} 次')
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    if args.auto:
        _run_auto(args)
        return

    today = date.today()
    long_win = {'years': args.long_years, 'months': 0}
    short_win = {'years': 0, 'months': args.short_months}

    horizons = []
    if args.task in ('both', 'long'):
        horizons.append((f'长期({args.long_years}年)', long_win))
    if args.task in ('both', 'short'):
        horizons.append((f'短期({args.short_months}个月)', short_win))

    print(f'解释器: {PYTHON}')
    print(f'命令:    {PYTHON} main.py backtest --random -g all --src {args.src} -st <start> -e <end>')
    print(f'重复次数: {args.runs}   起始日不早于: {args.earliest}   数据源: {args.src}')

    results = {}
    for label, win in horizons:
        print(f'\n===== {label} =====')
        agg, used = run_horizon(label, win, args, today)
        if agg is None:
            continue
        results[label] = agg
        for start, end in used:
            print(f'  使用区间: {start} ~ {end}')
        print_ranking(f'{label} 策略排名', agg, PERCENT_COLS[0])
        print_ranking(f'{label} 策略排名', agg, PERCENT_COLS[1])

    if len(results) >= 2:
        report_cross(results, '长期 vs 短期：持股年化收益率对比',
                     '综合排名（长期与短期 持股年化收益率 平均）')


if __name__ == '__main__':
    main()

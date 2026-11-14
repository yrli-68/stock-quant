#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全 A 股批量回测并导出 CSV 数据。

默认行为：
- 股票池：本地 SQLite 中全部 A 股（约 5500 只）
- 区间：结束日前 1 年
- 策略：boll,macd
- 数据源：sqlite（本地 stock_predict 缓存，缺失自动回退网络）
- 输出：report/backtest_all_a_<tag>_summary.csv
        report/backtest_all_a_<tag>_trades.csv
        report/backtest_all_a_<tag>_strategy_summary.csv

示例：
    .venv/bin/python test/backtest_all_a.py --limit 50
    .venv/bin/python test/backtest_all_a.py --start 20250101 --end 20261106
    .venv/bin/python test/backtest_all_a.py -g all --workers 6
    .venv/bin/python test/backtest_all_a.py -g boll,macd,hold --exclude-st
"""

import argparse
import math
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ============================================================================
# 单股票回测（在子进程中执行）
# ============================================================================

def _worker(task):
    code, name, start, end, strategies, capital, src, save_equity = task
    out = {'code': code, 'name': name, 'summary': [], 'trades': [],
           'equity': [], 'errors': []}
    try:
        import main
        from core.data_fetcher import DataFetcher, set_primary_source

        set_primary_source(src)
        main._set_fix_capital(False)
        main._set_seqnum(0)
        main._set_max_hold_days(None)

        fetcher = DataFetcher()
        df = fetcher.get_stock_data(
            code, main._warmup_fetch_start(start), end)
        if df is None or df.empty or len(df) < 20:
            out['errors'].append({
                'code': code, 'name': name, 'strategy': '',
                'error': '无行情数据或数据过短',
            })
            return out

        source = df.attrs.get('source', '')
        df = main.add_all_indicators(df)
        df = main._truncate_analysis_df(df, start)
        if df is None or df.empty:
            out['errors'].append({
                'code': code, 'name': name, 'strategy': '',
                'error': '分析区间无数据',
            })
            return out
        df.attrs['symbol'] = code

        for sk in strategies:
            try:
                res = main._run_strategy(sk, df, capital)
                sig = res.get('signals')
                last_signal = 0.0
                last_score = None
                if sig is not None and len(sig):
                    try:
                        last_signal = float(sig.iloc[-1])
                    except Exception:
                        last_signal = 0.0
                    score = getattr(sig, 'attrs', {}).get('score')
                    if isinstance(score, pd.Series) and len(score):
                        try:
                            last_score = float(score.iloc[-1])
                        except Exception:
                            last_score = None
                total_return = res.get('total_return')
                final_value = (capital * (1 + total_return)
                               if total_return is not None else None)
                out['summary'].append({
                    'code': code,
                    'name': name,
                    'strategy': res.get('strategy_name', sk),
                    'strategy_key': sk,
                    'start': start,
                    'end': end,
                    'bars': len(df),
                    'source': source,
                    'total_return': total_return,
                    'annual_return': res.get('annual_return'),
                    'max_drawdown': res.get('max_drawdown'),
                    'sharpe_ratio': res.get('sharpe_ratio'),
                    'win_rate': res.get('win_rate'),
                    'total_trades': res.get('total_trades'),
                    'profit_trades': res.get('profit_trades'),
                    'loss_trades': res.get('loss_trades'),
                    'profit_factor': res.get('profit_factor'),
                    'final_value': final_value,
                    'last_signal': last_signal,
                    'last_score': last_score,
                })

                tdf = res.get('trades_df')
                if tdf is not None and len(tdf):
                    for _, t in tdf.iterrows():
                        ed = t.get('entry_date')
                        xd = t.get('exit_date')
                        out['trades'].append({
                            'code': code,
                            'name': name,
                            'strategy': res.get('strategy_name', sk),
                            'entry_date': (ed.strftime('%Y-%m-%d')
                                           if hasattr(ed, 'strftime') else str(ed)),
                            'exit_date': (xd.strftime('%Y-%m-%d')
                                          if hasattr(xd, 'strftime') else str(xd)),
                            'entry_price': float(t.get('entry_price', 0) or 0),
                            'exit_price': float(t.get('exit_price', 0) or 0),
                            'position': float(t.get('position', 0) or 0),
                            'holding_days': int(t.get('holding_days', 0) or 0),
                            'return': float(t.get('return', 0) or 0),
                            'pnl': ((float(t.get('exit_price', 0) or 0)
                                     - float(t.get('entry_price', 0) or 0))
                                    * float(t.get('position', 0) or 0)),
                            'exit_reason': t.get('exit_reason', ''),
                        })

                if save_equity:
                    eq = res.get('equity_curve')
                    if eq is not None and len(eq):
                        for dt, val in eq.items():
                            out['equity'].append({
                                'code': code,
                                'name': name,
                                'strategy': res.get('strategy_name', sk),
                                'date': (dt.strftime('%Y-%m-%d')
                                         if hasattr(dt, 'strftime') else str(dt)),
                                'equity': float(val),
                            })
            except Exception as exc:
                out['errors'].append({
                    'code': code, 'name': name, 'strategy': sk,
                    'error': f'{type(exc).__name__}: {exc}',
                })
    except Exception as exc:
        out['errors'].append({
            'code': code, 'name': name, 'strategy': '',
            'error': f'{type(exc).__name__}: {exc}',
        })
    return out


# ============================================================================
# 股票池与参数
# ============================================================================

def _parse_date(value):
    for fmt in ('%Y%m%d', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f'日期格式错误: {value}，应为 YYYYMMDD')


def _load_universe(args):
    from core.sqlite_source import fetch_all_stock_codes, fetch_stock_list

    name_map = {}
    mktcap_map = {}
    try:
        listing = fetch_stock_list('changepercent')
        if listing is not None and len(listing):
            for _, row in listing.iterrows():
                sym = str(row.get('symbol', '')).zfill(6)
                if sym:
                    name_map[sym] = str(row.get('name', '') or '')
                    mktcap_map[sym] = row.get('mktcap')
    except Exception as exc:
        print(f'  [警告] 读取股票名称/市值失败: {exc}')

    if args.codes_file:
        codes = []
        with open(args.codes_file, 'r', encoding='utf-8') as f:
            for line in f:
                for token in line.replace(',', ' ').split():
                    token = token.strip()
                    if token.isdigit() and len(token) == 6:
                        codes.append(token)
        codes = list(dict.fromkeys(codes))
    else:
        codes = fetch_all_stock_codes()

    if not codes:
        raise SystemExit('未读取到任何 A 股代码，请检查 SQLite 数据源。')

    rows = []
    for code in codes:
        name = name_map.get(code, '')
        if args.exclude_st and 'ST' in name.upper():
            continue
        if args.min_mktcap is not None:
            cap = mktcap_map.get(code)
            try:
                cap = float(cap)
            except (TypeError, ValueError):
                cap = None
            if cap is None or cap < args.min_mktcap * 1e8:
                continue
        rows.append((code, name))

    if args.limit:
        rows = rows[:args.limit]
    return rows


def _parse_strategies(value):
    import main
    keys = []
    for item in str(value).replace('|', ',').split(','):
        key = item.strip()
        if not key:
            continue
        if key == 'all':
            keys.extend(main.ALL_SELECTION)
            continue
        if key not in main.STRATEGY_MAP:
            raise SystemExit(
                f'未知策略 "{key}"，可选: {", ".join(main.STRATEGY_MAP)}, all')
        keys.append(key)
    keys = list(dict.fromkeys(keys))
    if not keys:
        raise SystemExit('至少选择一个策略。')
    return keys


# ============================================================================
# 输出
# ============================================================================

def _write_outputs(args, summaries, trades, equities, errors, elapsed):
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    tag = args.tag or datetime.now().strftime('%Y%m%d_%H%M%S')
    summary_path = os.path.join(out_dir, f'backtest_all_a_{tag}_summary.csv')
    strategy_path = os.path.join(
        out_dir, f'backtest_all_a_{tag}_strategy_summary.csv')
    trades_path = os.path.join(out_dir, f'backtest_all_a_{tag}_trades.csv')
    errors_path = os.path.join(out_dir, f'backtest_all_a_{tag}_errors.csv')

    sdf = pd.DataFrame(summaries)
    if len(sdf):
        sdf = sdf.sort_values(['strategy', 'total_return'],
                              ascending=[True, False])
    sdf.to_csv(summary_path, index=False, encoding='utf-8-sig')

    top_paths = []
    if args.top and len(sdf):
        cols = ['code', 'name', 'strategy', 'total_return', 'annual_return',
                'max_drawdown', 'sharpe_ratio', 'win_rate', 'total_trades',
                'profit_factor', 'final_value', 'last_signal']
        for strat_name, grp in sdf.groupby('strategy'):
            ordered = grp.sort_values('total_return', ascending=False)
            top_path = os.path.join(
                out_dir, f'backtest_all_a_{tag}_top{args.top}_{strat_name}.csv')
            bottom_path = os.path.join(
                out_dir, f'backtest_all_a_{tag}_bottom{args.top}_{strat_name}.csv')
            ordered.head(args.top)[cols].to_csv(
                top_path, index=False, encoding='utf-8-sig')
            ordered.tail(args.top)[cols].sort_values('total_return').to_csv(
                bottom_path, index=False, encoding='utf-8-sig')
            top_paths.extend([top_path, bottom_path])

    if len(sdf):
        grouped = sdf.groupby('strategy', dropna=False)
        strat = grouped.agg(
            stocks=('code', 'nunique'),
            avg_total_return=('total_return', 'mean'),
            median_total_return=('total_return', 'median'),
            avg_annual_return=('annual_return', 'mean'),
            avg_max_drawdown=('max_drawdown', 'mean'),
            avg_sharpe=('sharpe_ratio', 'mean'),
            avg_win_rate=('win_rate', 'mean'),
            total_trades=('total_trades', 'sum'),
            avg_trades=('total_trades', 'mean'),
            positive_ratio=('total_return', lambda x: (x > 0).mean()),
            best=('total_return', 'max'),
            worst=('total_return', 'min'),
        ).reset_index()
        strat = strat.sort_values('avg_total_return', ascending=False)
    else:
        strat = pd.DataFrame(columns=[
            'strategy', 'stocks', 'avg_total_return', 'median_total_return',
            'avg_annual_return', 'avg_max_drawdown', 'avg_sharpe',
            'avg_win_rate', 'total_trades', 'avg_trades', 'positive_ratio',
            'best', 'worst'])
    strat.to_csv(strategy_path, index=False, encoding='utf-8-sig')

    if not args.no_trades:
        pd.DataFrame(trades).to_csv(
            trades_path, index=False, encoding='utf-8-sig')

    eq_path = ''
    if equities:
        eq_path = os.path.join(out_dir, f'backtest_all_a_{tag}_equity.csv')
        pd.DataFrame(equities).to_csv(
            eq_path, index=False, encoding='utf-8-sig')

    pd.DataFrame(errors).to_csv(
        errors_path, index=False, encoding='utf-8-sig')

    print()
    print('=' * 78)
    print(f'全 A 回测完成，用时 {elapsed / 60:.1f} 分钟')
    print(f'  股票×策略记录: {len(sdf)}')
    print(f'  交易明细:       {len(trades)}')
    print(f'  异常:           {len(errors)}')
    print(f'  汇总:           {summary_path}')
    print(f'  策略汇总:       {strategy_path}')
    if not args.no_trades:
        print(f'  交易明细:       {trades_path}')
    if eq_path:
        print(f'  权益曲线:       {eq_path}')
    if top_paths:
        print(f'  Top/Bottom:     {len(top_paths)} 个文件（目录 {out_dir}）')
    print(f'  异常记录:       {errors_path}')
    if len(strat):
        print()
        print('策略汇总（按平均总收益率排序）')
        cols = ['strategy', 'stocks', 'avg_total_return', 'median_total_return',
                'avg_max_drawdown', 'avg_sharpe', 'avg_win_rate',
                'positive_ratio', 'total_trades']
        view = strat[cols].copy()
        for col in ('avg_total_return', 'median_total_return',
                    'avg_max_drawdown', 'avg_win_rate', 'positive_ratio'):
            view[col] = (pd.to_numeric(view[col], errors='coerce') * 100
                         ).map(lambda x: f'{x:.2f}%' if pd.notna(x) else 'N/A')
        view['avg_sharpe'] = pd.to_numeric(
            view['avg_sharpe'], errors='coerce').map(
                lambda x: f'{x:.2f}' if pd.notna(x) else 'N/A')
        view['total_trades'] = view['total_trades'].map(
            lambda x: f'{int(x):,}' if pd.notna(x) else '0')
        print(view.to_string(index=False))
    print('=' * 78)


# ============================================================================
# 主流程
# ============================================================================

def main():
    ap = argparse.ArgumentParser(
        description='全 A 股批量回测并导出 CSV（复用 main.py 的策略与回测引擎）')
    ap.add_argument('--start', type=_parse_date, default=None,
                    help='开始日期 YYYYMMDD（默认结束日前 365 天）')
    ap.add_argument('--end', type=_parse_date, default=None,
                    help='结束日期 YYYYMMDD（默认昨天）')
    ap.add_argument('-g', '--strategies', default='boll,macd',
                    help='策略，逗号分隔，all=全部（默认 boll,macd）')
    ap.add_argument('--workers', type=int, default=min(6, os.cpu_count() or 4),
                    help='并行进程数（默认 min(6, CPU)）')
    ap.add_argument('--limit', type=int, default=0,
                    help='只回测前 N 只股票（调试用，0=全 A）')
    ap.add_argument('--codes-file', default=None,
                    help='从文件读取 6 位股票代码（默认使用 SQLite 全 A 列表）')
    ap.add_argument('--capital', type=float, default=100,
                    help='每只股票初始资金，单位万元（默认 100）')
    ap.add_argument('--src', default='sqlite',
                    choices=['auto', 'sqlite', 'sina', 'baostock',
                             'eastmoney', 'akshare'],
                    help='数据源（默认 sqlite，本地缺失自动回退网络）')
    ap.add_argument('--out', default=os.path.join(ROOT, 'report'),
                    help='输出目录（默认 report/）')
    ap.add_argument('--tag', default=None,
                    help='输出文件标签（默认时间戳）')
    ap.add_argument('--exclude-st', action='store_true',
                    help='排除名称含 ST 的股票')
    ap.add_argument('--min-mktcap', type=float, default=None,
                    help='最小总市值，单位亿元')
    ap.add_argument('--no-trades', action='store_true',
                    help='不导出交易明细 CSV')
    ap.add_argument('--top', type=int, default=20,
                    help='额外导出每个策略收益最高/最低 N 只（默认 20，0=不导出）')
    ap.add_argument('--equity', action='store_true',
                    help='额外导出每日权益曲线（文件较大）')
    args = ap.parse_args()

    end = args.end or (date.today() - timedelta(days=1))
    start = args.start or (end - timedelta(days=365))
    if start > end:
        raise SystemExit('开始日期不能晚于结束日期')
    start_s = start.strftime('%Y-%m-%d')
    end_s = end.strftime('%Y-%m-%d')
    strategies = _parse_strategies(args.strategies)
    capital = float(args.capital) * 10000

    universe = _load_universe(args)
    if not universe:
        raise SystemExit('过滤后没有可回测的股票。')

    print('=' * 78)
    print('全 A 股批量回测')
    print(f'  区间:     {start_s} ~ {end_s}')
    print(f'  股票:     {len(universe)} 只')
    print(f'  策略:     {", ".join(strategies)}')
    print(f'  资金:     {capital:,.0f} / 只')
    print(f'  数据源:   {args.src}')
    print(f'  并行:     {args.workers} 进程')
    print(f'  输出目录: {os.path.abspath(args.out)}')
    print('=' * 78, flush=True)

    tasks = [(code, name, start_s, end_s, strategies, capital,
              args.src, args.equity)
             for code, name in universe]
    summaries, trades, equities, errors = [], [], [], []
    t0 = time.time()
    done = 0
    total = len(tasks)

    # Linux 下用 fork 避免重复 import；其它平台使用默认启动方式。
    try:
        ctx = mp.get_context('fork')
    except ValueError:
        ctx = mp.get_context()

    with ProcessPoolExecutor(max_workers=max(1, args.workers),
                             mp_context=ctx) as ex:
        futures = {ex.submit(_worker, task): task[0] for task in tasks}
        try:
            for fut in as_completed(futures):
                done += 1
                try:
                    out = fut.result()
                    summaries.extend(out['summary'])
                    trades.extend(out['trades'])
                    equities.extend(out['equity'])
                    errors.extend(out['errors'])
                except Exception as exc:
                    errors.append({
                        'code': futures[fut], 'name': '',
                        'strategy': '', 'error': f'{type(exc).__name__}: {exc}',
                    })
                if done % 50 == 0 or done == total:
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed else 0
                    eta = (total - done) / rate / 60 if rate else math.nan
                    print(f'  进度 {done}/{total}  '
                          f'({done / total * 100:.1f}%)  '
                          f'用时 {elapsed / 60:.1f} min  '
                          f'预计剩余 {eta:.1f} min', flush=True)
        except KeyboardInterrupt:
            print('\n  收到中断，正在取消剩余任务…')
            for f in futures:
                f.cancel()
            raise

    _write_outputs(args, summaries, trades, equities, errors,
                   time.time() - t0)


if __name__ == '__main__':
    main()

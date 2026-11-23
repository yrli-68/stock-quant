#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 A 股市场遍历股票，按条件过滤后生成股票池文件。

用法示例：
    python3 sfilter.py -n 100 -f input/pool_filtered.txt \\
        --nost --nolowcapital --nohighpe --nohighprice
    python3 sfilter.py -n 100 -f input/pool_20250101.txt -d 20250101 --nost

选项：
    -n <num>       筛选 num 个符合条件的股票（默认 100）
    -f <file>      输出股票池文件名（默认 input/pool_filtered.txt；文件已存在时追加）
    -d <yyyymmdd>  筛选日期（默认今天），作为价格/PE 一年回看与市值估算的基准日
    --nost         排除 ST 股票（名称包含 ST）
    --nohighprice  排除近 1 年价格翻倍的股票（基准日收盘 >= 1 年前收盘 × 2）
    --nohighpe     排除 PE 为负、或近 1 年 PE 翻倍的股票
    --nolowcapital 排除总市值低于 50 亿元的股票

说明：
- 股票按数据源列表顺序（默认涨跌幅降序）遍历，取最先通过过滤的 num 只；
- 价格/PE 的历史判断按需读取，已选满 num 只即停止，不会遍历全市场；
- PE 历史来自数据库 valuation_history；无历史数据时不做“翻倍”判断（仅排除当前 PE 为负）；
- 指定历史日期时，总市值用“当前市值×（基准日收盘/当前价）”近似（股数按当前口径）；
- 历史日期仅影响价格/PE/市值取值口径，股票池仍基于当前在市股票列表。
"""

import argparse
import os
import sys
from datetime import datetime

import pandas as pd

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from core.data_fetcher import DataFetcher
from core.db import fetch_valuation, infer_asset_type


# 新浪 mktcap 单位为「万元」：50 亿 = 500,000 万元
LOW_CAP_50YI = 500000.0


def _parse_date(value):
    v = str(value).strip()
    for fmt in ('%Y%m%d', '%Y-%m-%d'):
        try:
            return datetime.strptime(v, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f'日期格式不正确: {v}，期望 YYYYMMDD 或 YYYY-MM-DD')


def _get_stock_list(fetcher, max_pages=60):
    """获取 A 股列表；若当前源不提供总市值（如 baostock），回退新浪列表。"""
    df = fetcher.get_stock_list(sort_key='changepercent', max_pages=max_pages)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    cap_ok = ('mktcap' in df.columns
              and pd.to_numeric(df['mktcap'], errors='coerce').notna().any())
    if not cap_ok:
        try:
            sina = fetcher._get_stock_list_sina('changepercent', max_pages)
            if sina is not None and len(sina) > 0:
                return sina
        except Exception:
            pass
    return df


def _load_close(fetcher, symbol, end_date, cache):
    """读取 [end_date-365, end_date] 的收盘价序列（按符号缓存）。"""
    if symbol in cache:
        return cache[symbol]
    start = (pd.Timestamp(end_date) - pd.Timedelta(days=365)).strftime('%Y-%m-%d')
    series = None
    try:
        kdf = fetcher.get_stock_data(symbol, start, end_date)
        if kdf is not None and len(kdf) > 0 and 'close' in kdf.columns:
            c = pd.to_numeric(kdf['close'], errors='coerce').dropna()
            if len(c) > 0:
                series = c
    except Exception:
        series = None
    cache[symbol] = series
    return series


def _price_doubled(close_series):
    """基准日收盘价相对 1 年前是否翻倍（>= 2 倍）；数据不足返回 False。"""
    if close_series is None or len(close_series) < 2:
        return False
    first = float(close_series.iloc[0])
    last = float(close_series.iloc[-1])
    if first <= 0:
        return False
    return last >= first * 2.0


def _market_cap_at(row, close_series, is_today):
    """基准日总市值（万元）。历史日用“当前市值×(基准日收盘/当前价)”近似。"""
    mktcap = pd.to_numeric(row.get('mktcap'), errors='coerce')
    if is_today or close_series is None or len(close_series) == 0:
        return mktcap
    price = pd.to_numeric(row.get('price'), errors='coerce')
    if pd.isna(mktcap) or pd.isna(price) or float(price) <= 0:
        return mktcap
    implied_shares = float(mktcap) / float(price)
    return implied_shares * float(close_series.iloc[-1])


def _pe_high_or_negative(symbol, list_pe, end_date, is_today):
    """PE 为负，或近 1 年 PE 翻倍时返回 True。

    - 基准日 PE <= 0：排除；
    - 基准日与约 1 年前正 PE 比较，>= 2 倍则排除；
    - 今天优先用列表实时 PE，历史日取 valuation_history 中 <= 基准日的最近值；
    - 无历史数据时不做翻倍判断。
    """
    start = (pd.Timestamp(end_date) - pd.Timedelta(days=365)).strftime('%Y-%m-%d')
    try:
        rows = fetch_valuation(symbol, start_date=start,
                               asset_type=infer_asset_type(symbol), force=True)
    except Exception:
        rows = []

    end_ts = pd.Timestamp(end_date)
    latest = None
    positives = []
    for row in rows or []:
        try:
            d = pd.Timestamp(row[0])
            v = float(row[1])  # (val_date, pe_ttm, ...)
        except (TypeError, ValueError, IndexError):
            continue
        if d > end_ts or pd.isna(v):
            continue
        latest = v
        if v > 0:
            positives.append(v)

    current = latest
    if is_today:
        try:
            lp = float(list_pe)
            if not pd.isna(lp):
                current = lp
        except (TypeError, ValueError):
            pass

    if current is None:
        return False
    if current <= 0:
        return True
    if not positives:
        return False
    return current >= positives[0] * 2.0


def _write_pool(path, items):
    """写入股票池；文件已存在时追加到末尾，不覆盖原有内容。返回是否追加。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    appended = os.path.exists(path) and os.path.getsize(path) > 0
    need_newline = False
    if appended:
        try:
            with open(path, 'rb') as f:
                f.seek(-1, os.SEEK_END)
                need_newline = f.read(1) != b'\n'
        except Exception:
            need_newline = False
    with open(path, 'a', encoding='utf-8') as f:
        if need_newline:
            f.write('\n')
        for symbol, name in items:
            f.write(f'{symbol} {name}'.rstrip() + '\n')
    return appended


def main():
    parser = argparse.ArgumentParser(
        description='从 A 股市场过滤生成股票池文件')
    parser.add_argument('-n', '--num', type=int, default=100,
                        help='筛选的股票数量（默认 100）')
    parser.add_argument('-f', '--file', default='input/pool_filtered.txt',
                        help='输出股票池文件（默认 input/pool_filtered.txt；已存在时追加，不覆盖）')
    parser.add_argument('-d', '--date', type=_parse_date, default=None,
                        help='筛选日期 YYYYMMDD（默认今天）')
    parser.add_argument('--nost', action='store_true',
                        help='排除 ST 股票')
    parser.add_argument('--nohighprice', action='store_true',
                        help='排除近 1 年价格翻倍的股票')
    parser.add_argument('--nohighpe', action='store_true',
                        help='排除 PE 为负或近 1 年 PE 翻倍的股票')
    parser.add_argument('--nolowcapital', action='store_true',
                        help='排除总市值低于 50 亿元的股票')
    args = parser.parse_args()

    if args.num <= 0:
        parser.error('-n/--num 必须为正整数')

    today = datetime.now().strftime('%Y-%m-%d')
    end_date = args.date or today
    is_today = (end_date == today)

    fetcher = DataFetcher()
    print('正在获取 A 股股票列表...', flush=True)
    df = _get_stock_list(fetcher)
    if df is None or len(df) == 0:
        print('错误: 未能获取股票列表', flush=True)
        return 1

    total = len(df)
    print(f'共获取 {total} 只股票，筛选日期 {end_date}，目标 {args.num} 只...',
          flush=True)

    selected = []
    checked = 0
    close_cache = {}
    for _, row in df.iterrows():
        if len(selected) >= args.num:
            break
        symbol = str(row.get('symbol') or row.get('code') or '').strip()
        name = str(row.get('name') or '').strip()
        if not symbol:
            continue
        checked += 1
        if checked % 20 == 0:
            print(f'  已检查 {checked}/{total}，已选中 {len(selected)}/{args.num}',
                  flush=True)

        if args.nost and 'ST' in name.upper():
            continue

        need_close = args.nohighprice or (args.nolowcapital and not is_today)
        close_series = (_load_close(fetcher, symbol, end_date, close_cache)
                        if need_close else None)

        if args.nolowcapital:
            cap = _market_cap_at(row, close_series, is_today)
            if pd.isna(cap) or float(cap) < LOW_CAP_50YI:
                continue

        if args.nohighpe and _pe_high_or_negative(
                symbol, row.get('pe'), end_date, is_today):
            continue

        if args.nohighprice and _price_doubled(close_series):
            continue

        selected.append((symbol, name))

    if not selected:
        print('未找到符合条件的股票', flush=True)
        return 1

    appended = _write_pool(args.file, selected)
    action = '追加到' if appended else '写入'
    print(f'已筛选 {len(selected)} 只股票（日期 {end_date}），{action}: {args.file}',
          flush=True)
    for symbol, name in selected:
        print(f'  {symbol} {name}'.rstrip(), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())

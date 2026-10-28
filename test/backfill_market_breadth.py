#!/usr/bin/env python3
"""回填 market_breadth 全部栏目（universe_code='ALL_A'）。

数据来源: daily_kline (asset_type='stock' AND adjust_type='raw')
计算用 pandas 完成，避免 MySQL 大窗口帧（250 日）性能问题。

用法:
    # 预览（默认 dry-run）
    python3 test/backfill_market_breadth.py
    # 真正写库
    python3 test/backfill_market_breadth.py --apply
    # 只回填指定日期区间（ADL 仍从全历史起算）
    python3 test/backfill_market_breadth.py --start 20240101 --end 20261231 --apply

口径与 data/migrations/20261028_backfill_market_breadth.sql 一致:
- change_pct = (今收-昨收)/昨收*100（按标的 LAG）
- advance/decline/flat = 涨/跌/平家数
- limit_up/down 按板块近似: 30/68 ±19.8%，8/4 ±29.8%，其余 ±9.8%
- new_high/low_N: 收盘创 N 日新高/新低（需至少 N 条历史）
- total_amount = SUM(amount)，缺失时用 volume*close 估算
- adr=涨/跌，obos=涨-跌，adl=obos 从最早日起累计
- adr_ma10/adl_ma20/obos_ma10 为对应序列 N 日移动平均
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from core.db import _load_db_config

KEYS = ['code', 'exchange', 'asset_type']
HIGHLOW_DAYS = (20, 60, 120, 250)


def _connect():
    import pymysql
    cfg = _load_db_config()
    if not cfg:
        raise SystemExit('未配置数据库 [tool.stock-quant.database]')
    return pymysql.connect(**cfg, connect_timeout=10, autocommit=False)


def _load_kline(conn):
    sql = """
        SELECT code, exchange, asset_type, trade_date, close, volume, amount
        FROM daily_kline
        WHERE asset_type = 'stock' AND adjust_type = 'raw'
        ORDER BY code, exchange, asset_type, trade_date
    """
    df = pd.read_sql(sql, conn)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    for c in ['close', 'volume', 'amount']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def _compute_breadth(df):
    df = df.sort_values(KEYS + ['trade_date']).reset_index(drop=True)

    # 涨跌幅（按标的取前收）
    df['prev_close'] = df.groupby(KEYS, sort=False)['close'].shift(1)
    prev = df['prev_close']
    chg = (df['close'] / prev - 1) * 100
    df['chg_pct'] = chg.where(prev.notna() & (prev != 0))

    # 涨跌/平标记
    df['is_up'] = (df['chg_pct'] > 0).astype(int)
    df['is_down'] = (df['chg_pct'] < 0).astype(int)
    df['is_flat'] = (df['chg_pct'] == 0).astype(int)

    # 涨跌停（板块近似阈值）
    code = df['code'].astype(str)
    thr = np.select(
        [code.str.startswith(('30', '68')), code.str.startswith(('8', '4'))],
        [19.8, 29.8], default=9.8)
    df['limit_up'] = (df['chg_pct'] >= thr).astype(int)
    df['limit_down'] = (df['chg_pct'] <= -thr).astype(int)

    # 成交量/成交额
    df['up_vol'] = np.where(df['chg_pct'] > 0, df['volume'], 0.0)
    df['down_vol'] = np.where(df['chg_pct'] < 0, df['volume'], 0.0)
    df['amount_est'] = df['amount'].where(
        df['amount'].notna(), df['volume'] * df['close'])

    # 创新高/新低（每组滚动窗口，需至少 N 条）
    for n in HIGHLOW_DAYS:
        roll_max = df.groupby(KEYS, sort=False)['close'].transform(
            lambda s: s.rolling(n, min_periods=n).max())
        roll_min = df.groupby(KEYS, sort=False)['close'].transform(
            lambda s: s.rolling(n, min_periods=n).min())
        df[f'new_high_{n}'] = (df['close'] >= roll_max).astype(int)
        df[f'new_low_{n}'] = (df['close'] <= roll_min).astype(int)

    g = df.groupby('trade_date', sort=True)
    out = pd.DataFrame({
        'advance_count': g['is_up'].sum(),
        'decline_count': g['is_down'].sum(),
        'flat_count': g['is_flat'].sum(),
        'total_count': g.size(),
        'limit_up_count': g['limit_up'].sum(),
        'limit_down_count': g['limit_down'].sum(),
        'up_volume': g['up_vol'].sum(),
        'down_volume': g['down_vol'].sum(),
        'total_volume': g['volume'].sum(),
        'total_amount': g['amount_est'].sum(),
        'avg_change_pct': g['chg_pct'].mean(),
        'median_change_pct': g['chg_pct'].median(),
    })
    for n in HIGHLOW_DAYS:
        out[f'new_high_{n}'] = g[f'new_high_{n}'].sum()
        out[f'new_low_{n}'] = g[f'new_low_{n}'].sum()
    out = out.reset_index()

    total = out['total_count'].replace(0, np.nan)
    decline = out['decline_count'].replace(0, np.nan)
    out['up_ratio'] = out['advance_count'] / total
    out['down_ratio'] = out['decline_count'] / total
    out['adr'] = out['advance_count'] / decline
    out['obos'] = out['advance_count'] - out['decline_count']
    out['adl'] = out['obos'].cumsum()
    out['adr_ma10'] = out['adr'].rolling(10, min_periods=1).mean()
    out['adl_ma20'] = out['adl'].rolling(20, min_periods=1).mean()
    out['obos_ma10'] = out['obos'].rolling(10, min_periods=1).mean()

    cols = [
        'trade_date',
        'advance_count', 'decline_count', 'flat_count', 'total_count',
        'limit_up_count', 'limit_down_count',
        'new_high_20', 'new_low_20', 'new_high_60', 'new_low_60',
        'new_high_120', 'new_low_120', 'new_high_250', 'new_low_250',
        'up_volume', 'down_volume', 'total_volume', 'total_amount',
        'avg_change_pct', 'median_change_pct',
        'up_ratio', 'down_ratio', 'adr', 'adl', 'obos',
        'adr_ma10', 'adl_ma20', 'obos_ma10',
    ]
    return out[cols]


def _to_db_value(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v


def _upsert(conn, out, universe, apply):
    cols = ['universe_code'] + list(out.columns) + ['source']
    placeholders = ','.join(['%s'] * len(cols))
    updates = ','.join(
        f'{c}=VALUES({c})' for c in cols
        if c not in ('universe_code', 'trade_date'))
    sql = (f"INSERT INTO market_breadth ({','.join(cols)}) "
           f"VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {updates}")

    rows = []
    for _, r in out.iterrows():
        row = [universe, r['trade_date'].date()]
        row += [_to_db_value(r[c]) for c in out.columns if c != 'trade_date']
        row.append('backfill')
        rows.append(tuple(row))

    if not apply:
        return len(rows)

    cur = conn.cursor()
    cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description='回填 market_breadth 全部栏目')
    ap.add_argument('--universe', default='ALL_A', help='统计口径（默认 ALL_A）')
    ap.add_argument('--start', default=None, help='输出起始日 YYYYMMDD')
    ap.add_argument('--end', default=None, help='输出结束日 YYYYMMDD')
    ap.add_argument('--apply', action='store_true', help='真正写库（默认 dry-run）')
    args = ap.parse_args()

    conn = _connect()
    print('读取 daily_kline ...')
    df = _load_kline(conn)
    print(f'原始记录: {len(df)}  标的数: {df[KEYS].drop_duplicates().shape[0]}')
    if df.empty:
        raise SystemExit('daily_kline 无可用数据')

    out = _compute_breadth(df)

    if args.start:
        out = out[out['trade_date'] >= pd.to_datetime(args.start)]
    if args.end:
        out = out[out['trade_date'] <= pd.to_datetime(args.end)]
    out = out.reset_index(drop=True)
    if out.empty:
        raise SystemExit('指定区间内无数据')

    print('=' * 78)
    print(f'模式: {"APPLY" if args.apply else "DRY-RUN"}   universe={args.universe}')
    print(f'待写入交易日: {len(out)}  '
          f'区间: {out["trade_date"].min().date()} ~ {out["trade_date"].max().date()}')
    sample = out.tail(3)[[
        'trade_date', 'advance_count', 'decline_count', 'flat_count',
        'total_count', 'new_high_20', 'adr', 'adl', 'obos',
        'avg_change_pct', 'median_change_pct', 'total_amount']]
    print(sample.to_string(index=False))

    n = _upsert(conn, out, args.universe, args.apply)
    conn.close()
    print('=' * 78)
    if args.apply:
        print(f'完成: 已写入/更新 {n} 个交易日的 market_breadth 全栏目。')
    else:
        print(f'DRY-RUN: 将写入/更新 {n} 个交易日；确认后加 --apply 执行。')


if __name__ == '__main__':
    main()

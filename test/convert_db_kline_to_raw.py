#!/usr/bin/env python3
"""将 daily_kline 中的前复权价格反复权还原为原始价格，并把 adjust_type 改为 'raw'。

原理:
    入库时: qfq = round(raw / factor, 3)
    反复权: raw = round(qfq * factor, 3)
    因子来自新浪 qfq.js，按交易日取 <= 该日的最近因子（与入库时一致）。

用法:
    # 预览（默认 dry-run，不写库）
    python3 test/convert_db_kline_to_raw.py
    # 只处理单只股票并写库
    python3 test/convert_db_kline_to_raw.py --code 002920 --apply
    # 只处理指定日期区间
    python3 test/convert_db_kline_to_raw.py --code 002920 --start 20260101 --end 20260630 --apply
    # 全量写库
    python3 test/convert_db_kline_to_raw.py --apply

注意:
- 只处理 adjust_type='qfq' 的行；若同一 code/日期已存在 raw 行，则删除重复的 qfq 行，保留已有 raw。
- 反复权依赖当前复权因子。若入库后发生过新的分红除权，历史 qfq 因子已整体缩放，
  反复权结果可能与真实原始价存在偏差；脚本会提示因子最新日期，请自行判断。
- 兼容新旧表结构（自动检测 exchange / asset_type 列）。
"""

import argparse
import logging
import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from core.data_fetcher import DataFetcher
from core.db import _load_db_config

PRICE_COLS = ['open', 'high', 'low', 'close']


def _connect():
    import pymysql
    cfg = _load_db_config()
    if not cfg:
        raise SystemExit('未配置数据库 [tool.stock-quant.database]')
    return pymysql.connect(**cfg, connect_timeout=10, autocommit=False)


def _table_columns(cur):
    cur.execute("SHOW COLUMNS FROM daily_kline")
    return {r[0] for r in cur.fetchall()}


def _codes(cur, cols, only_code=None, limit=None, start=None, end=None):
    if only_code:
        return [str(only_code)]
    where = ["adjust_type='qfq'"]
    params = []
    if start:
        where.append("trade_date >= %s")
        params.append(start)
    if end:
        where.append("trade_date <= %s")
        params.append(end)
    sql = (f"SELECT DISTINCT code FROM daily_kline WHERE {' AND '.join(where)} "
           f"ORDER BY code")
    if limit:
        sql += f" LIMIT {int(limit)}"
    cur.execute(sql, params)
    return [str(r[0]) for r in cur.fetchall()]


def _read_qfq_rows(cur, cols, code, start=None, end=None):
    select = ['id', 'trade_date'] + PRICE_COLS
    if 'exchange' in cols:
        select.append('exchange')
    if 'asset_type' in cols:
        select.append('asset_type')

    where = ["code=%s", "adjust_type='qfq'"]
    params = [code]
    if start:
        where.append("trade_date >= %s")
        params.append(start)
    if end:
        where.append("trade_date <= %s")
        params.append(end)

    cur.execute(
        f"SELECT {', '.join(select)} FROM daily_kline "
        f"WHERE {' AND '.join(where)} ORDER BY trade_date",
        params)
    rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=select)
    df = pd.DataFrame(rows, columns=select)
    df['trade_date'] = pd.to_datetime(df['trade_date']).astype('datetime64[ns]')
    for c in PRICE_COLS:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def _read_existing_raw_keys(cur, cols, code):
    select = ['trade_date']
    if 'exchange' in cols:
        select.append('exchange')
    if 'asset_type' in cols:
        select.append('asset_type')
    cur.execute(
        f"SELECT {', '.join(select)} FROM daily_kline "
        f"WHERE code=%s AND adjust_type='raw'", (code,))
    return {tuple(r) for r in cur.fetchall()}


def _invert_to_raw(df, factor_df):
    """返还 df，新增/覆盖 PRICE_COLS 为原始价，返回 (df, nan_factor_count)。"""
    df = df.copy()
    if factor_df is None or factor_df.empty:
        return df, 0

    f = factor_df.reset_index()
    date_col = 'date' if 'date' in f.columns else f.columns[0]
    f = f.rename(columns={date_col: 'factor_date', 'factor': 'factor'})
    f = f[['factor_date', 'factor']].sort_values('factor_date')
    f['factor_date'] = pd.to_datetime(f['factor_date']).astype('datetime64[ns]')

    df = df.sort_values('trade_date')
    df = pd.merge_asof(
        df, f, left_on='trade_date', right_on='factor_date',
        direction='backward')
    nan_factor = int(df['factor'].isna().sum())
    df['factor'] = df['factor'].fillna(1.0)
    for c in PRICE_COLS:
        df[c] = (df[c] * df['factor']).round(3)
    return df, nan_factor


def _process_code(cur, cols, code, fetcher, start, end, apply):
    df = _read_qfq_rows(cur, cols, code, start, end)
    if df.empty:
        return 0, 0, 0, None

    factor_df = fetcher._get_sina_adjust_factor(code, 'qfq')
    factor_max = None
    if factor_df is not None and not factor_df.empty:
        factor_max = pd.to_datetime(factor_df.index).max()

    df, nan_factor = _invert_to_raw(df, factor_df)

    raw_keys = _read_existing_raw_keys(cur, cols, code)
    has_exchange = 'exchange' in cols
    has_asset = 'asset_type' in cols

    update_rows = []
    delete_ids = []
    for r in df.itertuples(index=False):
        key = (pd.Timestamp(r.trade_date).date() if hasattr(r.trade_date, 'date')
               else r.trade_date,)
        if has_exchange:
            key += (r.exchange,)
        if has_asset:
            key += (r.asset_type,)
        if key in raw_keys:
            # 已存在 raw：删除重复的 qfq 行，保留原始 raw
            delete_ids.append((r.id,))
        elif any(pd.isna(getattr(r, c)) for c in PRICE_COLS):
            continue
        else:
            update_rows.append((
                float(r.open), float(r.high), float(r.low), float(r.close), r.id))

    if apply:
        if delete_ids:
            cur.executemany("DELETE FROM daily_kline WHERE id=%s", delete_ids)
        if update_rows:
            cur.executemany(
                "UPDATE daily_kline SET open=%s, high=%s, low=%s, close=%s, "
                "adjust_type='raw' WHERE id=%s AND adjust_type='qfq'",
                update_rows)
        cur.connection.commit()

    return len(update_rows), len(delete_ids), nan_factor, factor_max


def main():
    ap = argparse.ArgumentParser(
        description='将 daily_kline 的前复权价格反复权还原为原始价，并改 adjust_type=raw')
    ap.add_argument('--code', default=None, help='只处理指定股票代码')
    ap.add_argument('--limit', type=int, default=None, help='最多处理多少只股票')
    ap.add_argument('--start', default=None, help='起始交易日 YYYYMMDD')
    ap.add_argument('--end', default=None, help='结束交易日 YYYYMMDD')
    ap.add_argument('--apply', action='store_true',
                    help='真正写库（默认 dry-run，只预览）')
    args = ap.parse_args()

    conn = _connect()
    cur = conn.cursor()
    cols = _table_columns(cur)

    codes = _codes(cur, cols, args.code, args.limit, args.start, args.end)
    print('=' * 78)
    print(f'模式: {"APPLY（写库）" if args.apply else "DRY-RUN（仅预览，不写库）"}')
    print(f'待处理股票数: {len(codes)}   schema: '
          f'exchange={"有" if "exchange" in cols else "无"}, '
          f'asset_type={"有" if "asset_type" in cols else "无"}')
    if args.start or args.end:
        print(f'日期区间: {args.start or "最早"} ~ {args.end or "最新"}')
    if not args.apply:
        print('提示: 加 --apply 才会真正修改数据库')
    print('=' * 78)

    fetcher = DataFetcher(log_level=logging.WARNING)
    total_update = total_delete = total_nan = 0

    for i, code in enumerate(codes, 1):
        try:
            updated, deleted, nan_factor, factor_max = _process_code(
                cur, cols, code, fetcher, args.start, args.end, args.apply)
        except Exception as e:
            conn.rollback()
            print(f'[{i}/{len(codes)}] {code} 失败: {e}')
            continue

        total_update += updated
        total_delete += deleted
        total_nan += nan_factor
        note = ''
        if factor_max is not None:
            note = f' 因子最新={factor_max.date()}'
        if nan_factor:
            note += f' NaN因子={nan_factor}(按1.0处理)'
        print(f'[{i}/{len(codes)}] {code}: 还原={updated} 删除重复qfq={deleted}{note}')

    conn.close()
    print('=' * 78)
    print(f'完成: 还原为raw={total_update}  删除重复qfq={total_delete}  '
          f'NaN因子行={total_nan}')
    if not args.apply:
        print('以上为 DRY-RUN 结果，数据库未修改；确认无误后加 --apply 执行。')


if __name__ == '__main__':
    main()

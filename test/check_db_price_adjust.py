#!/usr/bin/env python3
"""检查数据库中K线价格是原始价还是复权价。

流程:
1. 从 daily_kline 读取指定股票的若干条K线（默认优先选取跨越除权除息日的区间）；
2. 从网络获取同区间的原始K线（东方财富，失败回退新浪）；
3. 用新浪前复权因子把原始K线换算为前复权价；
4. 分别计算数据库价格与「原始价」「前复权价」的偏差，判断数据库存储的是哪种价格。

用法:
    python3 test/check_db_price_adjust.py
    python3 test/check_db_price_adjust.py --code 600036 --rows 10

说明:
- 只读数据库，不修改任何数据。
- 兼容新旧库结构（不依赖 exchange / asset_type / adjust_type 列）。
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

TOL = 0.01  # 判定“价格相同”的绝对误差


def _connect():
    import pymysql
    cfg = _load_db_config()
    if not cfg:
        raise SystemExit('未配置数据库 [tool.stock-quant.database]')
    return pymysql.connect(**cfg, connect_timeout=5)


def _pick_code(cur, code=None):
    if code:
        return str(code)
    cur.execute("""
        SELECT code, COUNT(*) AS cnt, MAX(trade_date) AS mx
        FROM daily_kline
        GROUP BY code
        ORDER BY mx DESC, cnt DESC
        LIMIT 50
    """)
    for c, _cnt, _mx in cur.fetchall():
        c = str(c)
        if len(c) == 6 and c.isdigit() and c[0] in '0134568':
            return c
    raise SystemExit('数据库中没有可用于测试的A股K线')


def _read_db_rows(cur, code, rows):
    """读取数据库K线；优先取最近一次除权日前后各半的窗口。"""
    ex_date = None
    try:
        cur.execute("SHOW TABLES LIKE 'adjust_events'")
        if cur.fetchone():
            cur.execute(
                "SELECT MAX(ex_date) FROM adjust_events WHERE code=%s", (code,))
            row = cur.fetchone()
            ex_date = row[0] if row else None
    except Exception as e:
        print(f'读取除权日失败（忽略）: {e}')
        ex_date = None

    select_cols = "trade_date, open, high, low, close, volume"
    data = []
    if ex_date:
        half = max(1, rows // 2)
        cur.execute(f"""
            SELECT {select_cols} FROM daily_kline
            WHERE code=%s AND trade_date <= %s
            ORDER BY trade_date DESC LIMIT %s
        """, (code, ex_date, half))
        before = cur.fetchall()
        cur.execute(f"""
            SELECT {select_cols} FROM daily_kline
            WHERE code=%s AND trade_date > %s
            ORDER BY trade_date ASC LIMIT %s
        """, (code, ex_date, rows - half))
        after = cur.fetchall()
        data = before[::-1] + after

    if not data:
        cur.execute(f"""
            SELECT {select_cols} FROM daily_kline
            WHERE code=%s ORDER BY trade_date DESC LIMIT %s
        """, (code, rows))
        data = cur.fetchall()[::-1]

    if not data:
        raise SystemExit(f'数据库中没有 {code} 的K线数据')

    df = pd.DataFrame(
        data, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
    df['date'] = pd.to_datetime(df['date'])
    for c in ['open', 'high', 'low', 'close', 'volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['date']).sort_values('date').reset_index(drop=True)
    return df, ex_date


def _fetch_raw(fetcher, code, start, end):
    """获取同区间网络原始K线：优先东方财富，失败回退新浪。"""
    df = fetcher._get_a_stock_data_eastmoney(code, start, end)
    if df is not None and not df.empty:
        print('网络原始K线来源: 东方财富')
        return df
    df = fetcher._get_a_stock_data_sina(code, start, end, write_db=False)
    if df is not None and not df.empty:
        print('网络原始K线来源: 新浪')
        return df
    return pd.DataFrame()


def main():
    ap = argparse.ArgumentParser(description='检查数据库K线价格是原始价还是复权价')
    ap.add_argument('--code', default=None, help='股票代码，默认自动选择数据最新的A股')
    ap.add_argument('--rows', type=int, default=10, help='对比条数（默认10）')
    args = ap.parse_args()

    conn = _connect()
    cur = conn.cursor()
    code = _pick_code(cur, args.code)
    df_db, ex_date = _read_db_rows(cur, code, args.rows)
    conn.close()

    start = df_db['date'].min().strftime('%Y%m%d')
    end = df_db['date'].max().strftime('%Y%m%d')
    print('=' * 78)
    print(f'股票: {code}   条数: {len(df_db)}   区间: '
          f'{df_db["date"].min().date()} ~ {df_db["date"].max().date()}')
    print(f'最近除权日: {ex_date or "无"}')
    print('=' * 78)

    fetcher = DataFetcher(log_level=logging.WARNING)
    df_raw = _fetch_raw(fetcher, code, start, end)
    if df_raw.empty:
        raise SystemExit('从网络获取原始K线失败')

    factor_df = fetcher._get_sina_adjust_factor(code, 'qfq')
    if factor_df.empty:
        print('警告: 未获取到前复权因子，仅对比原始价')
        df_qfq = df_raw.copy()
    else:
        df_qfq = fetcher._apply_adjust_factor(df_raw.copy(), factor_df, 'qfq')

    m = df_db[['date', 'close']].merge(
        df_raw[['date', 'close']].rename(columns={'close': 'raw_close'}),
        on='date', how='inner')
    m = m.merge(
        df_qfq[['date', 'close']].rename(columns={'close': 'qfq_close'}),
        on='date', how='inner')
    if m.empty:
        raise SystemExit('数据库与网络数据日期无法匹配（数据库数据可能过旧或缺失）')

    m['diff_raw'] = (m['close'] - m['raw_close']).abs()
    m['diff_qfq'] = (m['close'] - m['qfq_close']).abs()

    print('\n日期         数据库收盘   网络原始价   前复权价   |差-原始|  |差-前复权|')
    print('-' * 78)
    for _, r in m.iterrows():
        print(f'{r["date"].date()}  {r["close"]:>10.3f}  {r["raw_close"]:>10.3f}  '
              f'{r["qfq_close"]:>9.3f}  {r["diff_raw"]:>8.4f}  {r["diff_qfq"]:>9.4f}')

    mean_raw = m['diff_raw'].mean()
    mean_qfq = m['diff_qfq'].mean()
    raw_vs_qfq = (m['raw_close'] - m['qfq_close']).abs().mean()
    n_raw = int((m['diff_raw'] <= TOL).sum())
    n_qfq = int((m['diff_qfq'] <= TOL).sum())

    print('-' * 78)
    print(f'平均绝对偏差: 原始价={mean_raw:.4f}   前复权价={mean_qfq:.4f}')
    print(f'接近(±{TOL})条数: 原始价={n_raw}/{len(m)}   前复权价={n_qfq}/{len(m)}')
    print(f'网络原始价与前复权价的差异: {raw_vs_qfq:.4f}')

    print('\n结论:')
    if raw_vs_qfq <= TOL:
        print('  ⚠️ 该区间原始价与前复权价几乎相同（未跨越除权日），无法区分。')
        print('     可指定 --code 并选择包含除权的区间后重试。')
    elif mean_qfq < mean_raw and n_qfq >= n_raw:
        print('  ✅ 数据库价格更接近【前复权价】→ 数据库存的是已复权价格。')
    elif mean_raw < mean_qfq and n_raw >= n_qfq:
        print('  ✅ 数据库价格更接近【原始价】→ 数据库存的是原始价格。')
    else:
        print('  ⚠️ 无法明确判断，请检查数据源或复权因子。')


if __name__ == '__main__':
    main()

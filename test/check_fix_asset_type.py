#!/usr/bin/env python3
"""检查并修正 daily_kline.asset_type（历史上全部默认成了 stock）。

判定优先级：
1. stocks 表：is_etf=1 -> etf；asset_type 为非 stock 的有效类型 -> 使用该值；
2. 否则按代码 + 交易所规则推断（区分 index/etf/fund/bond/stock）。

用法：
    # 预览
    python3 test/check_fix_asset_type.py
    # 写库
    python3 test/check_fix_asset_type.py --apply
    # 同时修正 stocks.asset_type，并把受影响日期加入广度重算队列
    python3 test/check_fix_asset_type.py --apply --include-stocks --mark-breadth

说明：
- 默认只读预览；--apply 才修改 daily_kline。
- 若同一 code/exchange/date/adjust_type 已同时存在目标 asset_type 的行，
  会产生唯一键冲突，脚本会跳过该标的并提示人工处理。
"""

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from core.db import _load_db_config, infer_asset_type


def _connect():
    import pymysql
    cfg = _load_db_config()
    if not cfg:
        raise SystemExit('未配置数据库 [tool.stock-quant.database]')
    return pymysql.connect(**cfg, connect_timeout=10, autocommit=False)


def _load_stocks_ref(cur):
    cur.execute("SELECT code, exchange, asset_type, is_etf, name FROM stocks")
    ref = {}
    for code, ex, at, is_etf, name in cur.fetchall():
        ref[(str(code), str(ex))] = {
            'asset_type': str(at or 'stock'),
            'is_etf': int(is_etf or 0),
            'name': name or '',
        }
    return ref


def _target_type(code, ex, ref):
    info = ref.get((str(code), str(ex)))
    if info:
        if info['is_etf'] == 1:
            return 'etf'
        if info['asset_type'] in ('index', 'etf', 'fund', 'bond'):
            return info['asset_type']
    return infer_asset_type(code, ex)


def _has_conflict(cur, code, ex, old_type, new_type):
    cur.execute("""
        SELECT COUNT(*) FROM daily_kline a
        JOIN daily_kline b
          ON a.code=b.code AND a.exchange=b.exchange
         AND a.trade_date=b.trade_date AND a.adjust_type=b.adjust_type
        WHERE a.code=%s AND a.exchange=%s
          AND a.asset_type=%s AND b.asset_type=%s
    """, (code, ex, old_type, new_type))
    return cur.fetchone()[0]


def main():
    ap = argparse.ArgumentParser(description='检查并修正 daily_kline.asset_type')
    ap.add_argument('--apply', action='store_true', help='真正写库（默认 dry-run）')
    ap.add_argument('--include-stocks', action='store_true',
                    help='同时修正 stocks.asset_type')
    ap.add_argument('--mark-breadth', action='store_true',
                    help='把受影响日期加入 market_breadth_dirty 队列')
    args = ap.parse_args()

    conn = _connect()
    cur = conn.cursor()
    ref = _load_stocks_ref(cur)

    cur.execute("""
        SELECT code, exchange, asset_type, COUNT(*) AS cnt,
               MIN(trade_date), MAX(trade_date)
        FROM daily_kline
        GROUP BY code, exchange, asset_type
        ORDER BY code, exchange, asset_type
    """)
    groups = cur.fetchall()

    mismatches = []
    for code, ex, at, cnt, mn, mx in groups:
        target = _target_type(code, ex, ref)
        if target != at:
            mismatches.append({
                'code': str(code), 'exchange': str(ex),
                'old': str(at), 'new': target,
                'rows': int(cnt), 'min': mn, 'max': mx,
            })

    print('=' * 92)
    print(f'模式: {"APPLY" if args.apply else "DRY-RUN"}   '
          f'daily_kline 分组: {len(groups)}   需修正: {len(mismatches)}')
    print('=' * 92)
    if not mismatches:
        print('asset_type 全部正确，无需修正。')
        conn.close()
        return

    print(f'{"code":<8}{"exchange":<10}{"old":<8}{"new":<8}{"rows":>8}  {"range":<24}')
    print('-' * 92)
    for m in mismatches:
        print(f'{m["code"]:<8}{m["exchange"]:<10}{m["old"]:<8}{m["new"]:<8}'
              f'{m["rows"]:>8}  {m["min"]} ~ {m["max"]}')

    if not args.apply:
        print('\nDRY-RUN: 未修改数据库；确认后加 --apply 执行。')
        conn.close()
        return

    total_rows = 0
    skipped = []
    try:
        for m in mismatches:
            if _has_conflict(cur, m['code'], m['exchange'], m['old'], m['new']):
                skipped.append(m)
                print(f'  跳过 {m["code"]}.{m["exchange"]}: '
                      f'目标类型 {m["new"]} 已存在同日数据（唯一键冲突）')
                continue
            cur.execute("""
                UPDATE daily_kline SET asset_type=%s
                WHERE code=%s AND exchange=%s AND asset_type=%s
            """, (m['new'], m['code'], m['exchange'], m['old']))
            total_rows += cur.rowcount
            if args.include_stocks:
                cur.execute("""
                    UPDATE stocks SET asset_type=%s
                    WHERE code=%s AND exchange=%s AND asset_type=%s
                """, (m['new'], m['code'], m['exchange'], m['old']))
            if args.mark_breadth:
                cur.execute("""
                    INSERT IGNORE INTO market_breadth_dirty
                        (universe_code, trade_date)
                    SELECT DISTINCT 'ALL_A', trade_date
                    FROM daily_kline
                    WHERE code=%s AND exchange=%s
                """, (m['code'], m['exchange']))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print('写库失败，已回滚:', e)
        conn.close()
        raise SystemExit(1)

    conn.close()
    print('-' * 92)
    print(f'完成: 修正 daily_kline {total_rows} 行，{len(mismatches)-len(skipped)} 个分组；'
          f'跳过 {len(skipped)} 个冲突分组。')
    if args.mark_breadth:
        print('受影响日期已加入 market_breadth_dirty，等待事件重算广度。')


if __name__ == '__main__':
    main()

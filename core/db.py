"""
数据库连接工具模块

提供 MySQL 连接管理和 CRUD 操作, 支持从 stock-quant.json 读取连接配置。
"""

import json
import os
import logging
from contextlib import contextmanager
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

_DB_CONFIG = None


def _load_db_config():
    """从 input/stock-quant.json 加载数据库配置"""
    global _DB_CONFIG
    if _DB_CONFIG is not None:
        return _DB_CONFIG

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'input', 'stock-quant.json'
    )
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        db_cfg = config.get('database', {})
        if db_cfg:
            _DB_CONFIG = {
                'host': db_cfg.get('host', 'localhost'),
                'port': db_cfg.get('port', 3306),
                'user': db_cfg.get('user', ''),
                'password': db_cfg.get('password', ''),
                'database': db_cfg.get('database', 'stock_quant'),
                'charset': db_cfg.get('charset', 'utf8mb4'),
            }
        else:
            _DB_CONFIG = None
    except Exception:
        _DB_CONFIG = None
    return _DB_CONFIG


@contextmanager
def get_connection():
    """获取数据库连接 (上下文管理器)"""
    cfg = _load_db_config()
    if not cfg:
        raise RuntimeError("数据库配置未找到, 请检查 input/stock-quant.json")

    try:
        import pymysql
        conn = pymysql.connect(**cfg, connect_timeout=5)
        yield conn
    except ImportError:
        logger.warning("pymysql 未安装, 将跳过数据库读写")
        yield None
    except Exception as e:
        logger.warning("数据库连接失败: %s", e)
        yield None
    finally:
        try:
            if 'conn' in dir() and conn is not None:
                conn.close()
        except Exception:
            pass


def fetch_kline(code: str, start_date: str, end_date: str) -> list:
    """从 daily_kline 表读取 K 线数据"""
    try:
        with get_connection() as conn:
            if conn is None:
                return []

            cursor = conn.cursor()
            cursor.execute("""
                SELECT trade_date, open, high, low, close, volume,
                       amount, amplitude, change_pct, change_val, turnover_rate
                FROM daily_kline
                WHERE code = %s AND trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date
            """, (code, start_date, end_date))
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取 K 线数据失败: %s", e)
        return []


def store_kline(code: str, rows: list):
    """批量写入 K 线数据到 daily_kline (REPLACE)"""
    if not rows:
        return
    try:
        with get_connection() as conn:
            if conn is None:
                return

            cursor = conn.cursor()
            sql = """
                REPLACE INTO daily_kline
                (code, trade_date, open, high, low, close, volume,
                 amount, amplitude, change_pct, change_val, turnover_rate, adjust_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'qfq')
            """
            cursor.executemany(sql, rows)
            conn.commit()
            logger.info("写入 %d 条 K 线数据: %s", len(rows), code)
    except Exception as e:
        logger.warning("写入 K 线数据失败: %s", e)


def fetch_valuation(code: str, start_date: str = '2010-01-01') -> list:
    """读取估值历史数据"""
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            cursor.execute("""
                SELECT val_date, pe_ttm, pb, ps, pcf, ey, dividend_yield
                FROM valuation_history
                WHERE code = %s AND val_date >= %s
                ORDER BY val_date
            """, (code, start_date))
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取估值数据失败: %s", e)
        return []


def store_valuation(code: str, rows: list):
    """批量写入估值历史 (REPLACE)"""
    if not rows:
        return
    try:
        with get_connection() as conn:
            if conn is None:
                return
            cursor = conn.cursor()
            sql = """
                REPLACE INTO valuation_history
                (code, val_date, pe_ttm, pb, ps, pcf, ey, dividend_yield, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'eastmoney')
            """
            cursor.executemany(sql, rows)
            conn.commit()
            logger.info("写入 %d 条估值数据: %s", len(rows), code)
    except Exception as e:
        logger.warning("写入估值数据失败: %s", e)


def fetch_financials(code: str) -> list:
    """读取财务报告数据"""
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            cursor.execute("""
                SELECT report_date, roe, cashflow_net_profit_ratio, net_margin,
                       debt_ratio, profit_growth
                FROM financial_reports
                WHERE code = %s
                ORDER BY report_date
            """, (code,))
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取财务数据失败: %s", e)
        return []


def store_financials(code: str, df):
    """写入财务报告数据 (REPLACE)"""
    import pandas as pd
    if df is None or df.empty:
        return
    try:
        rows = []
        cols_map = {
            '净资产收益率': 'roe',
            '销售净利率': 'net_margin',
            '资产负债率': 'debt_ratio',
        }
        for _, row in df.iterrows():
            report_date = str(row.get('报告期', row.get('日期', '')))[:10]
            if not report_date:
                continue
            vals = {'report_date': report_date, 'code': code}
            for src_key, db_key in cols_map.items():
                for col in df.columns:
                    if src_key in col:
                        v = row[col]
                        vals[db_key] = float(v) if pd.notna(v) else None
                        break
            rows.append(vals)
        if rows:
            store_financial_rows(rows)
    except Exception as e:
        logger.warning("写入财务数据失败: %s", e)


def store_financial_rows(rows: list):
    """批量写入财务数据"""
    if not rows:
        return
    try:
        with get_connection() as conn:
            if conn is None:
                return
            cursor = conn.cursor()
            # 构建动态 SQL
            keys = ['code', 'report_date', 'roe', 'cashflow_net_profit_ratio',
                    'net_margin', 'debt_ratio', 'profit_growth']
            placeholders = ', '.join(['%s'] * len(keys))
            sql = f"REPLACE INTO financial_reports ({', '.join(keys)}) VALUES ({placeholders})"
            data = []
            for r in rows:
                data.append(tuple(r.get(k, None) for k in keys))
            cursor.executemany(sql, data)
            conn.commit()
            logger.info("写入 %d 条财务数据", len(rows))
    except Exception as e:
        logger.warning("写入财务数据失败: %s", e)


def fetch_shareholder(code: str) -> list:
    """读取股东人数历史数据"""
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            cursor.execute("""
                SELECT change_date, holder_count, prev_holder_count,
                       holder_change_pct, avg_holding, prev_avg_holding,
                       avg_holding_change_pct
                FROM shareholder_history
                WHERE code = %s
                ORDER BY change_date
            """, (code,))
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取股东人数失败: %s", e)
        return []


def store_shareholder(code: str, df):
    """写入股东人数历史数据"""
    import pandas as pd
    if df is None or df.empty:
        return
    try:
        rows = []
        for _, row in df.iterrows():
            # 从列或索引中提取日期
            date_val = ''
            if '变动日期' in df.columns:
                date_val = str(row['变动日期'])[:10]
            elif 'change_date' in df.columns:
                date_val = str(row['change_date'])[:10]
            elif hasattr(row, 'name') and row.name is not None:
                d = pd.Timestamp(row.name)
                date_val = str(d)[:10]
            if not date_val or date_val == 'NaT':
                continue
            rows.append((
                code,
                date_val,
                _safe_float(row.get('本期股东人数')) if pd.notna(row.get('本期股东人数', None)) else None,
                _safe_float(row.get('上期股东人数')) if pd.notna(row.get('上期股东人数', None)) else None,
                _safe_float(row.get('股东人数增幅')) if pd.notna(row.get('股东人数增幅', None)) else None,
                _safe_float(row.get('本期人均持股数量')) if pd.notna(row.get('本期人均持股数量', None)) else None,
                _safe_float(row.get('上期人均持股数量')) if pd.notna(row.get('上期人均持股数量', None)) else None,
                _safe_float(row.get('人均持股数量增幅')) if pd.notna(row.get('人均持股数量增幅', None)) else None,
            ))
        if rows:
            with get_connection() as conn:
                if conn is None:
                    return
                cursor = conn.cursor()
                sql = """REPLACE INTO shareholder_history
                    (code, change_date, holder_count, prev_holder_count,
                     holder_change_pct, avg_holding, prev_avg_holding,
                     avg_holding_change_pct)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
                cursor.executemany(sql, rows)
                conn.commit()
                logger.info("写入 %d 条股东人数: %s", len(rows), code)
    except Exception as e:
        logger.warning("写入股东人数数据失败: %s", e)


def fetch_insider_trades(code: str) -> list:
    """读取增减持记录"""
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            cursor.execute("""
                SELECT announce_date, shareholder, change_amount, change_text,
                       trade_price, remaining, trade_period, trade_method
                FROM insider_trades
                WHERE code = %s
                ORDER BY announce_date
            """, (code,))
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取增减持数据失败: %s", e)
        return []


def _safe_float(val):
    """安全转换为 float，非数字返回 None"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def store_insider_trades(code: str, df):
    """写入增减持记录"""
    import pandas as pd
    if df is None or df.empty:
        return
    try:
        rows = []
        for _, row in df.iterrows():
            announce_str = str(row.get('公告日期', ''))[:10]
            if not announce_str or announce_str == 'NaT':
                continue
            rows.append((
                code,
                announce_str,
                str(row.get('变动股东', ''))[:100] if pd.notna(row.get('变动股东', None)) else None,
                None,  # change_amount 由文本解析，暂不存数值
                str(row.get('变动数量', ''))[:50] if pd.notna(row.get('变动数量', None)) else None,
                _safe_float(row.get('交易均价')) if pd.notna(row.get('交易均价', None)) else None,
                None,
                str(row.get('变动期间', ''))[:50] if pd.notna(row.get('变动期间', None)) else None,
                str(row.get('变动途径', ''))[:50] if pd.notna(row.get('变动途径', None)) else None,
            ))
        if rows:
            with get_connection() as conn:
                if conn is None:
                    return
                cursor = conn.cursor()
                sql = """REPLACE INTO insider_trades
                    (code, announce_date, shareholder, change_amount, change_text,
                     trade_price, remaining, trade_period, trade_method)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""
                cursor.executemany(sql, rows)
                conn.commit()
                logger.info("写入 %d 条增减持: %s", len(rows), code)
    except Exception as e:
        logger.warning("写入增减持数据失败: %s", e)


def fetch_stock_info(code: str) -> Optional[dict]:
    """从 stocks 表读取股票基本信息"""
    try:
        with get_connection() as conn:
            if conn is None:
                return None
            cursor = conn.cursor()
            cursor.execute(
                "SELECT code, name, market, stock_type, is_etf FROM stocks WHERE code = %s",
                (code,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    'code': row[0], 'name': row[1], 'market': row[2],
                    'stock_type': row[3], 'is_etf': row[4]
                }
            return None
    except Exception as e:
        logger.warning("读取股票信息失败: %s", e)
        return None


def store_stock_info(code: str, name: str, market: str = '', stock_type: str = 'dividend', is_etf: int = 0):
    """写入/更新股票基本信息"""
    try:
        with get_connection() as conn:
            if conn is None:
                return
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO stocks (code, name, market, stock_type, is_etf)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE name = VALUES(name), updated_at = CURRENT_TIMESTAMP
            """, (code, name, market, stock_type, is_etf))
            conn.commit()
            logger.info("写入股票信息: %s %s", code, name)
    except Exception as e:
        logger.warning("写入股票信息失败: %s", e)


def fetch_dividend_events(code: str) -> list:
    """从 adjust_events 读取除权除息历史"""
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            cursor.execute(
                "SELECT ex_date, cash_per_share, stock_per_share, plan_desc FROM adjust_events WHERE code = %s ORDER BY ex_date",
                (code,)
            )
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取除权数据失败: %s", e)
        return []


def store_dividend_events(code: str, df):
    """写入除权除息事件"""
    import pandas as pd
    if df is None or df.empty:
        return
    try:
        rows = []
        for _, row in df.iterrows():
            ex_date = str(row.get('ex_date', ''))[:10]
            if not ex_date or ex_date == 'NaT':
                continue
            plan = str(row.get('plan', ''))[:100] if pd.notna(row.get('plan', None)) else None
            rows.append((
                code, ex_date,
                _safe_float(row.get('cash_per_share')) or 0,
                _safe_float(row.get('stock_per_share')) or 0,
                plan,
                str(row.get('record_date', ''))[:10] if pd.notna(row.get('record_date', None)) else None,
                str(row.get('cash_date', ''))[:10] if pd.notna(row.get('cash_date', None)) else None,
                str(row.get('announce_date', ''))[:10] if pd.notna(row.get('announce_date', None)) else None,
            ))
        if rows:
            with get_connection() as conn:
                if conn is None:
                    return
                cursor = conn.cursor()
                sql = """REPLACE INTO adjust_events
                    (code, ex_date, cash_per_share, stock_per_share, plan_desc, record_date, cash_date, announce_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
                cursor.executemany(sql, rows)
                conn.commit()
                logger.info("写入 %d 条除权事件: %s", len(rows), code)
    except Exception as e:
        logger.warning("写入除权事件失败: %s", e)

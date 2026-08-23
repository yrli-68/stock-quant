"""
数据库连接工具模块

提供 MySQL 连接管理和 CRUD 操作, 支持从 stock-quant.json 读取连接配置。
"""

import json
import os
import logging
import threading
import time
import tempfile
from contextlib import contextmanager
from typing import Optional, List, Tuple

try:
    import fcntl
except ImportError:
    fcntl = None

logger = logging.getLogger(__name__)

_DB_CONFIG = None

# 数据库缓存模式：
#   0 = 忽略数据库缓存（纯网络，不读不写）
#   1 = 读缓存，不写数据库
#   2 = 不读缓存，直接走网络获取，获取的数据覆盖写入数据库
_DB_MODE = 1


def set_db_mode(mode: int):
    """设置数据库缓存模式 (0/1/2)"""
    global _DB_MODE
    _DB_MODE = int(mode)


def get_db_mode() -> int:
    """获取数据库缓存模式"""
    return _DB_MODE


# 写操作锁：多线程/多进程并发 REPLACE INTO 时 InnoDB 容易死锁（1213）。
# 优先用 fcntl 文件锁（跨进程，适用于多进程并行），不支持时回退线程锁。
_WRITE_LOCK = threading.Lock()
_LOCK_FILE_PATH = os.path.join(tempfile.gettempdir(), 'stock_quant_db_write.lock')

# 写锁获取超时（秒）。若残留/挂起进程一直持有文件锁，超时后跳过本次写库，
# 避免后续所有 -db 2 运行永久阻塞在 flock 上。
_LOCK_TIMEOUT = 30


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
def _open_connection():
    """建立数据库连接（不含模式判断）"""
    cfg = _load_db_config()
    if not cfg:
        raise RuntimeError("数据库配置未找到, 请检查 input/stock-quant.json")

    try:
        import pymysql
    except ImportError:
        logger.warning("pymysql 未安装, 将跳过数据库读写")
        yield None
        return

    conn = None
    try:
        conn = pymysql.connect(**cfg, connect_timeout=5, read_timeout=10, write_timeout=10)
    except Exception as e:
        logger.warning("数据库连接失败: %s", e)
        yield None
        return

    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def get_connection():
    """获取读数据库连接 (上下文管理器)"""
    if _DB_MODE in (0, 2):
        # 模式 0/2：不从数据库读取缓存
        yield None
        return
    with _open_connection() as conn:
        yield conn


@contextmanager
def _db_write_lock():
    """跨进程写锁（带超时）：优先 fcntl 文件锁，回退线程锁

    返回是否成功获取锁（False 表示超时未获取，调用方应跳过写库）。
    """
    if fcntl is not None:
        f = open(_LOCK_FILE_PATH, 'a')
        acquired = False
        deadline = time.time() + _LOCK_TIMEOUT
        try:
            while True:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.time() >= deadline:
                        logger.warning(
                            "获取写锁超时(%ds)，跳过本次数据库写入", _LOCK_TIMEOUT)
                        break
                    time.sleep(0.2)
            yield acquired
        finally:
            try:
                if acquired:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            finally:
                f.close()
    else:
        acquired = _WRITE_LOCK.acquire(timeout=_LOCK_TIMEOUT)
        try:
            yield acquired
        finally:
            if acquired:
                _WRITE_LOCK.release()


@contextmanager
def get_write_connection():
    """获取写数据库连接（带跨进程写锁，串行化写事务，避免死锁）"""
    if _DB_MODE in (0, 1):
        # 模式 0/1：不写数据库
        yield None
        return
    with _db_write_lock() as locked:
        if not locked:
            yield None
            return
        with _open_connection() as conn:
            yield conn


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
        with get_write_connection() as conn:
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


def fetch_indicators(code: str, start_date: str, end_date: str) -> list:
    """从 daily_indicators 读取技术指标"""
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            cursor.execute("""
                SELECT trade_date, ma5, ma10, ma20, ma60,
                       ema12, ema26, macd_dif, macd_dea, macd_bar,
                       rsi14, boll_upper, boll_middle, boll_lower,
                       kdj_k, kdj_d, kdj_j, atr14, obv, cci20, wr14,
                       vol_ma5, vwap, hv20, mom60
                FROM daily_indicators
                WHERE code = %s AND trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date
            """, (code, start_date, end_date))
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取技术指标失败: %s", e)
        return []


def store_indicators(code: str, df):
    """写入技术指标到 daily_indicators (REPLACE)"""
    import pandas as pd
    if df is None or df.empty:
        return
    col_map = {
        'MA5': 'ma5', 'MA10': 'ma10', 'MA20': 'ma20', 'MA60': 'ma60',
        'EMA12': 'ema12', 'EMA26': 'ema26',
        'MACD_DIF': 'macd_dif', 'MACD_DEA': 'macd_dea', 'MACD_BAR': 'macd_bar',
        'RSI14': 'rsi14',
        'BOLL_UPPER': 'boll_upper', 'BOLL_MIDDLE': 'boll_middle', 'BOLL_LOWER': 'boll_lower',
        'KDJ_K': 'kdj_k', 'KDJ_D': 'kdj_d', 'KDJ_J': 'kdj_j',
        'ATR14': 'atr14', 'OBV': 'obv', 'CCI20': 'cci20', 'WR14': 'wr14',
        'VOL_MA5': 'vol_ma5', 'VWAP': 'vwap',
        'HV20': 'hv20', 'MOM60': 'mom60',
    }
    try:
        rows = []
        for idx in df.index:
            date_str = idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)[:10]
            row_data = [code, date_str]
            for src_col, db_col in col_map.items():
                val = df.loc[idx, src_col] if src_col in df.columns else None
                row_data.append(float(val) if pd.notna(val) else None)
            rows.append(tuple(row_data))
        if rows:
            cols = ['code', 'trade_date'] + list(col_map.values())
            placeholders = ', '.join(['%s'] * len(cols))
            sql = f"REPLACE INTO daily_indicators ({', '.join(cols)}) VALUES ({placeholders})"
            with get_write_connection() as conn:
                if conn is None:
                    return
                cursor = conn.cursor()
                cursor.executemany(sql, rows)
                conn.commit()
                logger.info("写入 %d 条技术指标: %s", len(rows), code)
    except Exception as e:
        logger.warning("写入技术指标失败: %s", e)


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
        with get_write_connection() as conn:
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
        with get_write_connection() as conn:
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
            with get_write_connection() as conn:
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
            with get_write_connection() as conn:
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
        with get_write_connection() as conn:
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
            with get_write_connection() as conn:
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

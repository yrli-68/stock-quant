"""
数据库连接工具模块

提供 MySQL 连接管理和 CRUD 操作, 支持从 pyproject.toml 读取连接配置。
"""

import os
import logging
import threading
import time
import tempfile
from contextlib import contextmanager
from typing import Optional, List, Tuple

from core.config import load_config

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
# 默认 0（纯网络）：与 CLI 的 -db 缺省一致；未显式 set_db_mode 的命令（scan/backtest/compare）
# 不会尝试连接数据库，避免无数据库环境下的告警噪声。
_DB_MODE = 0


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


ASSET_TYPES = ('stock', 'index', 'etf', 'fund', 'bond')


def normalize_asset_type(asset_type: str = 'stock') -> str:
    """标准化资产类型；非法值回退为 stock。"""
    at = str(asset_type or 'stock').strip().lower()
    return at if at in ASSET_TYPES else 'stock'


def normalize_exchange(exchange: str = '') -> str:
    """标准化交易所代码。"""
    ex = str(exchange or '').strip().upper()
    aliases = {
        'SSE': 'SH', 'SHANGHAI': 'SH',
        'SZSE': 'SZ', 'SHENZHEN': 'SZ',
        'BSE': 'BJ', 'BEIJING': 'BJ',
    }
    return aliases.get(ex, ex[:8])


def infer_exchange(code: str, asset_type: str = 'stock') -> str:
    """根据代码和资产类型推断交易所。"""
    c = str(code or '').strip().upper()
    at = normalize_asset_type(asset_type)
    if at == 'index':
        if c.startswith(('399', '899')):
            return 'SZ'
        return 'SH'
    if c.startswith(('6', '9', '5')):
        return 'SH'
    if c.startswith(('0', '1', '2', '3')):
        return 'SZ'
    if c.startswith(('4', '8')):
        return 'BJ'
    return ''


def infer_asset_type(code: str, exchange: str = None) -> str:
    """根据代码与交易所推断资产类型（A股规则）。

    SH: 000/950 -> index；51/56/58 -> etf；50/52 -> fund；
        6/9 -> stock；1 -> bond
    SZ: 399 -> index；159 -> etf；15/16/18 -> fund；
        000/001/002/003/300/301/200 -> stock；1 -> bond
    BJ: 4/8 -> stock
    无法判断时回退 stock。
    """
    c = str(code or '').strip().upper()
    ex = normalize_exchange(exchange) or infer_exchange(c)
    if ex == 'SH':
        if c.startswith(('000', '950')):
            return 'index'
        if c.startswith(('51', '56', '58')):
            return 'etf'
        if c.startswith(('50', '52')):
            return 'fund'
        if c.startswith(('6', '9')):
            return 'stock'
        if c.startswith('1'):
            return 'bond'
    elif ex == 'SZ':
        if c.startswith('399'):
            return 'index'
        if c.startswith('159'):
            return 'etf'
        if c.startswith(('15', '16', '18')):
            return 'fund'
        if c.startswith(('000', '001', '002', '003', '300', '301', '200')):
            return 'stock'
        if c.startswith('1'):
            return 'bond'
    elif ex == 'BJ':
        if c.startswith(('4', '8')):
            return 'stock'
    return 'stock'


def normalize_instrument(code: str, exchange: str = None,
                         asset_type: str = 'stock'):
    """统一资产键：返回 (code, exchange, asset_type)。

    支持 600036 / sh600036 / 600036.SH 等写法。
    """
    raw = str(code or '').strip().upper()
    at = normalize_asset_type(asset_type)
    ex = ''
    if len(raw) > 2 and raw[:2] in ('SH', 'SZ', 'BJ') and raw[2:].isdigit():
        ex, raw = raw[:2], raw[2:]
    elif '.' in raw:
        left, right = raw.split('.', 1)
        if left in ('SH', 'SZ', 'BJ') and right.isdigit():
            ex, raw = left, right
        elif right in ('SH', 'SZ', 'BJ') and left.isdigit():
            ex, raw = right, left
    if raw.isdigit() and len(raw) < 6:
        raw = raw.zfill(6)
    ex = normalize_exchange(exchange) or ex or infer_exchange(raw, at)
    return raw, ex, at


def _load_db_config():
    """从 pyproject.toml 的 [tool.stock-quant.database] 加载数据库配置"""
    global _DB_CONFIG
    if _DB_CONFIG is not None:
        return _DB_CONFIG

    db_cfg = load_config().get('database') or {}
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
    return _DB_CONFIG


@contextmanager
def _open_connection():
    """建立数据库连接（不含模式判断）"""
    cfg = _load_db_config()
    if not cfg:
        # 未配置数据库：静默跳过（数据库为可选基础设施，不应报错）
        logger.debug("未配置数据库配置(pyproject.toml)，跳过数据库读写")
        yield None
        return

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
def get_connection(force: bool = False):
    """获取读数据库连接 (上下文管理器)

    Args:
        force: 为 True 时忽略 _DB_MODE，只要数据库已配置就尝试连接。
               用于 K 线缓存等需要主动读库的流程。
    """
    if not force and _DB_MODE in (0, 2):
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
def get_write_connection(force: bool = False):
    """获取写数据库连接（带跨进程写锁，串行化写事务，避免死锁）

    Args:
        force: 为 True 时忽略 _DB_MODE，只要数据库已配置就尝试写入。
               用于 K 线缓存补齐等需要主动写库的流程。
    """
    if not force and _DB_MODE in (0, 1):
        # 模式 0/1：不写数据库
        yield None
        return
    with _db_write_lock() as locked:
        if not locked:
            yield None
            return
        with _open_connection() as conn:
            yield conn


def fetch_market_trade_dates(start_date: str, end_date: str,
                             asset_type: str = 'stock',
                             adjust_type: str = 'raw',
                             force: bool = True) -> list:
    """读取区间内市场实际存在的交易日（任一个股有数据即视为交易日）。

    用于 K 线缺口检查时排除法定长假等非交易日，避免无意义的网络补数。
    """
    try:
        with get_connection(force=force) as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT trade_date
                FROM daily_kline
                WHERE asset_type = %s AND adjust_type = %s
                  AND trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date
            """, (asset_type, adjust_type, start_date, end_date))
            return [r[0] for r in cursor.fetchall()]
    except Exception as e:
        logger.warning("读取市场交易日失败: %s", e)
        return []


def fetch_kline(code: str, start_date: str, end_date: str,
                exchange: str = None, asset_type: str = None,
                adjust_type: str = 'qfq', force: bool = False) -> list:
    """从 daily_kline 表读取 K 线数据

    asset_type 为 None 时按代码自动推断（区分 stock/index/etf/fund/bond）。
    force 为 True 时忽略 _DB_MODE（用于 K 线缓存流程）。
    """
    if asset_type is None:
        asset_type = infer_asset_type(code, exchange)
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        with get_connection(force=force) as conn:
            if conn is None:
                return []

            cursor = conn.cursor()
            cursor.execute("""
                SELECT trade_date, open, high, low, close, volume,
                       amount, amplitude, change_pct, change_val, turnover_rate
                FROM daily_kline
                WHERE code = %s AND exchange = %s AND asset_type = %s
                  AND adjust_type = %s
                  AND trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date
            """, (code, exchange, asset_type, adjust_type, start_date, end_date))
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取 K 线数据失败: %s", e)
        return []


def store_kline(code: str, rows: list, exchange: str = None,
                asset_type: str = None, adjust_type: str = 'qfq',
                force: bool = False):
    """批量写入 K 线数据到 daily_kline (REPLACE)

    asset_type 为 None 时按代码自动推断（区分 stock/index/etf/fund/bond）。
    force 为 True 时忽略 _DB_MODE（用于 K 线缓存补齐）。
    """
    if not rows:
        return
    if asset_type is None:
        asset_type = infer_asset_type(code, exchange)
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        with get_write_connection(force=force) as conn:
            if conn is None:
                return

            cursor = conn.cursor()
            sql = """
                REPLACE INTO daily_kline
                (code, exchange, asset_type, trade_date, open, high, low, close,
                 volume, amount, amplitude, change_pct, change_val, turnover_rate,
                 adjust_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            data = [(code, exchange, asset_type, *row, adjust_type) for row in rows]
            cursor.executemany(sql, data)
            conn.commit()
            logger.info("写入 %d 条 K 线数据: %s %s/%s",
                        len(rows), code, exchange, asset_type)
    except Exception as e:
        logger.warning("写入 K 线数据失败: %s", e)


def fetch_indicators(code: str, start_date: str, end_date: str,
                     exchange: str = None, asset_type: str = 'stock',
                     adjust_type: str = 'qfq') -> list:
    """从 daily_indicators 读取技术指标"""
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            cursor.execute("""
                SELECT trade_date, ma5, ma10, ma20, ma30, ma60,
                       ema10, ema12, ema26, ema30, macd_dif, macd_dea, macd_bar,
                       rsi14, boll_upper, boll_middle, boll_lower,
                       kdj_k, kdj_d, kdj_j, adx14, plus_di, minus_di,
                       atr14, obv, cci20, wr14,
                       vol_ma5, vwap, hv20, mom60
                FROM daily_indicators
                WHERE code = %s AND exchange = %s AND asset_type = %s
                  AND adjust_type = %s
                  AND trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date
            """, (code, exchange, asset_type, adjust_type, start_date, end_date))
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取技术指标失败: %s", e)
        return []


def store_indicators(code: str, df, exchange: str = None,
                     asset_type: str = 'stock', adjust_type: str = 'qfq'):
    """写入技术指标到 daily_indicators (REPLACE)"""
    import pandas as pd
    if df is None or df.empty:
        return
    col_map = {
        'MA5': 'ma5', 'MA10': 'ma10', 'MA20': 'ma20', 'MA30': 'ma30', 'MA60': 'ma60',
        'EMA10': 'ema10', 'EMA12': 'ema12', 'EMA26': 'ema26', 'EMA30': 'ema30',
        'MACD_DIF': 'macd_dif', 'MACD_DEA': 'macd_dea', 'MACD_BAR': 'macd_bar',
        'RSI14': 'rsi14',
        'BOLL_UPPER': 'boll_upper', 'BOLL_MIDDLE': 'boll_middle', 'BOLL_LOWER': 'boll_lower',
        'KDJ_K': 'kdj_k', 'KDJ_D': 'kdj_d', 'KDJ_J': 'kdj_j',
        'ADX14': 'adx14', 'PLUS_DI': 'plus_di', 'MINUS_DI': 'minus_di',
        'ATR14': 'atr14', 'OBV': 'obv', 'CCI20': 'cci20', 'WR14': 'wr14',
        'VOL_MA5': 'vol_ma5', 'VWAP': 'vwap',
        'HV20': 'hv20', 'MOM60': 'mom60',
    }
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        rows = []
        for idx in df.index:
            date_str = idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)[:10]
            row_data = [code, exchange, asset_type, date_str, adjust_type]
            for src_col in col_map:
                val = df.loc[idx, src_col] if src_col in df.columns else None
                row_data.append(float(val) if pd.notna(val) else None)
            rows.append(tuple(row_data))
        if rows:
            cols = ['code', 'exchange', 'asset_type', 'trade_date', 'adjust_type'] + list(col_map.values())
            placeholders = ', '.join(['%s'] * len(cols))
            sql = f"REPLACE INTO daily_indicators ({', '.join(cols)}) VALUES ({placeholders})"
            with get_write_connection() as conn:
                if conn is None:
                    return
                cursor = conn.cursor()
                cursor.executemany(sql, rows)
                conn.commit()
                logger.info("写入 %d 条技术指标: %s %s/%s",
                            len(rows), code, exchange, asset_type)
    except Exception as e:
        logger.warning("写入技术指标失败: %s", e)


def fetch_valuation(code: str, start_date: str = '2010-01-01',
                    exchange: str = None, asset_type: str = 'stock') -> list:
    """读取估值历史数据"""
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            cursor.execute("""
                SELECT val_date, pe_ttm, pb, ps, pcf, ey, dividend_yield
                FROM valuation_history
                WHERE code = %s AND exchange = %s AND asset_type = %s
                  AND val_date >= %s
                ORDER BY val_date
            """, (code, exchange, asset_type, start_date))
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取估值数据失败: %s", e)
        return []


def store_valuation(code: str, rows: list, exchange: str = None,
                    asset_type: str = 'stock', source: str = 'eastmoney'):
    """批量写入估值历史 (REPLACE)"""
    if not rows:
        return
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        with get_write_connection() as conn:
            if conn is None:
                return
            cursor = conn.cursor()
            sql = """
                REPLACE INTO valuation_history
                (code, exchange, asset_type, val_date, pe_ttm, pb, ps, pcf,
                 ey, dividend_yield, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            data = [(code, exchange, asset_type, *row, source) for row in rows]
            cursor.executemany(sql, data)
            conn.commit()
            logger.info("写入 %d 条估值数据: %s %s/%s",
                        len(rows), code, exchange, asset_type)
    except Exception as e:
        logger.warning("写入估值数据失败: %s", e)


def fetch_financials(code: str, exchange: str = None,
                     asset_type: str = 'stock',
                     as_of: Optional[str] = None) -> list:
    """读取财务报告数据。

    as_of 用于回测/时点查询：仅返回 announce_date <= as_of 的记录。
    """
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            sql = """
                SELECT report_date, announce_date, data_version,
                       roe, cashflow_net_profit_ratio, net_margin,
                       debt_ratio, profit_growth, gross_margin, roa,
                       revenue_growth, eps
                FROM financial_reports
                WHERE code = %s AND exchange = %s AND asset_type = %s
            """
            params = [code, exchange, asset_type]
            if as_of:
                sql += " AND announce_date IS NOT NULL AND announce_date <= %s"
                params.append(as_of)
            sql += " ORDER BY announce_date, report_date, data_version"
            cursor.execute(sql, tuple(params))
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取财务数据失败: %s", e)
        return []


def _date_text(val) -> Optional[str]:
    """把日期值转成 YYYY-MM-DD；空值返回 None。"""
    import pandas as pd
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    text = str(val).strip()[:10]
    if not text or text in ('NaT', 'None', 'nan'):
        return None
    try:
        return pd.Timestamp(text).strftime('%Y-%m-%d')
    except Exception:
        return None


def store_financials(code: str, df, exchange: str = None,
                     asset_type: str = 'stock'):
    """写入财务报告数据（报告期 + 公告日 + 修订版本）"""
    import pandas as pd
    if df is None or df.empty:
        return
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        rows = []
        cols_map = {
            '净资产收益率': 'roe',
            '经营现金净流量与净利润的比率': 'cashflow_net_profit_ratio',
            '销售净利率': 'net_margin',
            '资产负债率': 'debt_ratio',
            '净利润增长率': 'profit_growth',
            '销售毛利率': 'gross_margin',
            '总资产净利润率': 'roa',
            '主营业务收入增长率': 'revenue_growth',
            '摊薄每股收益': 'eps',
        }
        for _, row in df.iterrows():
            report_date = _date_text(
                row.get('报告期', row.get('日期', row.get('report_date'))))
            if not report_date:
                continue
            announce_date = _date_text(
                row.get('公告日期', row.get('公告日',
                    row.get('announce_date', row.get('最新公告日期')))))
            try:
                data_version = int(row.get('data_version', 1) or 1)
            except (TypeError, ValueError):
                data_version = 1
            vals = {
                'code': code,
                'exchange': exchange,
                'asset_type': asset_type,
                'report_date': report_date,
                'announce_date': announce_date,
                'data_version': data_version,
            }
            for src_key, db_key in cols_map.items():
                for col in df.columns:
                    if src_key in col:
                        v = row[col]
                        vals[db_key] = _safe_float(v) if pd.notna(v) else None
                        break
            rows.append(vals)
        if rows:
            store_financial_rows(rows)
    except Exception as e:
        logger.warning("写入财务数据失败: %s", e)


def store_financial_rows(rows: list, exchange: str = None,
                         asset_type: str = 'stock'):
    """批量写入财务数据；仅更新本次提供的指标，避免清空其它列。"""
    if not rows:
        return
    try:
        keys = ['code', 'exchange', 'asset_type', 'report_date', 'announce_date',
                'data_version', 'roe', 'cashflow_net_profit_ratio', 'net_margin',
                'debt_ratio', 'profit_growth', 'gross_margin', 'roa',
                'revenue_growth', 'eps']
        placeholders = ', '.join(['%s'] * len(keys))
        updates = ', '.join(
            f'{k} = COALESCE(VALUES({k}), {k})' for k in keys[4:])
        sql = (f"INSERT INTO financial_reports ({', '.join(keys)}) "
               f"VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {updates}")
        data = []
        for r in rows:
            c, ex, at = normalize_instrument(
                r.get('code'), r.get('exchange', exchange),
                r.get('asset_type', asset_type))
            r = dict(r, code=c, exchange=ex, asset_type=at)
            data.append(tuple(r.get(k) for k in keys))
        with get_write_connection() as conn:
            if conn is None:
                return
            cursor = conn.cursor()
            cursor.executemany(sql, data)
            conn.commit()
            logger.info("写入 %d 条财务数据", len(rows))
    except Exception as e:
        logger.warning("写入财务数据失败: %s", e)


def fetch_shareholder(code: str, exchange: str = None,
                      asset_type: str = 'stock') -> list:
    """读取股东人数历史数据"""
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
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
                WHERE code = %s AND exchange = %s AND asset_type = %s
                ORDER BY change_date
            """, (code, exchange, asset_type))
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取股东人数失败: %s", e)
        return []


def store_shareholder(code: str, df, exchange: str = None,
                      asset_type: str = 'stock'):
    """写入股东人数历史数据"""
    import pandas as pd
    if df is None or df.empty:
        return
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
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
                exchange,
                asset_type,
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
                    (code, exchange, asset_type, change_date, holder_count,
                     prev_holder_count, holder_change_pct, avg_holding,
                     prev_avg_holding, avg_holding_change_pct)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
                cursor.executemany(sql, rows)
                conn.commit()
                logger.info("写入 %d 条股东人数: %s", len(rows), code)
    except Exception as e:
        logger.warning("写入股东人数数据失败: %s", e)


def fetch_insider_trades(code: str, exchange: str = None,
                         asset_type: str = 'stock') -> list:
    """读取增减持记录"""
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            cursor.execute("""
                SELECT announce_date, shareholder, change_amount, change_text,
                       trade_price, remaining, trade_period, trade_method
                FROM insider_trades
                WHERE code = %s AND exchange = %s AND asset_type = %s
                ORDER BY announce_date
            """, (code, exchange, asset_type))
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


def store_insider_trades(code: str, df, exchange: str = None,
                         asset_type: str = 'stock'):
    """写入增减持记录"""
    import pandas as pd
    if df is None or df.empty:
        return
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        rows = []
        for _, row in df.iterrows():
            announce_str = str(row.get('公告日期', ''))[:10]
            if not announce_str or announce_str == 'NaT':
                continue
            rows.append((
                code,
                exchange,
                asset_type,
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
                    (code, exchange, asset_type, announce_date, shareholder,
                     change_amount, change_text, trade_price, remaining,
                     trade_period, trade_method)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
                cursor.executemany(sql, rows)
                conn.commit()
                logger.info("写入 %d 条增减持: %s", len(rows), code)
    except Exception as e:
        logger.warning("写入增减持数据失败: %s", e)


def fetch_stock_info(code: str, exchange: str = None,
                     asset_type: str = 'stock') -> Optional[dict]:
    """从 stocks 表读取资产基本信息"""
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        with get_connection() as conn:
            if conn is None:
                return None
            cursor = conn.cursor()
            cursor.execute(
                """SELECT code, exchange, asset_type, name, market, stock_type, is_etf
                   FROM stocks
                   WHERE code = %s AND exchange = %s AND asset_type = %s""",
                (code, exchange, asset_type)
            )
            row = cursor.fetchone()
            if row:
                return {
                    'code': row[0], 'exchange': row[1], 'asset_type': row[2],
                    'name': row[3], 'market': row[4],
                    'stock_type': row[5], 'is_etf': row[6]
                }
            return None
    except Exception as e:
        logger.warning("读取股票信息失败: %s", e)
        return None


def store_stock_info(code: str, name: str, market: str = '',
                     stock_type: str = 'dividend', is_etf: int = 0,
                     exchange: str = None, asset_type: str = 'stock'):
    """写入/更新资产基本信息"""
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        with get_write_connection() as conn:
            if conn is None:
                return
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO stocks
                    (code, exchange, asset_type, name, market, stock_type, is_etf)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    market = VALUES(market),
                    stock_type = VALUES(stock_type),
                    is_etf = VALUES(is_etf),
                    updated_at = CURRENT_TIMESTAMP
            """, (code, exchange, asset_type, name, market, stock_type, is_etf))
            conn.commit()
            logger.info("写入资产信息: %s %s/%s %s", code, exchange, asset_type, name)
    except Exception as e:
        logger.warning("写入股票信息失败: %s", e)


def fetch_dividend_events(code: str, exchange: str = None,
                          asset_type: str = 'stock') -> list:
    """从 adjust_events 读取除权除息历史"""
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            cursor = conn.cursor()
            cursor.execute(
                """SELECT ex_date, cash_per_share, stock_per_share, plan_desc
                   FROM adjust_events
                   WHERE code = %s AND exchange = %s AND asset_type = %s
                   ORDER BY ex_date""",
                (code, exchange, asset_type)
            )
            return cursor.fetchall()
    except Exception as e:
        logger.warning("读取除权数据失败: %s", e)
        return []


def store_dividend_events(code: str, df, exchange: str = None,
                          asset_type: str = 'stock'):
    """写入除权除息事件"""
    import pandas as pd
    if df is None or df.empty:
        return
    code, exchange, asset_type = normalize_instrument(code, exchange, asset_type)
    try:
        rows = []
        for _, row in df.iterrows():
            ex_date = str(row.get('ex_date', ''))[:10]
            if not ex_date or ex_date == 'NaT':
                continue
            plan = str(row.get('plan', ''))[:100] if pd.notna(row.get('plan', None)) else None
            rows.append((
                code, exchange, asset_type, ex_date,
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
                    (code, exchange, asset_type, ex_date, cash_per_share,
                     stock_per_share, plan_desc, record_date, cash_date,
                     announce_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
                cursor.executemany(sql, rows)
                conn.commit()
                logger.info("写入 %d 条除权事件: %s", len(rows), code)
    except Exception as e:
        logger.warning("写入除权事件失败: %s", e)

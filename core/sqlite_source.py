#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stock_predict SQLite 缓存数据源（只读）。

对接 stock_predict 项目（默认 `../stock_predict/stock_cache.db`）的本地缓存库：

- ``daily_bars``：日 K 线。库内价格为后复权(hfq)，成交量单位为「手」。
- ``adjust``：显示缩放系数 k，``hfq * k`` 即乘法前复权(qfq)，与现价同一水平。
- ``stocks``：代码、名称、行业、总市值、换手率、PE、PB 快照。
- ``delisted``：已退市代码（列表查询时排除）。

本模块只做读取，不写入、不修改 stock_predict 的数据库。数据库不可用时
所有函数返回空结果，由调用方回退到 MySQL/网络数据源。
"""

import logging
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from core.config import app_root, load_config

logger = logging.getLogger(__name__)

_PATH_CACHE = None
_PATH_CHECKED = False

# stock_predict 中 A 股代码前缀：沪市 sh、深市 sz、北交所 bj
_A_SHARE_SQL = (
    "(s.code GLOB 'sh6*' OR s.code GLOB 'sz0*' OR s.code GLOB 'sz30*' "
    "OR s.code GLOB 'bj4*' OR s.code GLOB 'bj8*' OR s.code GLOB 'bj92*')"
)


def _config():
    cfg = load_config().get('sqlite') or {}
    return cfg if isinstance(cfg, dict) else {}


def _candidate_paths():
    """按优先级返回可能的数据库路径。"""
    cfg = _config()
    env_path = (os.environ.get('STOCK_QUANT_SQLITE')
                or os.environ.get('STOCK_DB') or '').strip()
    cfg_path = str(cfg.get('path') or '').strip()

    paths = []
    for raw in (env_path, cfg_path):
        if not raw:
            continue
        p = os.path.expanduser(raw)
        if not os.path.isabs(p):
            p = os.path.join(app_root(), p)
        paths.append(os.path.abspath(p))

    root = app_root()
    paths.extend([
        os.path.join(root, 'stock_predict', 'stock_cache.db'),
        os.path.join(os.path.dirname(root), 'stock_predict', 'stock_cache.db'),
        os.path.join(root, 'stock_cache.db'),
        os.path.join(os.path.dirname(root), 'stock_cache.db'),
    ])
    # 去重并保持顺序
    seen = set()
    out = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def sqlite_source_path(force: bool = False) -> str:
    """返回可用的 stock_predict SQLite 路径；未配置/不存在时返回空串。"""
    global _PATH_CACHE, _PATH_CHECKED
    if _PATH_CHECKED and not force:
        return _PATH_CACHE or ''

    cfg = _config()
    if cfg.get('enabled') is False:
        _PATH_CACHE = ''
        _PATH_CHECKED = True
        return ''

    for path in _candidate_paths():
        if os.path.isfile(path):
            _PATH_CACHE = path
            _PATH_CHECKED = True
            logger.info('SQLite 数据源已启用: %s', path)
            return path

    _PATH_CACHE = ''
    _PATH_CHECKED = True
    return ''


def sqlite_source_available() -> bool:
    """SQLite 数据源是否可用。"""
    return bool(sqlite_source_path())


@contextmanager
def _connect():
    """只读连接；WAL 库用 mode=ro 打开，失败时退回普通连接（仍只执行 SELECT）。"""
    path = sqlite_source_path()
    if not path:
        yield None
        return

    conn = None
    try:
        uri = Path(path).resolve().as_uri() + '?mode=ro'
        conn = sqlite3.connect(uri, uri=True, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=15000')
    except Exception as exc:
        logger.warning('只读打开 SQLite 失败，尝试普通连接: %s', exc)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        conn = sqlite3.connect(path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=15000')

    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def to_sqlite_code(symbol: str, kind: str = 'stock') -> str:
    """把 stock-quant 的代码转换为 stock_predict 的带市场前缀代码。

    - 600519 -> sh600519，000725 -> sz000725，920001 -> bj920001
    - kind='index'：000001 -> sh000001（上证指数），399001 -> sz399001
    - 已带 sh/sz/bj/hk/us 前缀时原样返回（小写）
    """
    raw = str(symbol or '').strip().lower()
    if re.fullmatch(r'(sh|sz|bj|hk|us|fu|cr)[0-9a-z]+', raw):
        return raw

    digits = re.sub(r'\D', '', raw)
    if not digits:
        return ''
    digits = digits[-6:].zfill(6)

    if kind == 'index':
        return ('sz' if digits.startswith('399') else 'sh') + digits

    if digits.startswith(('4', '8', '92')):
        return 'bj' + digits
    if digits.startswith(('6', '9', '5')):
        return 'sh' + digits
    return 'sz' + digits


def _date_text(value) -> str:
    return str(value).replace('-', '')[:8]


def _iso_date(value) -> str:
    d = _date_text(value)
    return f'{d[:4]}-{d[4:6]}-{d[6:8]}'


def _query_bars(conn, code: str, start_date: str, end_date: str):
    return conn.execute(
        """
        SELECT date, open, high, low, close, vol
        FROM daily_bars
        WHERE code = ? AND date >= ? AND date <= ?
        ORDER BY date
        """,
        (code, _iso_date(start_date), _iso_date(end_date)),
    ).fetchall()


def _adjust_k(conn, code: str) -> Optional[float]:
    try:
        row = conn.execute(
            'SELECT k FROM adjust WHERE code = ?', (code,)).fetchone()
        if row and row[0] is not None:
            k = float(row[0])
            return k if k > 0 else None
    except sqlite3.Error as exc:
        logger.debug('读取 adjust 失败(%s): %s', code, exc)
    return None


def fetch_daily_bars(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = 'qfq',
    kind: str = 'stock',
) -> pd.DataFrame:
    """读取日 K 线。

    Returns:
        DataFrame[date, open, high, low, close, volume]；volume 已换算为股。
        attrs 中带 source='sqlite'、adjust_effective（qfq/hfq）、sqlite_code、
        last_db_date、adjust_k。
    """
    if not sqlite_source_available():
        return pd.DataFrame()

    code = to_sqlite_code(symbol, kind=kind)
    if not code:
        return pd.DataFrame()

    try:
        with _connect() as conn:
            if conn is None:
                return pd.DataFrame()
            rows = _query_bars(conn, code, start_date, end_date)
            k = _adjust_k(conn, code) if kind == 'stock' else None
    except sqlite3.Error as exc:
        logger.warning('读取 SQLite K 线失败(%s): %s', code, exc)
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    df = df.rename(columns={'vol': 'volume'})
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    for col in ('open', 'high', 'low', 'close', 'volume'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['date', 'open', 'high', 'low', 'close'])
    if df.empty:
        return df

    # stock_predict 的成交量单位为「手」，换算为股，与新浪/东财口径一致。
    df['volume'] = df['volume'].fillna(0) * 100.0

    adjust = str(adjust or 'qfq').lower()
    effective = 'hfq'
    if adjust == 'qfq' and k:
        for col in ('open', 'high', 'low', 'close'):
            df[col] = df[col] * k
        effective = 'qfq'
    elif adjust == 'qfq':
        # 无 adjust 记录（多为新上市/异常标的）：保留 hfq，收益率仍然正确。
        logger.debug('%s 无 adjust 系数，返回后复权价格', code)

    df = df.sort_values('date').reset_index(drop=True)
    df.attrs.update({
        'source': 'sqlite',
        'adjust_effective': effective,
        'sqlite_code': code,
        'last_db_date': df['date'].max(),
        'adjust_k': k,
    })
    logger.info('SQLite 读取 %s (%s) %d 条，复权=%s',
                symbol, code, len(df), effective)
    return df


def fetch_index_bars(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """读取指数日 K（指数无复权系数，按库内价格返回）。"""
    return fetch_daily_bars(
        symbol, start_date, end_date, adjust='qfq', kind='index')


def fetch_stock_list(sort_key: str = 'changepercent') -> pd.DataFrame:
    """从 SQLite 生成 A 股快照列表，字段与 DataFetcher.get_stock_list 对齐。

    价格/涨跌幅/成交量取每只股票最后一个交易日；市值/换手率/PE/PB 取
    stocks 表快照。返回列为 symbol/name/price/change_pct/volume/amount/
    turnover_rate/mktcap/pe/pb。
    """
    if not sqlite_source_available():
        return pd.DataFrame()

    sql = f"""
        SELECT s.code AS code, s.name AS name,
               b.date AS date, b.close AS close, b.vol AS vol,
               s.mktcap AS mktcap, s.turnover AS turnover,
               s.pe AS pe, s.pb AS pb,
               COALESCE(a.k, 1.0) AS k,
               (SELECT b2.close FROM daily_bars b2
                 WHERE b2.code = s.code AND b2.date < b.date
                 ORDER BY b2.date DESC LIMIT 1) AS prev_close
        FROM stocks s
        JOIN daily_bars b ON b.code = s.code
        LEFT JOIN adjust a ON a.code = s.code
        WHERE b.date = (SELECT MAX(b3.date) FROM daily_bars b3
                         WHERE b3.code = s.code)
          AND {_A_SHARE_SQL}
          AND b.close > 0
          AND NOT EXISTS (SELECT 1 FROM delisted d WHERE d.code = s.code)
    """
    try:
        with _connect() as conn:
            if conn is None:
                return pd.DataFrame()
            rows = conn.execute(sql).fetchall()
    except sqlite3.Error as exc:
        logger.warning('读取 SQLite 股票列表失败: %s', exc)
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    for col in ('close', 'vol', 'mktcap', 'turnover', 'pe', 'pb', 'k',
                'prev_close'):
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # 全库快照过旧时让调用方回退网络，避免扫描基于过期数据。
    latest = pd.to_datetime(df['date'], errors='coerce').max()
    if pd.isna(latest) or (pd.Timestamp.today().normalize()
                           - latest.normalize()).days > 30:
        logger.warning('SQLite 股票列表最新日期为 %s，判定过旧', latest)
        return pd.DataFrame()

    k = df['k'].fillna(1.0)
    df['price'] = df['close'] * k
    prev = df['prev_close']
    df['change_pct'] = ((df['close'] / prev - 1.0) * 100.0).where(
        prev.notna() & (prev > 0), 0.0)
    df['volume'] = df['vol'].fillna(0) * 100.0
    df['amount'] = df['price'].fillna(0) * df['volume'].fillna(0)
    df['turnover_rate'] = df['turnover']
    df['symbol'] = df['code'].str[2:]
    df['name'] = df['name'].fillna('')

    out = df[[
        'symbol', 'name', 'price', 'change_pct', 'volume', 'amount',
        'turnover_rate', 'mktcap', 'pe', 'pb',
    ]].copy()

    sort_col = {
        'changepercent': 'change_pct',
        'turnoverratio': 'turnover_rate',
        'amount': 'amount',
        'mktcap': 'mktcap',
        'pe': 'pe',
    }.get(sort_key, 'change_pct')
    out = out.sort_values(sort_col, ascending=False, na_position='last')
    out = out.drop_duplicates(subset=['symbol']).reset_index(drop=True)
    logger.info('SQLite 股票列表 %d 只（排序: %s）', len(out), sort_key)
    return out


def fetch_all_stock_codes() -> list:
    """返回全部 A 股 6 位代码（按代码排序）。"""
    if not sqlite_source_available():
        return []
    sql = f"""
        SELECT s.code FROM stocks s
        WHERE {_A_SHARE_SQL}
          AND NOT EXISTS (SELECT 1 FROM delisted d WHERE d.code = s.code)
        ORDER BY s.code
    """
    try:
        with _connect() as conn:
            if conn is None:
                return []
            rows = conn.execute(sql).fetchall()
    except sqlite3.Error as exc:
        logger.warning('读取 SQLite 代码表失败: %s', exc)
        return []
    return [str(r[0])[2:] for r in rows if r and r[0]]


def stock_name(symbol: str) -> str:
    """按代码查询名称；查不到返回空串。"""
    if not sqlite_source_available():
        return ''
    code = to_sqlite_code(symbol)
    try:
        with _connect() as conn:
            if conn is None:
                return ''
            row = conn.execute(
                'SELECT name FROM stocks WHERE code = ?', (code,)).fetchone()
        return str(row[0] or '') if row else ''
    except sqlite3.Error:
        return ''


def find_code_by_name(name: str) -> Tuple[Optional[str], Optional[str]]:
    """按名称查代码，返回 (6 位代码, 名称)；未找到返回 (None, None)。"""
    if not sqlite_source_available():
        return None, None
    key = str(name or '').strip()
    if not key:
        return None, None

    sql = f"""
        SELECT s.code, s.name FROM stocks s
        WHERE {_A_SHARE_SQL}
          AND NOT EXISTS (SELECT 1 FROM delisted d WHERE d.code = s.code)
          AND s.name LIKE ?
        ORDER BY CASE WHEN s.name = ? THEN 0 ELSE 1 END, s.code
        LIMIT 200
    """

    def _rows(like_key):
        try:
            with _connect() as conn:
                if conn is None:
                    return []
                return conn.execute(sql, (like_key, key)).fetchall()
        except sqlite3.Error as exc:
            logger.warning('SQLite 名称查询失败(%s): %s', key, exc)
            return []

    rows = _rows(f'%{key}%')
    # stock_predict 的名称常含全角字母（京东方Ａ），这里做 NFKC 归一化兜底。
    if not rows:
        core = unicodedata.normalize('NFKC', key)
        cjk = ''.join(ch for ch in core if '\u4e00' <= ch <= '\u9fff')
        if cjk and cjk != key:
            rows = _rows(f'%{cjk}%')

    if not rows:
        return None, None

    norm_key = unicodedata.normalize('NFKC', key).upper()
    best = None
    for row in rows:
        name = str(row[1] or '')
        norm_name = unicodedata.normalize('NFKC', name).upper()
        if norm_name == norm_key:
            best = row
            break
        if best is None and norm_key in norm_name:
            best = row
    if best is None:
        best = rows[0]

    code = str(best[0])
    return (code[2:] if len(code) > 2 else code), str(best[1] or '')

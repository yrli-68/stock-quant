"""
股票数据获取模块

提供A股和美股数据的统一获取接口。
- A股数据使用新浪财经 / 东方财富 API
- 支持前复权(qfq)、后复权(hfq)、不复权(raw)三种价格
- 支持除权除息（分红送配）历史数据查询
- 美股数据使用 yfinance 作为备选

Author: stock_quant
"""

import json
import logging
import re
import time
from datetime import datetime
from typing import Optional, List

import numpy as np
import pandas as pd
import requests

# 配置模块级日志
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

# HTTP 请求头
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/'
}

# 首选数据源（'sina' / 'baostock' / 'eastmoney' / 'db'）：先尝试该源，失败自动改用其它通道。
# 'db' 为严格模式：只读 MySQL，数据不完整时抛出 DataSourceError，不回退网络。
# 由 CLI 的 --src 选项通过 set_primary_source() 设置。
_PRIMARY_SOURCE = 'sina'
_SOURCES = ('sina', 'baostock', 'eastmoney', 'db')
_NETWORK_SOURCES = ('sina', 'baostock', 'eastmoney')


class DataSourceError(RuntimeError):
    """数据源不可用或数据不满足完整性要求（如 --src db）。"""


def _source_fallback_chain(primary: str):
    """返回 [首选源, 其余数据源...]；'db' 为严格模式，不参与网络回退。"""
    if primary == 'db':
        return ['db']
    return [primary] + [s for s in _NETWORK_SOURCES if s != primary]


def set_primary_source(src: str) -> None:
    """设置首选数据源（'sina' / 'baostock' / 'eastmoney'）；非法值回退为 'sina'"""
    global _PRIMARY_SOURCE
    s = str(src).strip().lower()
    _PRIMARY_SOURCE = s if s in _SOURCES else 'sina'


def get_primary_source() -> str:
    """获取当前首选数据源"""
    return _PRIMARY_SOURCE


class DataFetcher:
    """
    股票数据获取器

    支持通过新浪财经获取A股数据，支持三种复权类型。
    提供历史行情、除权除息、实时行情、指数数据和股票列表等接口。

    使用示例:
        fetcher = DataFetcher()
        df = fetcher.get_stock_data('600036', '2024-01-01', '2024-12-31')
        df_qfq = fetcher.get_stock_data('600036', '2024-01-01', '2024-12-31', adjust='qfq')
        df_hfq = fetcher.get_stock_data('600036', '2024-01-01', '2024-12-31', adjust='hfq')
        dividends = fetcher.get_dividend_data('600036')
        all_adjust = fetcher.get_all_adjust_data('600036', '2024-01-01', '2024-12-31')
    """

    def __init__(self, log_level: int = logging.INFO):
        """
        初始化 DataFetcher

        Args:
            log_level: 日志级别，默认 INFO
        """
        self._setup_logging(log_level)
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self._check_dependencies()

    def _setup_logging(self, log_level: int) -> None:
        """配置日志格式和级别"""
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        logger.setLevel(log_level)

    def _check_dependencies(self) -> None:
        """检查依赖库是否可用"""
        try:
            import akshare
            logger.info("akshare 库加载成功，版本: %s", getattr(akshare, '__version__', 'unknown'))
        except ImportError:
            logger.info("akshare 未安装，将使用新浪/东方财富API")

        try:
            import yfinance
            logger.info("yfinance 库加载成功，版本: %s", getattr(yfinance, '__version__', 'unknown'))
        except ImportError:
            logger.info("yfinance 未安装，美股数据获取功能将不可用")

    def _is_a_stock(self, symbol: str) -> bool:
        """判断是否为A股代码（纯数字格式）"""
        return symbol.isdigit()

    def _normalize_a_stock_symbol(self, symbol: str) -> str:
        """标准化A股代码，去除可能的前缀如 sh/sz"""
        symbol = symbol.strip().upper()
        if symbol.startswith('SH') or symbol.startswith('SZ'):
            symbol = symbol[2:]
        return symbol

    def _get_sina_prefix(self, symbol: str) -> str:
        """
        获取新浪股票代码前缀

        Args:
            symbol: 纯数字股票代码

        Returns:
            str: 新浪前缀，如 'sh600036' 或 'sz000001'
        """
        symbol = self._normalize_a_stock_symbol(symbol)
        if symbol.startswith(('6', '9', '5')):
            return f'sh{symbol}'
        elif symbol.startswith(('0', '3', '2', '15', '16', '18')):
            return f'sz{symbol}'
        else:
            return f'sh{symbol}'

    # ==================== 核心数据获取 ====================

    def get_stock_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        source: str = 'sina',
        adjust: str = 'qfq'
    ) -> pd.DataFrame:
        """
        获取股票历史日线数据

        Args:
            symbol: 股票代码。A股如 '600036'、'000001'；美股如 'AAPL'、'MSFT'
            start_date: 起始日期，格式 'YYYY-MM-DD' 或 'YYYYMMDD'
            end_date: 结束日期，格式 'YYYY-MM-DD' 或 'YYYYMMDD'
            source: 数据源，默认 'sina'
            adjust: 复权类型。
                    - 'qfq': 前复权（默认），以最新价格为基准向前调整历史价格
                    - 'hfq': 后复权，以上市首日为基准向后调整后续价格
                    - '' 或 'None': 不复权，返回原始交易价格

        Returns:
            pd.DataFrame: 包含列 [date, open, high, low, close, volume]，
                          date 列为 datetime 类型且设为索引。
                          若获取失败返回空 DataFrame。

        Raises:
            ValueError: 当日期格式不正确时
        """
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)

        if start_date > end_date:
            raise ValueError(f"起始日期 {start_date} 不能晚于结束日期 {end_date}")

        logger.info("获取股票数据: symbol=%s, start=%s, end=%s, adjust=%s",
                     symbol, start_date, end_date, adjust)

        if self._is_a_stock(symbol):
            df = self._get_a_stock_data(symbol, start_date, end_date, adjust)
        else:
            df = self._get_us_stock_data(symbol, start_date, end_date)

        if df.empty:
            logger.warning("未获取到股票 %s 的数据，请检查代码和日期范围", symbol)
            return df

        src_used = df.attrs.get('source')
        df = self._standardize_dataframe(df, adjust)
        if src_used:
            df.attrs['source'] = src_used
        logger.info("成功获取 %s 数据，共 %d 条记录 (复权类型: %s)", symbol, len(df), adjust)
        return df

    def _get_a_stock_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> pd.DataFrame:
        """
        获取A股历史数据，支持复权

        数据流程:
        - `--src db`：只读 MySQL，要求「首条 ≤ start+5天 且 末条 ≥ end-1」，否则抛 DataSourceError；
        - 其它 `--src`：不读数据库，严格按首选网络通道获取（失败时按链回退）；
        - 新浪通道会获取复权因子并计算复权价格，并回写数据库。
        """
        raw_symbol = self._normalize_a_stock_symbol(symbol)
        primary = get_primary_source()

        # --src db：严格只读数据库；数据缺失/不完整时抛出 DataSourceError（不回退网络）
        if primary == 'db':
            df_db = self._get_a_stock_data_db(raw_symbol, start_date, end_date)
            df_db.attrs['source'] = 'db'
            return df_db

        # 其它 --src：严格走对应网络通道，不再优先读取数据库
        chain = _source_fallback_chain(primary)
        for idx, src in enumerate(chain):
            if src == 'sina':
                df = self._get_a_stock_data_sina(raw_symbol, start_date, end_date, adjust)
            elif src == 'baostock':
                df = self._get_a_stock_data_baostock(raw_symbol, start_date, end_date, adjust)
            else:
                df = self._get_a_stock_data_eastmoney(raw_symbol, start_date, end_date, adjust)
            if df is not None and not df.empty:
                df.attrs['source'] = src
                if idx > 0:
                    logger.info("已改用备用数据源 %s 获取 %s", src, symbol)
                return df
            if idx == 0:
                logger.warning("首选数据源 %s 获取 %s 失败，改用备用源 %s",
                               src, symbol, chain[1])

        return pd.DataFrame()

    def _get_a_stock_data_db(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从 MySQL 读取K线（--src db 专用）

        完整性要求：首条 ≤ start+5天 且 末条 ≥ end-1天。
        数据库读取失败或不满足要求时抛出 DataSourceError（由 CLI 报警退出）。
        """
        db_start = start_date[:4] + '-' + start_date[4:6] + '-' + start_date[6:8]
        db_end = end_date[:4] + '-' + end_date[4:6] + '-' + end_date[6:8]
        try:
            from core.db import fetch_kline
            db_rows = fetch_kline(symbol, db_start, db_end)
        except Exception as e:
            raise DataSourceError(f"数据库读取 {symbol} 失败: {e}") from e

        if not db_rows:
            raise DataSourceError(
                f"数据库无 {symbol} 的K线数据（请求区间 {db_start} ~ {db_end}）")

        df_db = pd.DataFrame(db_rows, columns=[
            'date', 'open', 'high', 'low', 'close', 'volume',
            'amount', 'amplitude', 'change_pct', 'change_val', 'turnover_rate'
        ])
        df_db['date'] = pd.to_datetime(df_db['date'])
        df_db = df_db.dropna(axis=1, how='all')
        if df_db.empty:
            raise DataSourceError(f"数据库 {symbol} 的K线数据为空（{db_start} ~ {db_end}）")

        db_first = df_db['date'].min()
        db_last = df_db['date'].max()
        start_dt = pd.to_datetime(db_start)
        end_dt = pd.to_datetime(db_end)
        margin = pd.Timedelta(days=5)
        if db_first > start_dt + margin or db_last < end_dt - pd.Timedelta(days=1):
            raise DataSourceError(
                f"数据库K线不完整: {db_first.date()} ~ {db_last.date()}，"
                f"需要覆盖 {db_start} ~ {db_end}"
                f"（首条 ≤ start+5天 且 末条 ≥ end-1）")

        logger.info("从数据库读取 %d 条 K 线记录: %s", len(df_db), symbol)
        return df_db

    def _get_a_stock_data_sina(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> pd.DataFrame:
        """从新浪 K 线 API 获取A股日线并复权（K线与复权因子并行请求）

        成功时返回 DataFrame，并回写数据库；获取失败返回空 DataFrame。
        """
        sina_code = self._get_sina_prefix(symbol)
        try:
            adjust_type = adjust if adjust != 'None' else ''
            need_factor = adjust_type in ('qfq', 'hfq')

            # K线与复权因子并行发起 HTTP 请求（self._session 非线程安全，
            # 每个请求用独立的 requests.Session）
            import concurrent.futures as _cf

            def _http_get(url, headers=None, timeout=15):
                s = requests.Session()
                s.headers.update(HEADERS)
                if headers:
                    s.headers.update(headers)
                r = s.get(url, timeout=timeout)
                return r

            kline_url = (
                f'https://quotes.sina.com.cn/cn/api/json_v2.php/'
                f'CN_MarketData.getKLineData?'
                f'symbol={sina_code}&scale=240&ma=no&datalen=1000'
            )
            factor_url = (
                f'https://finance.sina.com.cn/realstock/company/{sina_code}/{adjust_type}.js'
                if need_factor else None
            )

            logger.info("从新浪获取K线数据: %s%s", sina_code, ' (含复权因子)' if need_factor else '')
            with _cf.ThreadPoolExecutor(max_workers=2) as ex:
                fut_k = ex.submit(_http_get, kline_url)
                fut_f = ex.submit(_http_get, factor_url) if need_factor else None
                resp = fut_k.result()
                factor_resp = fut_f.result() if fut_f else None

            data = resp.json()

            if not data or not isinstance(data, list):
                logger.warning("新浪返回空数据: %s", symbol)
                return pd.DataFrame()

            # 构建DataFrame
            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['day'])
            df['open'] = pd.to_numeric(df['open'], errors='coerce')
            df['high'] = pd.to_numeric(df['high'], errors='coerce')
            df['low'] = pd.to_numeric(df['low'], errors='coerce')
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
            df = df.drop(columns=['day'])

            # 过滤日期范围
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)
            df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]

            if df.empty:
                logger.warning("新浪日期范围内无数据: %s", symbol)
                return pd.DataFrame()

            logger.info("新浪返回 %d 条原始K线记录 (不复权)", len(df))

            # 复权（复用已并行获取的因子响应）
            if need_factor and factor_resp is not None:
                factor_df = self._parse_sina_adjust_factor_text(symbol, factor_resp.text)
                if not factor_df.empty:
                    df = self._apply_adjust_factor(df, factor_df, adjust_type)
                    logger.info("已应用 %s 复权因子，共 %d 条记录", adjust_type, len(df))

            # 回写数据库
            try:
                from core.db import store_kline
                rows = []
                for _, row in df.iterrows():
                    rows.append((
                        symbol,
                        row['date'].strftime('%Y-%m-%d'),
                        float(row['open']), float(row['high']), float(row['low']),
                        float(row['close']), float(row['volume']),
                        float(row.get('amount', 0)) if pd.notna(row.get('amount')) else None,
                        float(row.get('amplitude', 0)) if pd.notna(row.get('amplitude')) else None,
                        float(row.get('change_pct', 0)) if pd.notna(row.get('change_pct')) else None,
                        float(row.get('change_val', 0)) if pd.notna(row.get('change_val')) else None,
                        float(row.get('turnover_rate', 0)) if pd.notna(row.get('turnover_rate')) else None,
                    ))
                if rows:
                    store_kline(symbol, rows)
            except Exception as e:
                logger.debug("K 线回写数据库跳过: %s", e)

            return df

        except requests.RequestException as e:
            logger.error("新浪网络请求失败: %s", str(e))
            return pd.DataFrame()
        except Exception as e:
            logger.error("新浪获取 %s 数据失败: %s", symbol, str(e))
            return pd.DataFrame()

    def _get_a_stock_data_baostock(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> pd.DataFrame:
        """从 Baostock 获取A股历史日线（新浪K线获取失败时的备用数据源）

        使用 baostock.query_history_k_data_plus，直接返回已复权价格：
            adjustflag: 'qfq' -> 2（前复权）、'hfq' -> 1（后复权）、其它 -> 3（不复权）

        登录在 DataFetcher 实例内复用（同一批量任务仅登录一次）。
        未安装 baostock 或获取失败时返回空 DataFrame。

        Returns:
            pd.DataFrame: 列 [date, open, high, low, close, volume, amount, change_pct, turnover_rate]
        """
        bs = self._baostock_login()
        if bs is None:
            return pd.DataFrame()

        # 新浪前缀 sh600036 -> Baostock 代码 sh.600036
        sina_code = self._get_sina_prefix(symbol)
        bs_code = f'{sina_code[:2]}.{sina_code[2:]}'

        s = f'{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}'
        e = f'{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}'
        adjustflag = {'qfq': '2', 'hfq': '1'}.get(adjust if adjust != 'None' else '', '3')

        try:
            fields = 'date,open,high,low,close,volume,amount,turn,pctChg'
            rs = bs.query_history_k_data_plus(
                bs_code, fields, start_date=s, end_date=e,
                frequency='d', adjustflag=adjustflag)
            if getattr(rs, 'error_code', '0') != '0':
                logger.error("Baostock 查询失败(%s): %s", symbol, getattr(rs, 'error_msg', ''))
                return pd.DataFrame()

            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                logger.warning("Baostock 返回空数据: %s", symbol)
                return pd.DataFrame()

            df = pd.DataFrame(rows, columns=fields.split(','))
            df = df.rename(columns={'pctChg': 'change_pct', 'turn': 'turnover_rate'})
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            for col in ['open', 'high', 'low', 'close', 'volume', 'amount',
                        'change_pct', 'turnover_rate']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            df = df.dropna(subset=['date'])

            start_dt = pd.to_datetime(s)
            end_dt = pd.to_datetime(e)
            df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]
            df = df.sort_values('date').reset_index(drop=True)
            logger.info("Baostock 获取 %s 共 %d 条记录 (adjustflag=%s)",
                        symbol, len(df), adjustflag)
            return df

        except Exception as ex:
            logger.error("Baostock 获取 %s 数据失败: %s", symbol, ex)
            return pd.DataFrame()

    def _get_a_stock_data_eastmoney(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = 'qfq'
    ) -> pd.DataFrame:
        """从东方财富 K 线 API 获取A股日线（支持前/后复权）

        接口: push2his.eastmoney.com/api/qt/stock/kline/get
        复权: fqt=1 前复权 / fqt=2 后复权 / fqt=0 不复权
        返回列: date, open, high, low, close, volume, amount, amplitude,
                change_pct, change, turnover_rate
        """
        sina_code = self._get_sina_prefix(symbol)
        market = '1' if sina_code.startswith('sh') else '0'
        secid = f'{market}.{sina_code[2:]}'
        fqt = {'qfq': '1', 'hfq': '2'}.get(adjust if adjust != 'None' else '', '0')

        url = (
            'https://push2his.eastmoney.com/api/qt/stock/kline/get?'
            f'secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b'
            '&fields1=f1,f2,f3,f4,f5,f6'
            '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
            f'&klt=101&fqt={fqt}&beg={start_date}&end={end_date}'
        )
        try:
            resp = self._session.get(
                url, timeout=15,
                headers={'Referer': 'https://quote.eastmoney.com/'})
            payload = resp.json()
        except Exception as ex:
            logger.error("东方财富获取 %s 数据失败: %s", symbol, ex)
            return pd.DataFrame()

        data = payload.get('data') or {}
        klines = data.get('klines') or []
        if not klines:
            logger.warning("东方财富返回空数据: %s", symbol)
            return pd.DataFrame()

        cols = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount',
                'amplitude', 'change_pct', 'change', 'turnover_rate']
        try:
            rows = [line.split(',') for line in klines]
            df = pd.DataFrame(rows, columns=cols)
        except Exception as ex:
            logger.error("东方财富解析 %s 数据失败: %s", symbol, ex)
            return pd.DataFrame()

        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        for col in cols[1:]:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['date'])

        start_dt = pd.to_datetime(start_date, format='%Y%m%d', errors='coerce')
        end_dt = pd.to_datetime(end_date, format='%Y%m%d', errors='coerce')
        if pd.notna(start_dt):
            df = df[df['date'] >= start_dt]
        if pd.notna(end_dt):
            df = df[df['date'] <= end_dt]
        df = df.sort_values('date').reset_index(drop=True)
        logger.info("东方财富获取 %s 共 %d 条记录 (fqt=%s)", symbol, len(df), fqt)
        return df

    def _baostock_login(self):
        """登录 Baostock（实例内复用，返回模块对象；未安装/登录失败返回 None）

        Baostock 的 socket 未设置超时，网络异常时可能永久阻塞；此处临时设置
        全局 socket 默认超时（15s），并在失败后缓存不可用状态，避免批量回测时
        逐只股票重复等待。
        """
        if getattr(self, '_bs_module', None) is not None:
            return self._bs_module
        if getattr(self, '_bs_unavailable', False):
            return None
        try:
            import baostock as bs
        except ImportError:
            logger.warning("未安装 baostock，无法使用备用数据源（可执行 pip install baostock）")
            self._bs_unavailable = True
            return None
        import contextlib
        import io
        import socket
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(15)
        try:
            # baostock.login() 会向 stdout 打印 "login success!"，此处屏蔽以保持 CLI 输出整洁
            with contextlib.redirect_stdout(io.StringIO()):
                lg = bs.login()
            if getattr(lg, 'error_code', '0') != '0':
                logger.error("Baostock 登录失败: %s", getattr(lg, 'error_msg', ''))
                self._bs_unavailable = True
                return None
            self._bs_module = bs
            return bs
        except Exception as e:
            logger.error("Baostock 登录异常: %s", e)
            self._bs_unavailable = True
            return None
        finally:
            socket.setdefaulttimeout(old_timeout)

    def _get_us_stock_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """通过 yfinance 获取美股历史数据"""
        try:
            import yfinance as yf

            logger.info("使用 yfinance 获取美股数据: %s", symbol)
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date)

            if df is None or df.empty:
                logger.warning("yfinance 返回空数据: %s", symbol)
                return pd.DataFrame()

            df = df.reset_index()
            logger.info("yfinance 返回 %d 条原始记录", len(df))
            return df

        except ImportError:
            logger.error("yfinance 未安装，无法获取美股数据")
            return pd.DataFrame()
        except Exception as e:
            logger.error("通过 yfinance 获取 %s 数据失败: %s", symbol, str(e))
            return pd.DataFrame()

    # ==================== 复权因子 ====================

    def _fetch_sina_adjust_factor_raw(self, symbol: str, adjust_type: str) -> str:
        """仅发起新浪复权因子 HTTP 请求，返回原始响应文本（用于与K线请求并行）"""
        sina_code = self._get_sina_prefix(symbol)
        url = f'https://finance.sina.com.cn/realstock/company/{sina_code}/{adjust_type}.js'
        resp = self._session.get(url, timeout=15)
        return resp.text

    def _parse_sina_adjust_factor_text(self, symbol: str, text: str) -> pd.DataFrame:
        """解析新浪复权因子原始响应文本，返回 [date, factor] DataFrame（date 为索引）"""
        match = re.search(r'=\s*(\{.*\})', text, re.DOTALL)
        if not match:
            logger.warning("无法解析复权因子数据: %s", symbol)
            return pd.DataFrame()

        data = json.loads(match.group(1))
        records = data.get('data', [])

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['d'])
        df['factor'] = pd.to_numeric(df['f'], errors='coerce')
        df = df.dropna(subset=['date', 'factor'])
        df = df.set_index('date')
        df = df.sort_index()
        return df

    def _get_sina_adjust_factor(
        self,
        symbol: str,
        adjust_type: str = 'qfq'
    ) -> pd.DataFrame:
        """
        从新浪财经获取复权因子

        新浪复权因子接口返回累乘因子，格式:
        var sh600036qfq = {"total":32, "data": [{"d":"2026-07-10", "f":"1.0000"}, ...]}

         前复权: 最新因子=1.0，历史因子逐渐增大，用于 price/factor 向下折算历史价格
         后复权: 最早因子=1.0，最新因子逐渐增大，用于 price*factor 向上折算后续价格

        Args:
            symbol: 纯数字股票代码
            adjust_type: 'qfq' 或 'hfq'

        Returns:
            pd.DataFrame: 包含 [date, factor] 列，date 为索引
        """
        sina_code = self._get_sina_prefix(symbol)

        try:
            logger.info("获取新浪复权因子: %s (%s)", sina_code, adjust_type)
            text = self._fetch_sina_adjust_factor_raw(symbol, adjust_type)
            df = self._parse_sina_adjust_factor_text(symbol, text)

            logger.info("获取复权因子成功: %s, 共 %d 个节点", symbol, len(df))
            return df

        except Exception as e:
            logger.error("获取复权因子失败: %s - %s", symbol, str(e))
            return pd.DataFrame()

    def _apply_adjust_factor(
        self,
        df: pd.DataFrame,
        factor_df: pd.DataFrame,
        adjust_type: str
    ) -> pd.DataFrame:
        """
        将复权因子应用到K线数据

        复权逻辑:
        - 前复权(qfq): 复权价格 = 原始价格 / 前复权因子
          前复权因子在最新日期为1.0，历史值逐渐增大，历史价格向下折算
        - 后复权(hfq): 复权价格 = 原始价格 × 后复权因子
          后复权因子在最早日期为1.0，最新值逐渐增大

        对于每个交易日，使用该日对应的复权因子（即该日之后最近一次除权对应的因子）

        Args:
            df: 原始K线数据 (date列非索引)
            factor_df: 复权因子数据 (date为索引)
            adjust_type: 复权类型

        Returns:
            pd.DataFrame: 应用复权因子后的数据
        """
        if factor_df.empty:
            return df

        # 为每个交易日匹配复权因子
        # 复权因子节点是除权除息日，该日及之后到下一个节点之前使用该因子
        factor_dates = factor_df.index.sort_values()

        # 为每个交易日找到对应的复权因子
        # 使用 merge_asof: 对于每个交易日，取 <= 该日期的最近因子日期
        df_sorted = df.sort_values('date').copy()
        factor_reset = factor_df.reset_index().rename(columns={'date': 'factor_date'})

        # 使用 merge_asof 进行前向匹配
        df_sorted = pd.merge_asof(
            df_sorted.sort_values('date'),
            factor_reset.sort_values('factor_date'),
            left_on='date',
            right_on='factor_date',
            direction='backward'
        )

        # 应用复权因子
        price_cols = ['open', 'high', 'low', 'close']
        for col in price_cols:
            if col in df_sorted.columns:
                if adjust_type == 'qfq':
                    df_sorted[col] = (df_sorted[col] / df_sorted['factor']).round(3)
                else:
                    df_sorted[col] = (df_sorted[col] * df_sorted['factor']).round(3)

        # 清理临时列
        df_sorted = df_sorted.drop(columns=['factor_date', 'factor', 'day'], errors='ignore')

        return df_sorted

    def get_adjust_factor(self, symbol: str) -> pd.DataFrame:
        """
        获取复权因子数据

        返回前复权和后复权两种因子，可用于在不同复权类型之间转换价格。
        - 前复权价格 = 不复权价格 × 前复权因子
        - 后复权价格 = 不复权价格 × 后复权因子

        Args:
            symbol: A股代码，如 '600036'

        Returns:
            pd.DataFrame: 包含 [date, factor_qfq, factor_hfq] 列，
                          date 为索引
        """
        logger.info("获取复权因子: %s", symbol)

        raw_symbol = self._normalize_a_stock_symbol(symbol)

        qfq_df = self._get_sina_adjust_factor(raw_symbol, 'qfq')
        hfq_df = self._get_sina_adjust_factor(raw_symbol, 'hfq')

        if qfq_df.empty and hfq_df.empty:
            return pd.DataFrame()

        result = pd.DataFrame()
        if not qfq_df.empty:
            result['factor_qfq'] = qfq_df['factor']
        if not hfq_df.empty:
            result['factor_hfq'] = hfq_df['factor']

        result = result.sort_index()
        logger.info("复权因子获取成功: %s, 共 %d 个节点", symbol, len(result))
        return result

    # ==================== 除权除息数据 ====================

    def get_dividend_data(self, symbol: str) -> pd.DataFrame:
        """
        获取A股除权除息历史数据（分红送配）

        从东方财富F10接口获取分红融资数据。

        Args:
            symbol: A股代码，如 '600036'

        Returns:
            pd.DataFrame: 包含列:
                - ex_date: 除权除息日
                - plan: 分红方案描述（如"10派10.03元"）
                - progress: 方案进度
                - record_date: 股权登记日
                - cash_date: 现金红利发放日
                - announce_date: 公告日期
                - cash_per_share: 每股现金分红（元，解析自方案描述）
                - stock_per_share: 每股送转股数（解析自方案描述）
                若获取失败返回空 DataFrame。
        """
        logger.info("获取除权除息数据: %s", symbol)

        raw_symbol = self._normalize_a_stock_symbol(symbol)

        # 优先从数据库读取
        try:
            from core.db import fetch_dividend_events
            db_rows = fetch_dividend_events(raw_symbol)
            if db_rows:
                df_db = pd.DataFrame(db_rows, columns=[
                    'ex_date', 'cash_per_share', 'stock_per_share', 'plan'
                ])
                df_db['ex_date'] = pd.to_datetime(df_db['ex_date'])
                logger.info("从数据库读取 %d 条除权事件: %s", len(df_db), symbol)
                return df_db
        except Exception:
            pass

        try:
            raw_symbol = self._normalize_a_stock_symbol(symbol)
            sina_code = self._get_sina_prefix(raw_symbol)
            code = sina_code.upper()

            # 使用东方财富F10分红融资接口
            url = (
                f'https://emweb.securities.eastmoney.com/'
                f'PC_HSF10/BonusFinancing/PageAjax?code={code}'
            )
            logger.info("从东方财富F10获取分红数据: %s", code)

            resp = self._session.get(url, timeout=15)
            data = resp.json()

            if not data or 'fhyx' not in data:
                logger.warning("%s 无分红数据", symbol)
                return pd.DataFrame()

            records = data['fhyx']
            if not records:
                logger.warning("%s 无分红记录", symbol)
                return pd.DataFrame()

            rows = []
            for r in records:
                # 只保留实施方案
                progress = r.get('ASSIGN_PROGRESS', '')
                if '实施' not in progress:
                    continue

                ex_date = r.get('EX_DIVIDEND_DATE', '')
                if not ex_date:
                    continue

                plan = r.get('IMPL_PLAN_PROFILE', '')

                # 解析方案描述，提取每股分红和送转
                cash_per_share, stock_per_share = self._parse_dividend_plan(plan)

                rows.append({
                    'ex_date': pd.to_datetime(ex_date[:10] if ' ' in str(ex_date) else ex_date),
                    'plan': plan,
                    'progress': progress,
                    'record_date': pd.to_datetime(r['EQUITY_RECORD_DATE'][:10])
                        if r.get('EQUITY_RECORD_DATE') else None,
                    'cash_date': pd.to_datetime(r['PAY_CASH_DATE'][:10])
                        if r.get('PAY_CASH_DATE') else None,
                    'announce_date': pd.to_datetime(r['NOTICE_DATE'][:10])
                        if r.get('NOTICE_DATE') else None,
                    'cash_per_share': cash_per_share,
                    'stock_per_share': stock_per_share,
                })

            df = pd.DataFrame(rows)
            df = df.sort_values('ex_date', ascending=False)
            logger.info("成功获取 %s 除权除息数据，共 %d 条记录", symbol, len(df))
            # 回写数据库
            try:
                from core.db import store_dividend_events
                store_dividend_events(raw_symbol, df)
            except Exception:
                pass
            return df

        except requests.RequestException as e:
            logger.error("网络请求失败: %s", str(e))
            return pd.DataFrame()
        except Exception as e:
            logger.error("获取 %s 除权除息数据失败: %s", symbol, str(e))
            return pd.DataFrame()

    def _parse_dividend_plan(self, plan: str):
        """
        解析分红方案描述，提取每股分红和送转股数

        支持的格式:
        - "10派10.03元" -> 每股现金分红 = 10.03/10 = 1.003
        - "10派20元" -> 每股现金分红 = 2.0
        - "10转5股派3元" -> 每股送转 = 0.5, 每股现金分红 = 0.3
        - "10送2股转3股派1元" -> 每股送转 = 0.5, 每股现金分红 = 0.1
        - "不分配不转增" -> 0, 0

        Args:
            plan: 方案描述字符串

        Returns:
            tuple: (每股现金分红, 每股送转股数)
        """
        if not plan or '不分配' in plan:
            return 0.0, 0.0

        cash = 0.0
        stock = 0.0

        # 提取"每10股派X元"
        cash_match = re.search(r'派\s*([\d.]+)\s*元', plan)
        if cash_match:
            cash = float(cash_match.group(1)) / 10.0

        # 提取"每10股送X股"
        send_match = re.search(r'送\s*([\d.]+)\s*股', plan)
        if send_match:
            stock += float(send_match.group(1)) / 10.0

        # 提取"每10股转增X股"
        transfer_match = re.search(r'转[增]?\s*([\d.]+)\s*股', plan)
        if transfer_match:
            stock += float(transfer_match.group(1)) / 10.0

        return cash, stock

    # ==================== 全复权对比数据 ====================

    def get_all_adjust_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """
        同时获取三种复权类型的数据，用于对比分析

        返回包含前复权(qfq)、后复权(hfq)和不复权(raw)三种价格的合并数据。

        Args:
            symbol: A股代码
            start_date: 起始日期
            end_date: 结束日期

        Returns:
            pd.DataFrame: 包含列:
                - date: 日期（索引）
                - open_qfq, high_qfq, low_qfq, close_qfq, volume: 前复权数据
                - open_hfq, high_hfq, low_hfq, close_hfq: 后复权数据
                - open_raw, high_raw, low_raw, close_raw: 不复权数据
        """
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)

        logger.info("获取全复权对比数据: %s, %s ~ %s", symbol, start_date, end_date)

        df_qfq = self.get_stock_data(symbol, start_date, end_date, adjust='qfq')
        df_hfq = self.get_stock_data(symbol, start_date, end_date, adjust='hfq')
        df_raw = self.get_stock_data(symbol, start_date, end_date, adjust='')

        if df_qfq.empty and df_hfq.empty and df_raw.empty:
            logger.warning("全复权数据获取失败: %s", symbol)
            return pd.DataFrame()

        result = pd.DataFrame()

        def rename_cols(dframe, suffix):
            if dframe is None or dframe.empty:
                return dframe
            return dframe.rename(columns={
                'open': f'open_{suffix}',
                'high': f'high_{suffix}',
                'low': f'low_{suffix}',
                'close': f'close_{suffix}',
            })

        if not df_qfq.empty:
            result = rename_cols(df_qfq, 'qfq')

        if not df_hfq.empty:
            hfq_cols = [c for c in ['open', 'high', 'low', 'close'] if c in df_hfq.columns]
            df_hfq_renamed = rename_cols(df_hfq[hfq_cols], 'hfq')
            if result.empty:
                result = df_hfq_renamed
            else:
                result = result.join(df_hfq_renamed, how='outer')

        if not df_raw.empty:
            raw_cols = [c for c in ['open', 'high', 'low', 'close'] if c in df_raw.columns]
            df_raw_renamed = rename_cols(df_raw[raw_cols], 'raw')
            if result.empty:
                result = df_raw_renamed
            else:
                result = result.join(df_raw_renamed, how='outer')

        if 'volume' not in result.columns and not df_qfq.empty and 'volume' in df_qfq.columns:
            result['volume'] = df_qfq['volume']

        result = result.sort_index()
        logger.info("全复权对比数据获取成功: %s, 共 %d 条记录", symbol, len(result))
        return result

    # ==================== 指数数据 ====================

    def get_index_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """
        获取指数历史数据

        支持的指数代码示例:
            - '000001' 或 'sh000001': 上证指数
            - '399001' 或 'sz399001': 深证成指
            - '399006' 或 'sz399006': 创业板指
            - '000688' 或 'sh000688': 科创50

        Args:
            symbol: 指数代码
            start_date: 起始日期
            end_date: 结束日期

        Returns:
            pd.DataFrame: 标准化后的指数数据
        """
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)

        logger.info("获取指数数据: symbol=%s, start=%s, end=%s",
                     symbol, start_date, end_date)

        raw_symbol = self._normalize_a_stock_symbol(symbol)
        # 优先使用东方财富指数接口（支持完整历史；新浪仅返回最近 1000 根）
        try:
            secid = f'0.{raw_symbol}' if raw_symbol.startswith('399') else f'1.{raw_symbol}'
            url = (
                'https://push2his.eastmoney.com/api/qt/stock/kline/get?'
                f'secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b'
                '&fields1=f1,f2,f3,f4,f5,f6'
                '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
                f'&klt=101&fqt=0&beg={start_date}&end={end_date}'
            )
            resp = self._session.get(
                url, timeout=20,
                headers={'Referer': 'https://quote.eastmoney.com/'})
            payload = resp.json()
            klines = (payload.get('data') or {}).get('klines') or []
            if klines:
                cols = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount',
                        'amplitude', 'change_pct', 'change', 'turnover_rate']
                df = pd.DataFrame([line.split(',') for line in klines], columns=cols)
                df['date'] = pd.to_datetime(df['date'], errors='coerce')
                for col in cols[1:]:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                df = df.dropna(subset=['date'])
                logger.info("东方财富指数数据返回 %d 条记录", len(df))
                return self._standardize_dataframe(df)
        except Exception as e:
            logger.warning("东方财富指数数据获取失败: %s", str(e))

        try:
            raw_symbol = self._normalize_a_stock_symbol(symbol)
            raw_lower = str(symbol).strip().lower()
            if raw_lower.startswith(('sh', 'sz')):
                sina_code = raw_lower
            elif raw_symbol.startswith('399'):
                sina_code = f'sz{raw_symbol}'
            else:
                sina_code = f'sh{raw_symbol}'

            url = (
                f'https://quotes.sina.com.cn/cn/api/json_v2.php/'
                f'CN_MarketData.getKLineData?'
                f'symbol={sina_code}&scale=240&ma=no&datalen=1000'
            )
            resp = self._session.get(url, timeout=15)
            data = resp.json()

            if not data or not isinstance(data, list):
                logger.warning("指数数据为空: %s", symbol)
                return pd.DataFrame()

            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['day'])
            df = df.drop(columns=['day'], errors='ignore')
            df['open'] = pd.to_numeric(df['open'], errors='coerce')
            df['high'] = pd.to_numeric(df['high'], errors='coerce')
            df['low'] = pd.to_numeric(df['low'], errors='coerce')
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce')

            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)
            df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]

            logger.info("指数数据返回 %d 条记录", len(df))
            return self._standardize_dataframe(df)

        except Exception as e:
            logger.error("获取指数数据失败: %s", str(e))

        # 备选：yfinance
        try:
            import yfinance as yf
            logger.info("尝试使用 yfinance 获取指数: %s", symbol)
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date)
            if df is not None and not df.empty:
                df = df.reset_index()
                return self._standardize_dataframe(df)
        except Exception:
            pass

        return pd.DataFrame()

    # ==================== 股票列表 ====================

    # ---- 热门股 HotScore 热度分 ----

    # 五大维度权重
    HOT_SCORE_WEIGHTS = {
        'activity': 0.5,      # 交易活跃度
        'capital': 0.3,       # 资金异动
        'strength': 0.1,      # 价格强势
        'attention': 0.08,    # 平台关注度
        'sentiment': 0.02,    # 舆情热度
    }

    def _fetch_sina_rank(self, sort_key: str, count: int = 50) -> pd.DataFrame:
        """从新浪行情中心获取按指定字段降序的前 count 只股票"""
        stocks = []
        try:
            pages = (count // 100) + 1
            for page in range(1, pages + 1):
                url = (
                    f'http://vip.stock.finance.sina.com.cn/quotes_service/'
                    f'api/json_v2.php/Market_Center.getHQNodeData?'
                    f'page={page}&num=100&sort={sort_key}&asc=0&'
                    f'node=hs_a&symbol=&_s_r_a=auto'
                )
                resp = self._session.get(url, timeout=15)
                data = json.loads(resp.text)
                for item in data:
                    code = item.get('code', '')
                    if not code:
                        continue
                    stocks.append({
                        'symbol': code,
                        'name': item.get('name', ''),
                        'price': float(item.get('trade', 0) or 0),
                        'change_pct': float(item.get('changepercent', 0) or 0),
                        'volume': float(item.get('volume', 0) or 0),
                        'amount': float(item.get('amount', 0) or 0),
                        'turnover_rate': float(item.get('turnoverratio', 0) or 0),
                        'high': float(item.get('high', 0) or 0),
                        'low': float(item.get('low', 0) or 0),
                        'settlement': float(item.get('settlement', 0) or 0),
                    })
        except Exception as e:
            logger.warning("获取新浪排行(%s)失败: %s", sort_key, e)
        df = pd.DataFrame(stocks)
        if not df.empty:
            df = df.drop_duplicates(subset=['symbol']).head(count).reset_index(drop=True)
        return df

    def _run_with_timeout(self, fn, timeout: int = 30):
        """在守护线程中运行函数，超时返回 None，避免 akshare 接口卡死"""
        import threading
        result = {}

        def _runner():
            try:
                result['value'] = fn()
            except Exception as e:
                result['error'] = e

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            logger.warning("数据获取超时(>%ds)，已跳过", timeout)
            return None
        return result.get('value')

    def _enrich_hot_data(self, symbols: List[str]) -> pd.DataFrame:
        """
        通过 akshare 补抓额外数据，返回以 symbol 为索引的富集 DataFrame。

        返回列: lhb_count(龙虎榜上榜次数), lhb_net_buy(龙虎榜净买额),
               lhb_inst_net(机构净买入), main_inflow_1d(今日主力净流入),
               main_inflow_3d(3日主力净流入), hot_rank(东财人气排名),
               volume_ratio(量比)
        """
        import akshare as ak
        syms = set(str(s) for s in symbols)
        enrich = pd.DataFrame(index=list(syms))
        for col in ['lhb_count', 'lhb_net_buy', 'lhb_inst_net',
                    'main_inflow_1d', 'main_inflow_3d', 'hot_rank', 'volume_ratio']:
            enrich[col] = np.nan

        # 1. 龙虎榜统计（近一月）—— 资金异动
        lhb = self._run_with_timeout(lambda: ak.stock_lhb_stock_statistic_em(symbol='近一月'), timeout=30)
        if lhb is not None and not lhb.empty and '代码' in lhb.columns:
            sub = lhb[lhb['代码'].astype(str).isin(syms)]
            if not sub.empty:
                for _, r in sub.iterrows():
                    code = str(r['代码']).zfill(6)
                    enrich.at[code, 'lhb_count'] = float(r.get('上榜次数', 0) or 0)
                    enrich.at[code, 'lhb_net_buy'] = float(r.get('龙虎榜净买额', 0) or 0)
                    enrich.at[code, 'lhb_inst_net'] = float(r.get('机构买入净额', 0) or 0)

        # 2. 主力资金流排名（今日 / 3日）—— 资金异动
        for indicator, col, field in [
            ('今日', 'main_inflow_1d', '今日主力净流入-净额'),
            ('3日', 'main_inflow_3d', '3日主力净流入-净额'),
        ]:
            ff = self._run_with_timeout(
                lambda ind=indicator: ak.stock_individual_fund_flow_rank(indicator=ind), timeout=40)
            if ff is not None and not ff.empty and '代码' in ff.columns and field in ff.columns:
                sub = ff[ff['代码'].astype(str).isin(syms)]
                if not sub.empty:
                    for _, r in sub.iterrows():
                        code = str(r['代码']).zfill(6)
                        enrich.at[code, col] = float(r.get(field, 0) or 0)

        # 3. 东财人气榜 —— 平台关注度 / 舆情
        hr = self._run_with_timeout(ak.stock_hot_rank_em, timeout=30)
        if hr is not None and not hr.empty and '代码' in hr.columns:
            sub = hr[hr['代码'].astype(str).isin(syms)]
            if not sub.empty:
                for _, r in sub.iterrows():
                    code = str(r['代码']).zfill(6)
                    enrich.at[code, 'hot_rank'] = float(r.get('当前排名', 0) or 0)

        # 4. 全市场快照（量比）—— 交易活跃度
        spot = self._run_with_timeout(ak.stock_zh_a_spot_em, timeout=60)
        if spot is not None and not spot.empty and '代码' in spot.columns and '量比' in spot.columns:
            sub = spot[spot['代码'].astype(str).isin(syms)]
            if not sub.empty:
                for _, r in sub.iterrows():
                    code = str(r['代码']).zfill(6)
                    enrich.at[code, 'volume_ratio'] = float(r.get('量比', 0) or 0)

        return enrich

    def _limit_up_flag(self, cand: pd.DataFrame) -> pd.Series:
        """涨停标记（近似），创业板/科创板 20%，其余 10%"""
        flag = []
        for code, chg in zip(cand['symbol'], cand['change_pct']):
            th = 19.5 if str(code).startswith(('300', '301', '688')) else 9.5
            flag.append(1.0 if chg >= th else 0.0)
        return pd.Series(flag, index=cand.index)

    def _compute_hot_score(self, cand: pd.DataFrame) -> pd.Series:
        """计算 HotScore 热度分（0~100）"""
        def pct(series):
            return series.rank(pct=True)

        scores = pd.DataFrame(index=cand.index)

        # 1. 交易活跃度：成交额 / 换手率 / 振幅 / 量比
        activity_parts = [pct(cand['amount']), pct(cand['turnover_rate'])]
        if cand['amplitude'].notna().any():
            activity_parts.append(pct(cand['amplitude']))
        if 'volume_ratio' in cand.columns and cand['volume_ratio'].notna().any():
            activity_parts.append(pct(cand['volume_ratio'].fillna(1.0)))
        scores['activity'] = pd.concat(activity_parts, axis=1).mean(axis=1)

        # 2. 资金异动：主力净流入(3日优先) / 龙虎榜上榜次数 / 龙虎榜净买额
        capital_parts = []
        for col in ['main_inflow_3d', 'main_inflow_1d']:
            if col in cand.columns and cand[col].notna().any():
                capital_parts.append(pct(cand[col].fillna(0.0)))
                break
        for col in ['lhb_count', 'lhb_net_buy']:
            if col in cand.columns and cand[col].notna().any():
                capital_parts.append(pct(cand[col].fillna(0.0)))
        if capital_parts:
            scores['capital'] = pd.concat(capital_parts, axis=1).mean(axis=1)
        else:
            scores['capital'] = np.nan

        # 3. 价格强势：今日涨幅 / 涨停标记
        scores['strength'] = pd.concat([pct(cand['change_pct']), self._limit_up_flag(cand)], axis=1).mean(axis=1)

        # 4. 平台关注度：东财人气排名（越低越热；未上榜按 0 处理）
        if 'hot_rank' in cand.columns and cand['hot_rank'].notna().any():
            scores['attention'] = cand['hot_rank'].apply(
                lambda r: max(0.0, 1.0 - (r - 1) / 100.0) if pd.notna(r) else 0.0)
        else:
            scores['attention'] = np.nan

        # 5. 舆情热度：以人气排名为代理（数据源有限），缺失时回退换手率
        if scores['attention'].notna().any():
            scores['sentiment'] = scores['attention']
        else:
            scores['sentiment'] = pct(cand['turnover_rate'])

        # 加权融合，缺失维度权重重新归一化
        weights = self.HOT_SCORE_WEIGHTS
        total = np.zeros(len(cand))
        wsum = np.zeros(len(cand))
        for dim, w in weights.items():
            s = scores[dim]
            has = s.notna().to_numpy()
            wsum += has * w
            total += s.fillna(0.0).to_numpy() * w
        hot_score = np.where(wsum > 0, total / np.where(wsum > 0, wsum, 1.0), 0.0)
        return pd.Series(hot_score * 100, index=cand.index)

    def get_hot_stocks(self, count: int = 20, sort_by: str = 'hotscore') -> pd.DataFrame:
        """
        获取热门股票列表（按 HotScore 热度分排序）

        流程：
        1. 分别按成交额、涨幅、换手率各取前 50，合并去重作为候选池
        2. 通过 akshare 补抓龙虎榜/主力资金/人气榜等数据
        3. 计算 HotScore 热度分（交易活跃度/资金异动/价格强势/平台关注度/舆情）
        4. 按热度分降序返回前 count 只

        Args:
            count: 返回数量
            sort_by: 排序依据，'hotscore'(热度分) 或 'amount'/'changepercent'/'turnoverratio'

        Returns:
            pd.DataFrame: 包含 symbol/name/hot_score/price/change_pct/amount 等列
        """
        logger.info("获取热门股票列表: count=%d, sort_by=%s", count, sort_by)

        # 1. 候选池：三项指标各取前 50 合并去重
        try:
            candidates = pd.concat([
                self._fetch_sina_rank('amount', 50),
                self._fetch_sina_rank('changepercent', 50),
                self._fetch_sina_rank('turnoverratio', 50),
            ], ignore_index=True)
            candidates = candidates.drop_duplicates(subset=['symbol']).reset_index(drop=True)
        except Exception as e:
            logger.error("构建热门候选池失败: %s", str(e))
            return pd.DataFrame()

        if candidates.empty:
            logger.warning("候选池为空")
            return candidates

        # 振幅 (振幅 = (最高-最低)/昨收 * 100)
        candidates['amplitude'] = np.where(
            candidates['settlement'] > 0,
            (candidates['high'] - candidates['low']) / candidates['settlement'] * 100,
            0.0,
        )

        # 2. 富集额外数据
        if sort_by == 'hotscore':
            try:
                enrich = self._enrich_hot_data(candidates['symbol'].tolist())
                for col in enrich.columns:
                    candidates[col] = candidates['symbol'].map(enrich[col])
                # 3. 计算热度分
                candidates['hot_score'] = self._compute_hot_score(candidates)
                candidates = candidates.sort_values('hot_score', ascending=False).head(count).reset_index(drop=True)
            except Exception as e:
                logger.error("计算热度分失败，回退到成交额排序: %s", str(e))
                candidates = candidates.sort_values('amount', ascending=False).head(count).reset_index(drop=True)
        else:
            sort_key = {'amount': 'amount', 'changepercent': 'change_pct',
                        'turnoverratio': 'turnover_rate'}.get(sort_by, 'amount')
            candidates = candidates.sort_values(sort_key, ascending=False).head(count).reset_index(drop=True)

        logger.info("获取到 %d 只热门股票", len(candidates))
        return candidates


    def get_stock_list(self, sort_key: str = 'changepercent', max_pages: int = 10) -> pd.DataFrame:
        """获取A股股票列表（遵守 --src 首选数据源，失败时自动改用另一通道）

        Returns:
            pd.DataFrame: 含 symbol/name/price/change_pct/volume/amount/
            turnover_rate/mktcap/pe 列，按 sort_key 降序
        """
        primary = get_primary_source()
        if primary == 'db':
            logger.warning("--src db 不提供股票列表接口")
            return pd.DataFrame()
        chain = _source_fallback_chain(primary)
        for idx, src in enumerate(chain):
            if src == 'sina':
                df = self._get_stock_list_sina(sort_key, max_pages)
            elif src == 'baostock':
                df = self._get_stock_list_baostock(sort_key)
            else:
                # eastmoney / db 无股票列表接口
                df = pd.DataFrame()
            if df is not None and len(df):
                if idx > 0:
                    logger.info("已改用备用数据源 %s 获取股票列表", src)
                return df
            if idx == 0:
                logger.warning("首选数据源 %s 获取股票列表失败，改用备用源 %s", src, chain[1])
        return pd.DataFrame()

    def _get_stock_list_baostock(self, sort_key: str = 'changepercent') -> pd.DataFrame:
        """从 Baostock 获取最近交易日全部A股快照

        使用 query_daily_history_k_AStock + query_stock_basic：
            - 价格/涨跌幅/换手率/成交额/PE(peTTM) 来自当日快照
            - 名称来自 query_stock_basic
            - Baostock 不提供总市值，mktcap 列置为 NaN（--nolowcap 在该源下不生效）
        """
        bs = self._baostock_login()
        if bs is None:
            return pd.DataFrame()

        # 最近交易日（今天可能是周末/节假日）
        day = datetime.now().strftime('%Y-%m-%d')
        try:
            start = (datetime.now() - pd.Timedelta(days=15)).strftime('%Y-%m-%d')
            rs_d = bs.query_trade_dates(start_date=start, end_date=day)
            last_td = None
            while rs_d.next():
                row = rs_d.get_row_data()
                if len(row) >= 2 and str(row[1]) == '1':
                    last_td = row[0]
            if last_td:
                day = last_td
        except Exception:
            pass

        try:
            rs = bs.query_daily_history_k_AStock(date=day)
            if getattr(rs, 'error_code', '0') != '0':
                logger.error("Baostock 股票列表查询失败(%s): %s", day,
                             getattr(rs, 'error_msg', ''))
                return pd.DataFrame()
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                logger.warning("Baostock %s 无股票快照数据", day)
                return pd.DataFrame()
            raw = pd.DataFrame(rows, columns=rs.fields)
        except Exception as e:
            logger.error("Baostock 获取股票列表失败: %s", e)
            return pd.DataFrame()

        # 代码 -> 名称
        name_map = {}
        try:
            rsb = bs.query_stock_basic()
            while rsb.next():
                r = rsb.get_row_data()
                if len(r) >= 6 and str(r[4]) == '1' and str(r[5]) == '1':
                    name_map[r[0]] = r[1]
        except Exception:
            pass

        def _num(v):
            try:
                return float(v)
            except Exception:
                return np.nan

        code = raw['code'].astype(str)
        out = pd.DataFrame({
            'symbol': code.str.split('.').str[-1],
            'name': code.map(lambda c: name_map.get(c, c)),
            'price': raw['close'].map(_num),
            'change_pct': raw['pctChg'].map(_num),
            'volume': raw['volume'].map(_num),
            'amount': raw['amount'].map(_num),
            'turnover_rate': raw['turn'].map(_num),
            'mktcap': np.nan,
            'pe': raw['peTTM'].map(_num),
        })
        if 'tradestatus' in raw.columns:
            out = out[raw['tradestatus'].astype(str) == '1']
        out = out[out['price'] > 0]

        sort_col = {'changepercent': 'change_pct', 'turnoverratio': 'turnover_rate',
                    'amount': 'amount', 'mktcap': 'mktcap', 'pe': 'pe'}.get(
                        sort_key, 'change_pct')
        out = out.sort_values(sort_col, ascending=False, na_position='last')
        out = out.drop_duplicates(subset=['symbol']).reset_index(drop=True)
        logger.info("Baostock 获取股票列表 %d 只（交易日 %s）", len(out), day)
        return out

    def _get_stock_list_sina(self, sort_key: str = 'changepercent', max_pages: int = 10) -> pd.DataFrame:
        """从新浪行情中心分页获取A股股票列表"""
        logger.info("获取A股股票列表: sort_key=%s, max_pages=%d", sort_key, max_pages)

        # 新浪接口市盈率字段为 per（而非 pe），排序/解析均需映射
        api_sort = 'per' if sort_key == 'pe' else sort_key
        all_stocks = []
        last_err = None
        for page in range(1, max_pages + 1):
            url = (
                f'http://vip.stock.finance.sina.com.cn/quotes_service/'
                f'api/json_v2.php/Market_Center.getHQNodeData?'
                f'page={page}&num=100&sort={api_sort}&asc=0&'
                f'node=hs_a&symbol=&_s_r_a=auto'
            )
            data = None
            # 单页失败重试；多次失败则保留已获取的部分数据，避免整批丢弃
            for attempt in range(3):
                try:
                    resp = self._session.get(url, timeout=15)
                    data = json.loads(resp.text)
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.3 * (attempt + 1))
            if data is None:
                logger.warning("获取股票列表第 %d 页失败: %s", page, last_err)
                break
            if not data:
                break

            for item in data:
                code = item.get('code', '')
                name = item.get('name', '')
                if not code:
                    continue
                try:
                    all_stocks.append({
                        'symbol': code,
                        'name': name,
                        'price': float(item.get('trade', 0) or 0),
                        'change_pct': float(item.get('changepercent', 0) or 0),
                        'volume': float(item.get('volume', 0) or 0),
                        'amount': float(item.get('amount', 0) or 0),
                        'turnover_rate': float(item.get('turnoverratio', 0) or 0),
                        'mktcap': float(item.get('mktcap', 0) or 0),
                        'pe': float(item.get('per', item.get('pe', 0)) or 0),
                    })
                except (TypeError, ValueError):
                    # 个别字段异常（如 per 为 '-'）时跳过该股票，避免整批失败
                    continue

        if not all_stocks:
            logger.error("获取股票列表失败: %s", last_err or '接口返回空数据')
            return pd.DataFrame()
        df = pd.DataFrame(all_stocks).drop_duplicates(subset=['symbol'])
        logger.info("成功获取 %d 只股票", len(df))
        return df

    def get_all_stock_codes(self) -> list:
        """获取全部 A 股代码列表（按代码排序分页拉取，供通配符展开使用）"""
        codes = []
        try:
            for page in range(1, 61):
                url = (
                    f'http://vip.stock.finance.sina.com.cn/quotes_service/'
                    f'api/json_v2.php/Market_Center.getHQNodeData?'
                    f'page={page}&num=100&sort=code&asc=1&'
                    f'node=hs_a&symbol=&_s_r_a=auto'
                )
                resp = self._session.get(url, timeout=15)
                data = json.loads(resp.text)
                if not data:
                    break
                for item in data:
                    code = item.get('code', '')
                    if code:
                        codes.append(code)
                if len(data) < 100:
                    break
        except Exception as e:
            logger.error("获取全量股票列表失败: %s", str(e))
        return codes

    # ==================== 实时行情 ====================

    def get_realtime_quote(self, symbol: str) -> dict:
        """
        获取股票实时行情

        Args:
            symbol: 股票代码

        Returns:
            dict: 包含实时行情数据的字典，获取失败返回空字典
        """
        logger.info("获取实时行情: %s", symbol)

        try:
            raw_symbol = self._normalize_a_stock_symbol(symbol)
            sina_code = self._get_sina_prefix(raw_symbol)

            if self._is_a_stock(symbol):
                # A股实时行情 - 使用新浪接口
                url = f'https://hq.sinajs.cn/list={sina_code}'
                headers = {**HEADERS, 'Referer': 'https://finance.sina.com.cn/'}
                resp = self._session.get(url, headers=headers, timeout=10)
                text = resp.text

                # 解析新浪行情数据
                # var hq_str_sh600036="招商银行,39.880,..."
                match = re.search(r'"([^"]+)"', text)
                if not match:
                    return {}

                parts = match.group(1).split(',')
                if len(parts) < 30:
                    return {}

                # 新浪原生行情时间：parts[30]=日期, parts[31]=时间
                sina_datetime = None
                try:
                    if len(parts) > 31 and parts[30] and parts[31]:
                        sina_datetime = datetime.strptime(
                            f"{parts[30]} {parts[31]}", "%Y-%m-%d %H:%M:%S")
                except Exception:
                    sina_datetime = None

                quote = {
                    'symbol': raw_symbol,
                    'name': parts[0],
                    'open': float(parts[1]) if parts[1] else 0,
                    'prev_close': float(parts[2]) if parts[2] else 0,
                    'price': float(parts[3]) if parts[3] else 0,
                    'high': float(parts[4]) if parts[4] else 0,
                    'low': float(parts[5]) if parts[5] else 0,
                    'volume': float(parts[8]) if parts[8] else 0,
                    'amount': float(parts[9]) if parts[9] else 0,
                    'change': round(float(parts[3]) - float(parts[2]), 3)
                        if parts[3] and parts[2] else 0,
                    'change_pct': round(
                        (float(parts[3]) - float(parts[2])) / float(parts[2]) * 100, 2
                    ) if parts[3] and parts[2] and float(parts[2]) != 0 else 0,
                    'datetime': sina_datetime.isoformat() if sina_datetime else None, # 新浪原生行情时间
                }
                logger.info("成功获取 %s 实时行情，价格: %.2f", raw_symbol, quote['price'])
                return quote
            else:
                # 美股实时行情
                try:
                    import yfinance as yf
                    ticker = yf.Ticker(symbol)
                    info = ticker.info
                    fast_info = ticker.fast_info
                    # 美股无新浪原生行情时间，保持 None（调用方按 datetime==今日 判断会跳过）
                    sina_datetime = None
                    quote = {
                        'symbol': symbol,
                        'name': info.get('shortName', info.get('longName', '')),
                        'price': float(getattr(fast_info, 'last_price', 0) or info.get('currentPrice', 0)),
                        'open': float(info.get('open', 0) or 0),
                        'high': float(info.get('dayHigh', 0) or 0),
                        'low': float(info.get('dayLow', 0) or 0),
                        'prev_close': float(info.get('previousClose', 0) or 0),
                        'volume': float(info.get('volume', 0) or 0),
                        'change': float(info.get('regularMarketChange', 0) or 0),
                        'change_pct': float((info.get('regularMarketChangePercent', 0) or 0)),
                        'datetime': sina_datetime.isoformat() if sina_datetime else None, # 新浪原生行情时间
                    }
                    return quote
                except Exception:
                    pass

        except Exception as e:
            logger.error("获取 %s 实时行情失败: %s", symbol, str(e))

        return {}

    # ==================== 标准化 ====================

    def _standardize_dataframe(self, df: pd.DataFrame, adjust: str = 'qfq') -> pd.DataFrame:
        """
        标准化 DataFrame 格式，统一列名和索引

        Args:
            df: 原始 DataFrame
            adjust: 复权类型

        Returns:
            pd.DataFrame: 标准化后的 DataFrame
        """
        column_mapping = {
            '日期': 'date', 'date': 'date', 'Date': 'date', 'day': 'date',
            '开盘': 'open', 'open': 'open', 'Open': 'open',
            '收盘': 'close', 'close': 'close', 'Close': 'close',
            '最高': 'high', 'high': 'high', 'High': 'high',
            '最低': 'low', 'low': 'low', 'Low': 'low',
            '成交量': 'volume', 'volume': 'volume', 'Volume': 'volume',
            '成交额': 'amount', 'amount': 'amount',
            '振幅': 'amplitude', 'amplitude': 'amplitude',
            '涨跌幅': 'change_pct', 'change_pct': 'change_pct',
            '涨跌额': 'change', 'change': 'change',
            '换手率': 'turnover_rate', 'turnover_rate': 'turnover_rate',
        }

        df = df.rename(columns=column_mapping)

        required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        available_cols = [col for col in required_cols if col in df.columns]

        if len(available_cols) < len(required_cols):
            missing = set(required_cols) - set(available_cols)
            logger.warning("缺少列: %s，可用列: %s", missing, df.columns.tolist())

        extra_cols = [c for c in ['amount', 'amplitude', 'change_pct', 'change', 'turnover_rate']
                      if c in df.columns]
        df = df[available_cols + extra_cols].copy()

        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            df = df.dropna(subset=['date'])
            df = df.set_index('date')

        for col in ['open', 'high', 'low', 'close', 'volume', 'amount', 'amplitude',
                    'change_pct', 'change', 'turnover_rate']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.sort_index()
        return df

    def _normalize_date(self, date_str: str) -> str:
        """
        标准化日期为 YYYYMMDD 格式

        Args:
            date_str: 日期字符串，支持 'YYYY-MM-DD' 或 'YYYYMMDD' 格式

        Returns:
            str: YYYYMMDD 格式的日期字符串

        Raises:
            ValueError: 日期格式不正确时
        """
        date_str = date_str.strip().replace('-', '')
        if len(date_str) != 8 or not date_str.isdigit():
            raise ValueError(f"日期格式不正确: {date_str}，期望格式 YYYY-MM-DD 或 YYYYMMDD")
        try:
            datetime.strptime(date_str, '%Y%m%d')
        except ValueError:
            raise ValueError(f"无效日期: {date_str}")
        return date_str

    def filter_by_volume(self, stock_list, min_volume):
        """按成交量过滤股票列表"""
        if isinstance(stock_list, pd.DataFrame):
            if 'volume' in stock_list.columns:
                return stock_list[stock_list['volume'] >= min_volume]
            if 'amount' in stock_list.columns:
                return stock_list[stock_list['amount'] >= min_volume]
        return stock_list
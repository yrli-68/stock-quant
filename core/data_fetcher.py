"""
股票数据获取模块

提供A股和美股数据的统一获取接口。
- A股数据优先使用 akshare 库
- 美股数据使用 yfinance 作为备选

Author: stock_quant
"""

import logging
from datetime import datetime
from typing import Optional, List

import pandas as pd

# 配置模块级日志
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DataFetcher:
    """
    股票数据获取器

    支持通过 akshare 获取A股数据，通过 yfinance 获取美股数据。
    提供历史行情、实时行情、指数数据和股票列表等接口。

    使用示例:
        fetcher = DataFetcher()
        df = fetcher.get_stock_data('600036', '2024-01-01', '2024-12-31')
        quote = fetcher.get_realtime_quote('600036')
    """

    def __init__(self, log_level: int = logging.INFO):
        """
        初始化 DataFetcher

        Args:
            log_level: 日志级别，默认 INFO
        """
        self._setup_logging(log_level)
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
            logger.warning("akshare 未安装，A股数据获取功能将不可用")

        try:
            import yfinance
            logger.info("yfinance 库加载成功，版本: %s", getattr(yfinance, '__version__', 'unknown'))
        except ImportError:
            logger.warning("yfinance 未安装，美股数据获取功能将不可用")

    def _is_a_stock(self, symbol: str) -> bool:
        """
        判断是否为A股代码（纯数字格式）

        Args:
            symbol: 股票代码

        Returns:
            bool: 是否为A股代码
        """
        return symbol.isdigit()

    def _normalize_a_stock_symbol(self, symbol: str) -> str:
        """
        标准化A股代码，去除可能的前缀如 sh/sz

        Args:
            symbol: 原始股票代码

        Returns:
            str: 纯数字代码
        """
        symbol = symbol.strip().upper()
        # 去除 sh/sz/SH/SZ 前缀
        if symbol.startswith('SH') or symbol.startswith('SZ'):
            symbol = symbol[2:]
        return symbol

    def get_stock_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        source: str = 'akshare'
    ) -> pd.DataFrame:
        """
        获取股票历史日线数据

        Args:
            symbol: 股票代码。A股如 '600036'、'000001'；美股如 'AAPL'、'MSFT'
            start_date: 起始日期，格式 'YYYY-MM-DD' 或 'YYYYMMDD'
            end_date: 结束日期，格式 'YYYY-MM-DD' 或 'YYYYMMDD'
            source: 数据源，A股默认 'akshare'，美股自动使用 'yfinance'

        Returns:
            pd.DataFrame: 包含列 [date, open, high, low, close, volume]，
                          date 列为 datetime 类型且设为索引。
                          若获取失败返回空 DataFrame。

        Raises:
            ValueError: 当日期格式不正确时
        """
        # 标准化日期格式
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)

        # 校验日期范围
        if start_date > end_date:
            raise ValueError(f"起始日期 {start_date} 不能晚于结束日期 {end_date}")

        logger.info("获取股票数据: symbol=%s, start=%s, end=%s, source=%s",
                     symbol, start_date, end_date, source)

        df = pd.DataFrame()

        if self._is_a_stock(symbol):
            # A股数据
            df = self._get_a_stock_data(symbol, start_date, end_date)
        else:
            # 美股数据（字母代码）
            df = self._get_us_stock_data(symbol, start_date, end_date)

        if df.empty:
            logger.warning("未获取到股票 %s 的数据，请检查代码和日期范围", symbol)
            return df

        # 统一列名和格式
        df = self._standardize_dataframe(df)
        logger.info("成功获取 %s 数据，共 %d 条记录", symbol, len(df))
        return df

    def _get_a_stock_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """
        通过 akshare 获取A股历史数据

        Args:
            symbol: A股代码（纯数字）
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            pd.DataFrame
        """
        try:
            import akshare as ak

            raw_symbol = self._normalize_a_stock_symbol(symbol)
            logger.info("使用 akshare 获取A股数据: %s", raw_symbol)

            df = ak.stock_zh_a_hist(
                symbol=raw_symbol,
                period='daily',
                start_date=start_date,
                end_date=end_date,
                adjust='qfq'  # 前复权
            )

            if df is None or df.empty:
                logger.warning("akshare 返回空数据: %s", symbol)
                return pd.DataFrame()

            logger.info("akshare 返回 %d 条原始记录", len(df))
            return df

        except ImportError:
            logger.error("akshare 未安装，无法获取A股数据")
            return pd.DataFrame()
        except Exception as e:
            logger.error("通过 akshare 获取 %s 数据失败: %s", symbol, str(e))
            return pd.DataFrame()

    def _get_us_stock_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """
        通过 yfinance 获取美股历史数据

        Args:
            symbol: 美股代码，如 'AAPL'
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            pd.DataFrame
        """
        try:
            import yfinance as yf

            logger.info("使用 yfinance 获取美股数据: %s", symbol)

            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date)

            if df is None or df.empty:
                logger.warning("yfinance 返回空数据: %s", symbol)
                return pd.DataFrame()

            # 重置索引，将日期变为列
            df = df.reset_index()
            logger.info("yfinance 返回 %d 条原始记录", len(df))
            return df

        except ImportError:
            logger.error("yfinance 未安装，无法获取美股数据")
            return pd.DataFrame()
        except Exception as e:
            logger.error("通过 yfinance 获取 %s 数据失败: %s", symbol, str(e))
            return pd.DataFrame()

    def _standardize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        标准化 DataFrame 格式，统一列名和索引

        将不同数据源的列名映射为统一的 [date, open, high, low, close, volume]，
        并将 date 列转为 datetime 类型设为索引。

        Args:
            df: 原始 DataFrame

        Returns:
            pd.DataFrame: 标准化后的 DataFrame
        """
        # 定义列名映射规则（忽略大小写）
        column_mapping = {
            '日期': 'date', 'date': 'date', 'Date': 'date',
            '开盘': 'open', 'open': 'open', 'Open': 'open',
            '收盘': 'close', 'close': 'close', 'Close': 'close',
            '最高': 'high', 'high': 'high', 'High': 'high',
            '最低': 'low', 'low': 'low', 'Low': 'low',
            '成交量': 'volume', 'volume': 'volume', 'Volume': 'volume',
        }

        # 重命名列
        df = df.rename(columns=column_mapping)

        # 确保所需的列存在
        required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        available_cols = [col for col in required_cols if col in df.columns]

        if len(available_cols) < len(required_cols):
            missing = set(required_cols) - set(available_cols)
            logger.warning("缺少列: %s，可用列: %s", missing, df.columns.tolist())

        df = df[available_cols].copy()

        # 转换 date 列为 datetime 类型
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            # 删除无效日期行
            df = df.dropna(subset=['date'])
            # 设置 date 为索引
            df = df.set_index('date')

        # 确保数值列为 float 类型
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 按日期排序
        df = df.sort_index()

        return df

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
            - '^GSPC': 标普500（通过 yfinance）

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

        df = pd.DataFrame()

        try:
            import akshare as ak

            raw_symbol = self._normalize_a_stock_symbol(symbol)
            logger.info("使用 akshare 获取指数数据: %s", raw_symbol)

            df = ak.stock_zh_index_daily(symbol=f"sh{raw_symbol}" if raw_symbol.startswith(('0', '6')) else f"sz{raw_symbol}")

            if df is None or df.empty:
                logger.warning("akshare 指数数据为空: %s", symbol)
                return pd.DataFrame()

            # 过滤日期范围
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'], errors='coerce')
                mask = (df['date'] >= start_date) & (df['date'] <= end_date)
                df = df[mask]
            elif df.index.name == 'date' or 'date' in [c.lower() for c in df.index.names]:
                df = df.reset_index()
                if 'date' in df.columns:
                    df['date'] = pd.to_datetime(df['date'], errors='coerce')
                mask = (df['date'] >= start_date) & (df['date'] <= end_date)
                df = df[mask]

            logger.info("akshare 指数数据返回 %d 条记录", len(df))
            return self._standardize_dataframe(df)

        except ImportError:
            logger.error("akshare 未安装")
        except Exception as e:
            logger.error("获取指数数据失败: %s", str(e))

        # 备选：尝试使用 yfinance 获取（如美股指数）
        try:
            import yfinance as yf
            logger.info("尝试使用 yfinance 获取指数: %s", symbol)
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date)
            if df is not None and not df.empty:
                df = df.reset_index()
                logger.info("yfinance 指数数据返回 %d 条记录", len(df))
                return self._standardize_dataframe(df)
        except ImportError:
            pass
        except Exception as e:
            logger.error("yfinance 获取指数失败: %s", str(e))

        return pd.DataFrame()

    def get_stock_list(self) -> pd.DataFrame:
        """
        获取A股股票列表

        Returns:
            pd.DataFrame: 包含代码、名称、行业等信息的股票列表
        """
        logger.info("获取A股股票列表")

        try:
            import akshare as ak

            # 获取沪深A股实时行情数据作为股票列表
            df = ak.stock_zh_a_spot_em()

            if df is None or df.empty:
                logger.warning("未获取到股票列表")
                return pd.DataFrame()

            # 选择关键列并重命名
            column_map = {
                '代码': 'symbol',
                '名称': 'name',
                '最新价': 'price',
                '涨跌幅': 'change_pct',
                '涨跌额': 'change',
                '成交量': 'volume',
                '成交额': 'amount',
                '振幅': 'amplitude',
                '最高': 'high',
                '最低': 'low',
                '今开': 'open',
                '昨收': 'prev_close',
                '量比': 'volume_ratio',
                '换手率': 'turnover_rate',
                '市盈率-动态': 'pe',
                '市净率': 'pb',
                '总市值': 'total_market_cap',
                '流通市值': 'circulating_market_cap',
            }

            # 只保留存在的列
            existing_cols = {k: v for k, v in column_map.items() if k in df.columns}
            df = df[list(existing_cols.keys())].rename(columns=existing_cols)

            logger.info("成功获取 %d 只股票", len(df))
            return df

        except ImportError:
            logger.error("akshare 未安装，无法获取股票列表")
            return pd.DataFrame()
        except Exception as e:
            logger.error("获取股票列表失败: %s", str(e))
            return pd.DataFrame()

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
            import akshare as ak

            raw_symbol = self._normalize_a_stock_symbol(symbol)

            if self._is_a_stock(symbol):
                # A股实时行情
                df = ak.stock_zh_a_spot_em()
                if df is not None and not df.empty:
                    row = df[df['代码'] == raw_symbol]
                    if not row.empty:
                        row = row.iloc[0]
                        quote = {
                            'symbol': raw_symbol,
                            'name': row.get('名称', ''),
                            'price': float(row.get('最新价', 0)),
                            'open': float(row.get('今开', 0)),
                            'high': float(row.get('最高', 0)),
                            'low': float(row.get('最低', 0)),
                            'prev_close': float(row.get('昨收', 0)),
                            'volume': float(row.get('成交量', 0)),
                            'amount': float(row.get('成交额', 0)),
                            'change': float(row.get('涨跌额', 0)),
                            'change_pct': float(row.get('涨跌幅', 0)),
                            'turnover_rate': float(row.get('换手率', 0)),
                            'timestamp': datetime.now().isoformat(),
                        }
                        logger.info("成功获取 %s 实时行情，价格: %.2f", raw_symbol, quote['price'])
                        return quote
                    else:
                        logger.warning("未找到股票 %s 的实时行情", raw_symbol)
            else:
                # 美股实时行情
                try:
                    import yfinance as yf
                    ticker = yf.Ticker(symbol)
                    info = ticker.info
                    fast_info = ticker.fast_info
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
                        'timestamp': datetime.now().isoformat(),
                    }
                    logger.info("成功获取 %s 实时行情，价格: %.2f", symbol, quote['price'])
                    return quote
                except ImportError:
                    logger.error("yfinance 未安装，无法获取美股实时行情")
                except Exception as e:
                    logger.error("yfinance 获取 %s 实时行情失败: %s", symbol, str(e))

        except ImportError:
            logger.error("akshare 未安装")
        except Exception as e:
            logger.error("获取 %s 实时行情失败: %s", symbol, str(e))

        return {}

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
        """按成交量过滤股票列表（占位实现，实际筛选在 get_stock_data 时进行）"""
        # 简单实现：如果stock_list是DataFrame且有volume相关列，则过滤
        if isinstance(stock_list, pd.DataFrame):
            if 'volume' in stock_list.columns:
                return stock_list[stock_list['volume'] >= min_volume]
            if '成交额' in stock_list.columns:
                return stock_list[stock_list['成交额'] >= min_volume]
        return stock_list
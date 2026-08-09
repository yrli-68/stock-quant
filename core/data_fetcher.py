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
from datetime import datetime
from typing import Optional, List

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
        if symbol.startswith(('6', '9')):
            return f'sh{symbol}'
        elif symbol.startswith(('0', '3', '2')):
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

        df = self._standardize_dataframe(df, adjust)
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
        1. 从新浪财经获取不复权K线数据
        2. 如果需要复权，从新浪获取复权因子并计算复权价格

        Args:
            symbol: A股代码
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD
            adjust: 复权类型

        Returns:
            pd.DataFrame: 原始数据（未标准化）
        """
        raw_symbol = self._normalize_a_stock_symbol(symbol)
        sina_code = self._get_sina_prefix(raw_symbol)

        try:
            # 第一步：获取不复权K线数据
            datalen = 1000  # 最多获取1000条
            url = (
                f'https://quotes.sina.com.cn/cn/api/json_v2.php/'
                f'CN_MarketData.getKLineData?'
                f'symbol={sina_code}&scale=240&ma=no&datalen={datalen}'
            )
            logger.info("从新浪获取K线数据: %s", sina_code)

            resp = self._session.get(url, timeout=15)
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
            df = df.drop(columns=['day'])  # 移除原始day列，避免与date冲突

            # 过滤日期范围
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)
            df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]

            if df.empty:
                logger.warning("日期范围内无数据: %s", symbol)
                return df

            logger.info("新浪返回 %d 条原始K线记录 (不复权)", len(df))

            # 第二步：如果需要复权，获取复权因子并计算
            adjust_type = adjust if adjust != 'None' else ''
            if adjust_type in ('qfq', 'hfq'):
                factor_df = self._get_sina_adjust_factor(raw_symbol, adjust_type)
                if not factor_df.empty:
                    df = self._apply_adjust_factor(df, factor_df, adjust_type)
                    logger.info("已应用 %s 复权因子，共 %d 条记录", adjust_type, len(df))

            return df

        except requests.RequestException as e:
            logger.error("网络请求失败: %s", str(e))
            return pd.DataFrame()
        except Exception as e:
            logger.error("获取 %s 数据失败: %s", symbol, str(e))
            return pd.DataFrame()

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

    def _get_sina_adjust_factor(
        self,
        symbol: str,
        adjust_type: str = 'qfq'
    ) -> pd.DataFrame:
        """
        从新浪财经获取复权因子

        新浪复权因子接口返回累乘因子，格式:
        var sh600036qfq = {"total":32, "data": [{"d":"2026-07-10", "f":"1.0000"}, ...]}

        前复权: 最新因子=1.0，历史因子逐渐增大
        后复权: 最早因子=1.0，最新因子逐渐增大

        Args:
            symbol: 纯数字股票代码
            adjust_type: 'qfq' 或 'hfq'

        Returns:
            pd.DataFrame: 包含 [date, factor] 列，date 为索引
        """
        sina_code = self._get_sina_prefix(symbol)
        url = f'https://finance.sina.com.cn/realstock/company/{sina_code}/{adjust_type}.js'

        try:
            logger.info("获取新浪复权因子: %s (%s)", sina_code, adjust_type)
            resp = self._session.get(url, timeout=15)

            # 解析 JS 格式: var sh600036qfq = {...}
            text = resp.text
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
        - 前复权(qfq): 复权价格 = 原始价格 × 前复权因子
          前复权因子在最新日期为1.0，历史值逐渐增大
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

        try:
            raw_symbol = self._normalize_a_stock_symbol(symbol)
            sina_code = self._get_sina_prefix(raw_symbol)

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

    def get_stock_list(self) -> pd.DataFrame:
        """
        获取A股股票列表

        Returns:
            pd.DataFrame: 包含代码、名称等信息的股票列表
        """
        logger.info("获取A股股票列表")

        all_stocks = []
        try:
            for page in range(1, 11):
                url = (
                    f'http://vip.stock.finance.sina.com.cn/quotes_service/'
                    f'api/json_v2.php/Market_Center.getHQNodeData?'
                    f'page={page}&num=100&sort=changepercent&asc=0&'
                    f'node=hs_a&symbol=&_s_r_a=auto'
                )
                resp = self._session.get(url, timeout=15)
                data = json.loads(resp.text)

                for item in data:
                    code = item.get('code', '')
                    name = item.get('name', '')
                    if code:
                        all_stocks.append({
                            'symbol': code,
                            'name': name,
                            'price': float(item.get('trade', 0) or 0),
                            'change_pct': float(item.get('changepercent', 0) or 0),
                            'volume': float(item.get('volume', 0) or 0),
                            'amount': float(item.get('amount', 0) or 0),
                            'turnover_rate': float(item.get('turnoverratio', 0) or 0),
                        })

            df = pd.DataFrame(all_stocks)
            # 去重
            df = df.drop_duplicates(subset=['symbol'])
            logger.info("成功获取 %d 只股票", len(df))
            return df

        except Exception as e:
            logger.error("获取股票列表失败: %s", str(e))
            return pd.DataFrame()

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
                    'timestamp': datetime.now().isoformat(),
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
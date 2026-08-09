"""
价值因子策略

基于 PE-TTM、PB、PS、PCF、股息率、EY 等估值指标的历史分位数，
判断当前估值高低并生成买卖信号。

支持四类标的的差异化因子权重：
 - 宽基指数：PE-TTM、PB、股息率、股权风险溢价
 - 周期/金融：PB、ROE、股息率（PE失真）
 - 成长赛道：PS、PEG、PCF（常亏损，PE无效）
 - 红利类：股息率、PE
"""

import logging
import numpy as np
import pandas as pd
import warnings
from core.strategy import Strategy

logger = logging.getLogger(__name__)
warnings.filterwarnings('ignore')


class ValueFactorStrategy(Strategy):
    """
    价值因子策略

    获取历史估值数据，计算近5年分位数，根据估值高低生成买卖信号。

    Attributes:
        stock_type: 标的类型 (broad_index/cyclical/growth/dividend/auto)
        lookback_years: 分位数计算回溯年数
        buy_threshold: 买入阈值（分位数低于此值视为低估）
        sell_threshold: 卖出阈值（分位数高于此值视为高估）
    """

    STOCK_TYPES = {
        'broad_index': {
            'name': '宽基指数',
            'factors': ['pe_ttm', 'pb'],
            'weights': [0.55, 0.45],
        },
        'cyclical': {
            'name': '周期/金融',
            'factors': ['pb', 'dividend_yield'],
            'weights': [0.60, 0.40],
        },
        'growth': {
            'name': '成长赛道',
            'factors': ['ps', 'pcf'],
            'weights': [0.55, 0.45],
        },
        'dividend': {
            'name': '红利/普通股',
            'factors': ['pe_ttm', 'pb', 'ps'],
            'weights': [0.40, 0.35, 0.25],
        },
    }

    # 宽基指数代码
    BROAD_INDEX_CODES = {
        '000300', '000905', '000016', '000688', '000852', '000001',
        '399006', '399005', '399001', '000903', '000922',
    }
    # 成长类代码/板块
    GROWTH_PREFIXES = ('300', '301', '688')
    # 周期类关键词（名称匹配）
    CYCLICAL_NAMES = ('银行', '地产', '钢铁', '煤炭', '有色', '化工', '建材', '券商')

    def _classify_stock(self, symbol, df):
        """根据代码自动判断标的类型"""
        if symbol in self.BROAD_INDEX_CODES:
            return 'broad_index'
        if symbol.startswith(self.GROWTH_PREFIXES):
            return 'growth'
        return 'dividend'

    def __init__(self, stock_type='auto', lookback_years=5,
                 buy_threshold=0.55, sell_threshold=0.45,
                 name='ValueFactor'):
        super().__init__(name=name)
        self.stock_type = stock_type
        self.lookback_years = lookback_years
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self._valuation_cache = {}

    def _fetch_valuation(self, symbol, stock_type):
        """获取估值历史数据"""
        if symbol in self._valuation_cache:
            return self._valuation_cache[symbol]

        try:
            import akshare as ak

            if stock_type == 'broad_index':
                df = self._fetch_index_valuation(symbol)
            else:
                df = self._fetch_stock_valuation(symbol)
            self._valuation_cache[symbol] = df
            return df
        except Exception as e:
            logger.warning("获取 %s 估值数据失败: %s", symbol, str(e))
            return None

    def _fetch_index_valuation(self, symbol):
        """获取指数 PE/PB/股息率 历史"""
        import akshare as ak

        # 尝试乐咕乐股指数PE
        index_name_map = {
            '000300': '沪深300', '000016': '上证50', '000905': '中证500',
            '399006': '创业板指', '000688': '科创50', '000852': '中证1000',
            '000001': '上证指数', '399005': '中小100',
        }
        index_name = index_name_map.get(symbol, '沪深300')

        df_pe = None
        df_pb = None
        try:
            df_pe = ak.stock_index_pe_lg(symbol=index_name)
            df_pe = df_pe.rename(columns={'日期': 'date', '滚动市盈率(TTM)': 'pe_ttm'})
            df_pe['date'] = pd.to_datetime(df_pe['date'])
            df_pe = df_pe.set_index('date')[['pe_ttm']]
        except Exception:
            pass

        try:
            df_pb = ak.stock_index_pb_lg(symbol=index_name)
            df_pb = df_pb.rename(columns={'日期': 'date', '市净率': 'pb'})
            df_pb['date'] = pd.to_datetime(df_pb['date'])
            df_pb = df_pb.set_index('date')[['pb']]
        except Exception:
            pass

        if df_pe is not None and df_pb is not None:
            df = df_pe.join(df_pb, how='outer')
        elif df_pe is not None:
            df = df_pe
        elif df_pb is not None:
            df = df_pb
        else:
            return None

        if 'pe_ttm' in df.columns:
            df['ey'] = 1.0 / df['pe_ttm'].replace(0, np.nan)
            df['dividend_yield'] = np.nan
        return df

    def _fetch_stock_valuation(self, symbol):
        """获取个股 PE-TTM/PB/PS/PCF 历史"""
        import akshare as ak

        # 主数据源：东方财富估值分析
        try:
            df = ak.stock_value_em(symbol=symbol)
            if df is not None and not df.empty:
                col_map = {
                    '数据日期': 'date',
                    'PE(TTM)': 'pe_ttm',
                    '市净率': 'pb',
                    '市销率': 'ps',
                    '市现率': 'pcf',
                }
                rename = {k: v for k, v in col_map.items() if k in df.columns}
                df = df.rename(columns=rename)
                cols = ['date'] + [v for v in rename.values() if v != 'date']
                df = df[cols].copy()
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date')
                if 'pe_ttm' in df.columns:
                    df['ey'] = 1.0 / df['pe_ttm'].replace(0, np.nan)
                df['dividend_yield'] = np.nan
                return df
        except Exception:
            pass

        # 备选：百度估值（支持 ETF）
        try:
            pe_df = ak.stock_zh_valuation_baidu(symbol=symbol, indicator='市盈率(TTM)', period='全部')
            pb_df = ak.stock_zh_valuation_baidu(symbol=symbol, indicator='市净率', period='全部')
            if pe_df is not None:
                pe_df = pe_df.rename(columns={'date': 'date', 'value': 'pe_ttm'})
                pe_df['date'] = pd.to_datetime(pe_df['date'])
                pe_df = pe_df.set_index('date')[['pe_ttm']]
            if pb_df is not None:
                pb_df = pb_df.rename(columns={'date': 'date', 'value': 'pb'})
                pb_df['date'] = pd.to_datetime(pb_df['date'])
                pb_df = pb_df.set_index('date')[['pb']]
            if pe_df is not None and pb_df is not None:
                df = pe_df.join(pb_df, how='outer')
            elif pe_df is not None:
                df = pe_df
            elif pb_df is not None:
                df = pb_df
            else:
                return None
            if 'pe_ttm' in df.columns:
                df['ey'] = 1.0 / df['pe_ttm'].replace(0, np.nan)
            df['dividend_yield'] = np.nan
            return df
        except Exception:
            return None

    def _score_to_signal(self, score):
        """将综合评分转换为信号（分数越高越值得买）"""
        if score >= self.buy_threshold:
            return 1
        elif score <= self.sell_threshold:
            return -1
        else:
            return 0

    def generate_signals(self, df):
        """
        生成价值因子信号

        Args:
            df: 行情数据 DataFrame，需包含 'close' 列

        Returns:
            pd.Series: 交易信号序列 (1/0/-1)
        """
        symbol = df.attrs.get('symbol', 'unknown')
        if self.stock_type == 'auto':
            stock_type = self._classify_stock(symbol, df)
        else:
            stock_type = self.stock_type

        type_cfg = self.STOCK_TYPES.get(stock_type, self.STOCK_TYPES['broad_index'])

        val_df = self._fetch_valuation(symbol, stock_type)
        if val_df is None or val_df.empty:
            return pd.Series(0, index=df.index)

        factors = type_cfg['factors']
        weights = type_cfg['weights']

        # 为行情数据的每个日期计算估值分位数和信号
        signals = pd.Series(0.0, index=df.index)
        scores = pd.Series(np.nan, index=df.index)

        for i, date in enumerate(df.index):
            if date not in val_df.index:
                continue

            hist_val = val_df.loc[:date]
            if len(hist_val) < 60:
                continue

            percentile_scores = []
            current_vals = val_df.loc[date]

            for factor, weight in zip(factors, weights):
                if factor not in hist_val.columns:
                    continue
                series = hist_val[factor].dropna()
                if len(series) < 60:
                    continue
                current = current_vals[factor]
                if pd.isna(current) or current <= 0:
                    continue
                rank = (series < current).sum()
                pct = rank / len(series)

                # PE/PB/PS/PCF: 高分位=高估值=差，反转
                # EY/股息率: 高分位=高收益=好，不反转
                if factor in ('pe_ttm', 'pb', 'ps', 'pcf'):
                    pct = 1.0 - pct

                percentile_scores.append((pct, weight))

            if not percentile_scores:
                continue

            total_weight = sum(w for _, w in percentile_scores)
            if total_weight == 0:
                continue

            score = sum(pct * w / total_weight for pct, w in percentile_scores)
            scores.iloc[i] = score
            signals.iloc[i] = self._score_to_signal(score)

        signals = signals.astype(int)
        signals.attrs['stock_type'] = type_cfg['name']
        signals.attrs['scores'] = scores

        return signals

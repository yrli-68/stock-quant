"""
质量因子策略

基于财务质量指标的历史分位数，评估公司盈利质量、现金流质量和财务健康度。

核心指标：
 - 盈利能力：ROE（净资产收益率）、净利率、毛利率
 - 现金流质量：经营现金流/净利润比率
 - 财务健康：资产负债率（反向）
 - 盈利稳定性：利润波动率（反向）

采用历史纵向对比（同一股票的历史分位数），各指标加权打分生成信号。
"""

import logging
import numpy as np
import pandas as pd
from core.strategy import Strategy

logger = logging.getLogger(__name__)


class QualityFactorStrategy(Strategy):
    """
    质量因子策略

    获取历史财务指标，计算各期在同股票历史上的分位数，加权打分。

    Attributes:
        lookback_quarters: 回溯季度数（用于分位数计算）
        buy_threshold: 买入阈值（质量分高于此值买入）
        sell_threshold: 卖出阈值（质量分低于此值卖出）
    """

    # 质量因子配置：(列名关键词, 权重, 方向, 显示名)
    # 方向: 'positive'=越高越好, 'negative'=越低越好（会反转）
    FACTOR_CONFIG = [
        ('净资产收益率', 0.30, 'positive', 'ROE'),
        ('经营现金净流量与净利润的比率', 0.25, 'positive', '经营现金流/净利润'),
        ('销售净利率', 0.20, 'positive', '净利率'),
        ('资产负债率', 0.15, 'negative', '资产负债率'),
        ('净利润增长率', 0.10, 'positive', '利润稳定性'),
    ]

    def __init__(self, buy_threshold=0.55, sell_threshold=0.45,
                 lookback_quarters=20, name='QualityFactor'):
        super().__init__(name=name)
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.lookback_quarters = lookback_quarters
        self._cache = {}

    def _fetch_financials(self, symbol):
        """获取财务指标历史数据"""
        if symbol in self._cache:
            return self._cache[symbol]

        try:
            import akshare as ak
            df = ak.stock_financial_analysis_indicator(symbol=symbol, start_year='2016')
            self._cache[symbol] = df
            return df
        except Exception as e:
            logger.warning("获取 %s 财务数据失败: %s", symbol, str(e))
            return None

    def _find_column(self, df, keyword):
        """在 DataFrame 中查找包含关键词的列名"""
        for col in df.columns:
            if keyword in col:
                return col
        return None

    def _calc_percentile_score(self, series, direction='positive'):
        """
        计算序列最后一个值的历史分位数评分

        Args:
            series: 指标序列
            direction: 'positive'=越高越好, 'negative'=越低越好

        Returns:
            0~1 之间的评分（越高越好）
        """
        s = series.dropna()
        if len(s) < 4:
            return None

        current = s.iloc[-1]
        if pd.isna(current):
            return None

        # 取最近 N 个数据点（或全部，取较小值）
        recent = s.tail(min(self.lookback_quarters, len(s)))
        rank = (recent < current).sum()
        pct = rank / len(recent)

        if direction == 'negative':
            pct = 1.0 - pct
        return pct

    def _score_to_signal(self, score):
        if score >= self.buy_threshold:
            return 1
        elif score <= self.sell_threshold:
            return -1
        return 0

    def generate_signals(self, df):
        """
        生成质量因子信号

        Args:
            df: 行情数据 DataFrame（索引为日期，需要 df.attrs['symbol']）

        Returns:
            pd.Series: 交易信号序列 (1/0/-1)
        """
        symbol = df.attrs.get('symbol', 'unknown')
        fin_df = self._fetch_financials(symbol)
        if fin_df is None or fin_df.empty:
            return pd.Series(0, index=df.index)

        # 为每个财务报告日计算质量得分
        fin_df = fin_df.copy()
        fin_df['日期'] = pd.to_datetime(fin_df['日期'])

        # 收集所有因子列名
        factor_cols = []
        factor_weights = []
        factor_directions = []
        for keyword, weight, direction, _ in self.FACTOR_CONFIG:
            col = self._find_column(fin_df, keyword)
            if col:
                factor_cols.append(col)
                factor_weights.append(weight)
                factor_directions.append(direction)

        if not factor_cols:
            return pd.Series(0, index=df.index)

        # 计算每个财务报告期的质量得分
        fin_scores = []
        for idx in range(len(fin_df)):
            scores_parts = []
            weights_parts = []
            for col, weight, direction in zip(factor_cols, factor_weights, factor_directions):
                series = fin_df[col].iloc[:idx + 1]
                pct = self._calc_percentile_score(series, direction)
                if pct is not None:
                    scores_parts.append(pct)
                    weights_parts.append(weight)

            if scores_parts:
                total_w = sum(weights_parts)
                score = sum(s * w / total_w for s, w in zip(scores_parts, weights_parts))
            else:
                score = np.nan
            fin_scores.append((fin_df['日期'].iloc[idx], score))

        if not fin_scores:
            return pd.Series(0, index=df.index)

        # 将分数映射到行情数据的日期索引
        score_series = pd.Series(
            [s[1] for s in fin_scores],
            index=[s[0] for s in fin_scores]
        ).sort_index()
        # 前向填充，使每个行情日期都有最近的财务质量分
        score_aligned = score_series.reindex(df.index, method='ffill')

        # 转换为信号
        signals = pd.Series(0, index=df.index)
        for i, date in enumerate(df.index):
            s = score_aligned.iloc[i]
            if pd.notna(s):
                signals.iloc[i] = self._score_to_signal(s)

        # 存储元信息
        last_score = score_series.iloc[-1] if len(score_series) > 0 else np.nan
        signals.attrs['quality_score'] = last_score

        return signals

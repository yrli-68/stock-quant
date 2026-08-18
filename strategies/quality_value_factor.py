"""
质量价值融合策略 (Quality-Value Fusion Strategy)

融合估值、质量、筹码、增减持和波动率五个维度，综合评估标的的投资价值。

估值因子（价值维度）：
    - PE-TTM（市盈率）
    - PB（市净率）
    - PS（市销率）
    - PCF（市现率）
    - EY（盈利收益率 = 1/PE）

质量因子（质量维度）：
    - ROE（净资产收益率）
    - 经营现金流/净利润比率
    - 销售净利率
    - 资产负债率（反向）
    - 净利润增长率

筹码因子（股东人数维度）：
    - 股东人数变化趋势：人数减少 → 筹码集中 → 利好
    - 人均持股变化：人均持股增加 → 筹码集中 → 利好

增减持因子（内部人交易维度）：
    - 大股东/高管增减持：增持 → 利好，减持 → 利空

波动率因子（低波异象维度）：
    - 20日历史波动率（HV20）：低波动长期收益更高
    - 最大回撤（252日）：低回撤更稳健
    - ATR/价格比：震荡、熊市表现优秀，大牛市跑输高波动成长股
    - 近期净增持量占流通股比例

计算逻辑：
    1. 分别计算四个维度的分位数得分（均为 0~1）
    2. 按配置权重组合加权
    3. combined ≥ buy_threshold → 买入, combined ≤ sell_threshold → 卖出

支持四类标的的差异化估值因子权重：
    - 宽基指数：PE-TTM、PB
    - 成长赛道：PS、PCF（常亏损，PE无效）
    - 红利类：PE-TTM、PB、PS
"""

import logging
import numpy as np
import pandas as pd
import re as _re
import warnings
from datetime import datetime, timedelta
from core.strategy import Strategy

logger = logging.getLogger(__name__)
warnings.filterwarnings('ignore')


def _call_with_timeout(fn, timeout=30):
    """在守护线程中运行函数，超时返回 None，避免 akshare 网络接口卡死"""
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
    if 'error' in result:
        raise result['error']
    return result.get('value')


class QualityValueFactorStrategy(Strategy):
    """
    质量价值融合策略

    融合估值、质量、筹码、增减持、波动率五个维度，加权生成综合信号。

    Attributes:
        stock_type: 标的类型 (broad_index/growth/dividend/auto)
        buy_threshold: 买入阈值
        sell_threshold: 卖出阈值
        value_weight: 估值因子权重
        quality_weight: 质量因子权重
        shareholder_weight: 股东人数变化权重
        insider_weight: 增减持因子权重
        volatility_weight: 波动率因子权重
    """

    # ---- 价值因子：标的分类配置 ----
    STOCK_TYPES = {
        'broad_index': {
            'name': '宽基指数',
            'factors': ['pe_ttm', 'pb'],
            'weights': [0.55, 0.45],
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

    BROAD_INDEX_CODES = {
        '000300', '000905', '000016', '000688', '000852', '000001',
        '399006', '399005', '399001', '000903', '000922',
    }
    GROWTH_PREFIXES = ('300', '301', '688')

    # ETF 代码 → 跟踪指数名称（用于估值因子）
    ETF_INDEX_MAP = {
        '510300': '沪深300', '510050': '上证50', '510500': '中证500',
        '512100': '中证1000', '588000': '科创50', '159915': '创业板指',
        '510180': '上证180', '159901': '深证100',
    }

    # ---- 质量因子配置 ----
    QUALITY_FACTOR_CONFIG = [
        ('净资产收益率', 0.30, 'positive', 'ROE'),
        ('经营现金净流量与净利润的比率', 0.25, 'positive', '经营现金流/净利润'),
        ('销售净利率', 0.20, 'positive', '净利率'),
        ('资产负债率', 0.15, 'negative', '资产负债率'),
        ('净利润增长率', 0.10, 'positive', '利润稳定性'),
    ]

    def __init__(self, stock_type='auto',
                 buy_threshold=0.6, sell_threshold=0.4,
                 value_weight=0.35, quality_weight=0.30,
                 shareholder_weight=0.15, insider_weight=0.10,
                 volatility_weight=0.10,
                 name='QualityValueFactor'):
        super().__init__(name=name)
        self.stock_type = stock_type
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.value_weight = value_weight
        self.quality_weight = quality_weight
        self.shareholder_weight = shareholder_weight
        self.insider_weight = insider_weight
        self.volatility_weight = volatility_weight
        self._valuation_cache = {}
        self._financials_cache = {}
        self._shareholder_cache = {}
        self._insider_cache = {}

    # =========================================================================
    # 标的分类
    # =========================================================================

    def _classify_stock(self, symbol):
        if symbol in self.BROAD_INDEX_CODES:
            return 'broad_index'
        if self._is_etf(symbol):
            return 'broad_index'
        if symbol.startswith(self.GROWTH_PREFIXES):
            return 'growth'
        return 'dividend'

    @staticmethod
    def _is_etf(symbol):
        return symbol.startswith(('5', '15', '16', '51', '58', '588')) or symbol[:3] == '159'

    # =========================================================================
    # 估值数据获取（价值维度）
    # =========================================================================

    def _fetch_valuation(self, symbol, stock_type):
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
        # 先尝试从数据库读取
        try:
            from core.db import fetch_valuation
            db_rows = fetch_valuation(symbol)
            if db_rows:
                df_db = pd.DataFrame(db_rows, columns=[
                    'date', 'pe_ttm', 'pb', 'ps', 'pcf', 'ey', 'dividend_yield'
                ])
                df_db['date'] = pd.to_datetime(df_db['date'])
                df_db = df_db.set_index('date')
                logger.info("从数据库读取 %d 条指数估值: %s", len(df_db), symbol)
                return df_db
        except Exception:
            pass

        import akshare as ak

        index_name_map = {
            '000300': '沪深300', '000016': '上证50', '000905': '中证500',
            '399006': '创业板指', '000688': '科创50', '000852': '中证1000',
            '000001': '上证指数', '399005': '中小100',
        }
        index_name_map.update(self.ETF_INDEX_MAP)
        index_name = index_name_map.get(symbol, '沪深300')

        df_pe, df_pb = None, None
        try:
            df_pe = _call_with_timeout(lambda: ak.stock_index_pe_lg(symbol=index_name), timeout=30)
            pe_col = '滚动市盈率(TTM)' if '滚动市盈率(TTM)' in df_pe.columns else '滚动市盈率'
            df_pe = df_pe.rename(columns={'日期': 'date', pe_col: 'pe_ttm'})
            df_pe['date'] = pd.to_datetime(df_pe['date'])
            df_pe = df_pe.set_index('date')[['pe_ttm']]
        except Exception:
            df_pe = None

        try:
            df_pb = _call_with_timeout(lambda: ak.stock_index_pb_lg(symbol=index_name), timeout=30)
            df_pb = df_pb.rename(columns={'日期': 'date', '市净率': 'pb'})
            df_pb['date'] = pd.to_datetime(df_pb['date'])
            df_pb = df_pb.set_index('date')[['pb']]
        except Exception:
            df_pb = None

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
        self._save_valuation_to_db(symbol, df)
        return df

    def _fetch_stock_valuation(self, symbol):
        # 先尝试从数据库读取
        try:
            from core.db import fetch_valuation
            db_rows = fetch_valuation(symbol)
            if db_rows:
                df_db = pd.DataFrame(db_rows, columns=[
                    'date', 'pe_ttm', 'pb', 'ps', 'pcf', 'ey', 'dividend_yield'
                ])
                df_db['date'] = pd.to_datetime(df_db['date'])
                df_db = df_db.set_index('date')
                logger.info("从数据库读取 %d 条估值记录: %s", len(df_db), symbol)
                return df_db
        except Exception:
            pass

        import akshare as ak

        try:
            df = _call_with_timeout(lambda: ak.stock_value_em(symbol=symbol), timeout=30)
            if df is not None and not df.empty:
                col_map = {
                    '数据日期': 'date', 'PE(TTM)': 'pe_ttm',
                    '市净率': 'pb', '市销率': 'ps', '市现率': 'pcf',
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
                # 回写数据库
                self._save_valuation_to_db(symbol, df)
                return df
        except Exception:
            pass

        try:
            pe_df = _call_with_timeout(lambda: ak.stock_zh_valuation_baidu(symbol=symbol, indicator='市盈率(TTM)', period='全部'), timeout=30)
            pb_df = _call_with_timeout(lambda: ak.stock_zh_valuation_baidu(symbol=symbol, indicator='市净率', period='全部'), timeout=30)
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
            self._save_valuation_to_db(symbol, df)
            return df
        except Exception:
            return None

    def _save_valuation_to_db(self, symbol, df):
        """回写估值数据到数据库"""
        try:
            from core.db import store_valuation
            rows = []
            for idx, row in df.iterrows():
                if hasattr(idx, 'strftime'):
                    date_str = idx.strftime('%Y-%m-%d')
                else:
                    continue
                rows.append((
                    symbol,
                    date_str,
                    float(row['pe_ttm']) if pd.notna(row.get('pe_ttm')) else None,
                    float(row['pb']) if pd.notna(row.get('pb')) else None,
                    float(row['ps']) if pd.notna(row.get('ps')) else None,
                    float(row['pcf']) if pd.notna(row.get('pcf')) else None,
                    float(row['ey']) if pd.notna(row.get('ey')) else None,
                    float(row['dividend_yield']) if pd.notna(row.get('dividend_yield')) else None,
                ))
            if rows:
                store_valuation(symbol, rows)
        except Exception:
            pass

    def _save_financials_to_db(self, symbol, df):
        """回写财务数据到数据库"""
        try:
            from core.db import store_financials
            store_financials(symbol, df)
        except Exception:
            pass

    # =========================================================================
    # 价值因子分位数打分
    # =========================================================================

    def _compute_value_score(self, df_index, val_df, stock_type):
        type_cfg = self.STOCK_TYPES.get(stock_type, self.STOCK_TYPES['dividend'])
        factors = type_cfg['factors']
        weights = type_cfg['weights']

        scores = pd.Series(np.nan, index=df_index)

        for i, date in enumerate(df_index):
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

                if factor in ('pe_ttm', 'pb', 'ps', 'pcf'):
                    pct = 1.0 - pct

                percentile_scores.append((pct, weight))

            if not percentile_scores:
                continue

            total_weight = sum(w for _, w in percentile_scores)
            if total_weight == 0:
                continue

            scores.iloc[i] = sum(pct * w / total_weight for pct, w in percentile_scores)

        return scores

    # =========================================================================
    # 质量因子：数据获取 & 分位数打分
    # =========================================================================

    def _fetch_financials(self, symbol):
        if symbol in self._financials_cache:
            return self._financials_cache[symbol]

        # 先尝试从数据库读取
        try:
            from core.db import fetch_financials
            db_rows = fetch_financials(symbol)
            if db_rows:
                df_db = pd.DataFrame(db_rows, columns=[
                    'report_date', 'roe', 'cashflow_net_profit_ratio',
                    'net_margin', 'debt_ratio', 'profit_growth'
                ])
                df_db['日期'] = pd.to_datetime(df_db['report_date'])
                logger.info("从数据库读取 %d 条财务记录: %s", len(df_db), symbol)
                self._financials_cache[symbol] = df_db
                return df_db
        except Exception:
            pass

        try:
            import akshare as ak
            df = _call_with_timeout(lambda: ak.stock_financial_analysis_indicator(symbol=symbol, start_year='2016'), timeout=30)
            self._financials_cache[symbol] = df
            # 回写数据库
            try:
                self._save_financials_to_db(symbol, df)
            except Exception:
                pass
            return df
        except Exception as e:
            logger.warning("获取 %s 财务数据失败: %s", symbol, str(e))
            return None

    def _find_column(self, df, keyword):
        for col in df.columns:
            if keyword in col:
                return col
        return None

    def _calc_percentile_score(self, series, direction='positive'):
        s = series.dropna()
        if len(s) < 4:
            return None

        current = s.iloc[-1]
        if pd.isna(current):
            return None

        rank = (s < current).sum()
        pct = rank / len(s)

        if direction == 'negative':
            pct = 1.0 - pct
        return pct

    def _compute_quality_score(self, df_index, symbol):
        fin_df = self._fetch_financials(symbol)
        if fin_df is None or fin_df.empty:
            return pd.Series(np.nan, index=df_index)

        fin_df = fin_df.copy()
        fin_df['日期'] = pd.to_datetime(fin_df['日期'])

        factor_cols, factor_weights, factor_directions = [], [], []
        for keyword, weight, direction, _ in self.QUALITY_FACTOR_CONFIG:
            col = self._find_column(fin_df, keyword)
            if col:
                factor_cols.append(col)
                factor_weights.append(weight)
                factor_directions.append(direction)

        if not factor_cols:
            return pd.Series(np.nan, index=df_index)

        fin_scores = []
        for idx in range(len(fin_df)):
            scores_parts, weights_parts = [], []
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
            return pd.Series(np.nan, index=df_index)

        score_series = pd.Series(
            [s[1] for s in fin_scores],
            index=[s[0] for s in fin_scores]
        ).sort_index()

        score_aligned = score_series.reindex(df_index, method='ffill')
        return score_aligned

    # =========================================================================
    # 筹码因子：股东人数变化
    # =========================================================================

    def _fetch_shareholder_count(self, symbol):
        """获取股东人数历史数据（DB 优先，类级缓存避免重复拉取全量）"""
        if symbol in self._shareholder_cache:
            return self._shareholder_cache[symbol]

        # 先尝试从数据库读取
        try:
            from core.db import fetch_shareholder
            db_rows = fetch_shareholder(symbol)
            if db_rows:
                df_db = pd.DataFrame(db_rows, columns=[
                    'change_date', 'holder_count', 'prev_holder_count',
                    'holder_change_pct', 'avg_holding', 'prev_avg_holding',
                    'avg_holding_change_pct'
                ])
                df_db['变动日期'] = pd.to_datetime(df_db['change_date'])
                keep_cols = {}
                for c, dbc in [('本期股东人数', 'holder_count'), ('上期股东人数', 'prev_holder_count'),
                                 ('股东人数增幅', 'holder_change_pct'), ('本期人均持股数量', 'avg_holding'),
                                 ('上期人均持股数量', 'prev_avg_holding'), ('人均持股数量增幅', 'avg_holding_change_pct')]:
                    if df_db[dbc].notna().any():
                        keep_cols[c] = dbc
                hist = df_db.rename(columns={v: k for k, v in keep_cols.items()})
                hist = hist[list(keep_cols.keys())]
                hist = hist.set_index('变动日期').sort_index()
                logger.info("从数据库读取 %d 条股东数据: %s", len(hist), symbol)
                self._shareholder_cache[symbol] = hist
                return hist
        except Exception:
            pass

        # 类级缓存：按日期缓存全量数据，同一日期只需拉取一次
        if not hasattr(QualityValueFactorStrategy, '_holder_date_cache'):
            QualityValueFactorStrategy._holder_date_cache = {}

        try:
            import akshare as ak

            # 仅取最近 3 个报告期（半年一次）
            current_year = datetime.now().year
            dates_to_fetch = []
            for y in range(current_year - 1, current_year + 1):
                for m in ['0630', '1231']:
                    d = f'{y}{m}'
                    if d <= datetime.now().strftime('%Y%m%d'):
                        dates_to_fetch.append(d)
            dates_to_fetch = sorted(dates_to_fetch)[-3:]

            all_data = []
            for report_date in dates_to_fetch:
                if report_date not in QualityValueFactorStrategy._holder_date_cache:
                    try:
                        QualityValueFactorStrategy._holder_date_cache[report_date] = \
                            _call_with_timeout(lambda: ak.stock_hold_num_cninfo(date=report_date), timeout=30)
                    except Exception:
                        continue

                df_all = QualityValueFactorStrategy._holder_date_cache[report_date]
                if df_all is not None and not df_all.empty:
                    row = df_all[df_all['证券代码'] == symbol]
                    if not row.empty:
                        all_data.append(row.iloc[0])

            if not all_data:
                return None

            hist = pd.DataFrame(all_data)
            if '变动日期' in hist.columns:
                hist['变动日期'] = pd.to_datetime(hist['变动日期'])
                hist = hist.set_index('变动日期').sort_index()

            keep_cols = []
            for c in ['本期股东人数', '上期股东人数', '股东人数增幅',
                       '本期人均持股数量', '上期人均持股数量', '人均持股数量增幅']:
                if c in hist.columns:
                    keep_cols.append(c)
            hist = hist[keep_cols]

            self._shareholder_cache[symbol] = hist
            # 回写数据库
            try:
                from core.db import store_shareholder
                store_shareholder(symbol, hist.reset_index())
            except Exception:
                pass
            return hist
        except Exception as e:
            logger.warning("获取 %s 股东人数失败: %s", symbol, str(e))
            return None

    def _compute_shareholder_score(self, df_index, symbol):
        """
        计算筹码集中度得分。

        股东人数减少 → 筹码集中 → 高得分
        人均持股增加 → 筹码集中 → 高得分
        """
        holder_df = self._fetch_shareholder_count(symbol)
        if holder_df is None or holder_df.empty:
            return pd.Series(np.nan, index=df_index)

        # 为每个报告期计算得分
        holder_scores = []
        for date in holder_df.index:
            val = holder_df.loc[date]
            score_parts = []

            # 股东人数增幅: 负数=减少=筹码集中=好
            if '股东人数增幅' in holder_df.columns:
                pct_change = val['股东人数增幅']
                if pd.notna(pct_change):
                    # 将增幅映射为分数: -30% → 0.9, 0% → 0.5, +30% → 0.1
                    share_score = max(0.0, min(1.0, 0.5 - pct_change / 100.0))
                    score_parts.append((share_score, 0.6))

            # 人均持股增幅: 正数=增加=筹码集中=好
            if '人均持股数量增幅' in holder_df.columns:
                per_cap_change = val['人均持股数量增幅']
                if pd.notna(per_cap_change):
                    per_cap_score = max(0.0, min(1.0, 0.5 + per_cap_change / 100.0))
                    score_parts.append((per_cap_score, 0.4))

            if score_parts:
                total_w = sum(w for _, w in score_parts)
                score = sum(s * w / total_w for s, w in score_parts)
            else:
                score = np.nan
            holder_scores.append((date, score))

        if not holder_scores:
            return pd.Series(np.nan, index=df_index)

        score_series = pd.Series(
            [s[1] for s in holder_scores],
            index=[s[0] for s in holder_scores]
        ).sort_index()

        return score_series.reindex(df_index, method='ffill')

    # =========================================================================
    # 增减持因子：大股东/高管交易
    # =========================================================================

    def _fetch_insider_trades(self, symbol):
        """获取大股东及高管增减持历史 (DB 优先)"""
        if symbol in self._insider_cache:
            return self._insider_cache[symbol]

        # 先尝试从数据库读取
        try:
            from core.db import fetch_insider_trades
            db_rows = fetch_insider_trades(symbol)
            if db_rows:
                df_db = pd.DataFrame(db_rows, columns=[
                    'announce_date', 'shareholder', 'change_amount',
                    'change_text', 'trade_price', 'remaining',
                    'trade_period', 'trade_method'
                ])
                df_db['公告日期'] = pd.to_datetime(df_db['announce_date'])
                for c in ['变动数量', '交易均价', '变动期间', '变动途径']:
                    if c not in df_db.columns:
                        df_db[c] = None
                df_db['变动数量'] = df_db['change_text']
                df_db['交易均价'] = df_db['trade_price']
                df_db['变动期间'] = df_db['trade_period']
                df_db['变动途径'] = df_db['trade_method']
                df_db['变动股东'] = df_db['shareholder']
                logger.info("从数据库读取 %d 条增减持: %s", len(df_db), symbol)
                self._insider_cache[symbol] = df_db
                return df_db
        except Exception:
            pass

        try:
            import akshare as ak

            df_ths = None
            try:
                df_ths = _call_with_timeout(lambda: ak.stock_shareholder_change_ths(symbol=symbol), timeout=30)
            except Exception:
                pass

            self._insider_cache[symbol] = df_ths
            # 回写数据库
            if df_ths is not None and not df_ths.empty:
                try:
                    from core.db import store_insider_trades
                    store_insider_trades(symbol, df_ths)
                except Exception:
                    pass
            return df_ths
        except Exception as e:
            logger.warning("获取 %s 增减持数据失败: %s", symbol, str(e))
            return None

    def _parse_change_amount(self, text):
        """解析变动数量字符串，如 '减持1170.99万' → -11709900"""
        if not text or pd.isna(text):
            return 0.0
        text = str(text).strip()

        multiplier = 1.0
        if '亿' in text:
            multiplier = 1e8
        elif '万' in text:
            multiplier = 1e4

        sign = -1 if '减持' in text or '减' in text else 1
        num_match = _re.search(r'([\d.]+)', text)
        if num_match:
            return sign * float(num_match.group(1)) * multiplier
        return 0.0

    def _compute_insider_score(self, df_index, symbol):
        """
        计算增减持因子得分。

        近期净增持 → 高得分
        近期净减持 → 低得分
        """
        trades_df = self._fetch_insider_trades(symbol)
        if trades_df is None or trades_df.empty:
            return pd.Series(np.nan, index=df_index)

        trades_df = trades_df.copy()
        if '公告日期' not in trades_df.columns:
            return pd.Series(np.nan, index=df_index)

        trades_df['公告日期'] = pd.to_datetime(trades_df['公告日期'])
        trades_df = trades_df.sort_values('公告日期')

        # 解析变动数量
        if '变动数量' in trades_df.columns:
            trades_df['_net_change'] = trades_df['变动数量'].apply(self._parse_change_amount)
        else:
            return pd.Series(np.nan, index=df_index)

        # 对每个交易日，计算过去 12 个月的累计净增持
        # 先建立一个评分时间序列（在公告日期打分）
        insider_scores = []
        lookback_months = 12

        for date in trades_df['公告日期'].unique():
            cutoff = date - timedelta(days=lookback_months * 30)
            recent = trades_df[
                (trades_df['公告日期'] >= cutoff) &
                (trades_df['公告日期'] <= date)
            ]
            net_change = recent['_net_change'].sum()

            # 净增持为正 → 高得分，净减持为负 → 低得分
            # 使用 sigmoid 型映射，使极端值两端平滑
            # net_change 范围可能很大，用对数映射
            if net_change > 0:
                score = 0.5 + 0.4 * min(1.0, np.log10(max(net_change, 1)) / 8.0)
            elif net_change < 0:
                score = 0.5 - 0.4 * min(1.0, np.log10(max(abs(net_change), 1)) / 8.0)
            else:
                score = 0.5

            insider_scores.append((date, score))

        if not insider_scores:
            return pd.Series(np.nan, index=df_index)

        score_series = pd.Series(
            [s[1] for s in insider_scores],
            index=[s[0] for s in insider_scores]
        ).sort_index()

        return score_series.reindex(df_index, method='ffill')

    # =========================================================================
    # 波动率因子：低波异象
    # =========================================================================

    def _compute_volatility_score(self, df_index, df):
        """
        计算波动率因子得分（低波异象：波动越低得分越高）。

        因子：
            - HV20（20日年化历史波动率）：越低越好
            - MaxDD（252日最大回撤）：越低越好
            - ATR14/close（归一化真实波幅）：越低越好

        Returns:
            pd.Series: 与 df_index 对齐的波动率得分 (0~1)
        """
        scores = pd.Series(np.nan, index=df_index)
        min_data = 60

        for i in range(len(df)):
            if i < min_data:
                continue

            date = df.index[i]
            window = df.iloc[:i + 1]

            score_parts = []

            # HV20：年化波动率，越低越好
            if 'HV20' in df.columns:
                hv_val = window['HV20'].iloc[-1]
                if pd.notna(hv_val) and hv_val > 0:
                    # 通过分位数计算: 在 window 中的排名
                    hv_series = window['HV20'].dropna()
                    if len(hv_series) >= min_data:
                        # 低波动 = 高分位反转
                        rank = (hv_series > hv_val).sum()
                        hv_pct = rank / len(hv_series)
                        score_parts.append((hv_pct, 0.40))

            # MaxDD（252日最大回撤）：越低越好
            close_series = window['close']
            if len(close_series) >= 60:
                roll_max = close_series.rolling(window=min(252, len(close_series)), min_periods=1).max()
                dd = (close_series - roll_max) / roll_max
                current_dd = dd.iloc[-1]
                # 回撤绝对值越小越好，映射为: -0%→1.0, -50%→0.0
                dd_score = max(0.0, min(1.0, 1.0 + current_dd / 0.5))
                score_parts.append((dd_score, 0.35))

            # ATR14/close（归一化波幅）：越低越好
            if 'ATR14' in df.columns:
                atr_series = window['ATR14'].dropna()
                close_vals = window.loc[atr_series.index, 'close']
                if len(atr_series) >= min_data:
                    atr_pct = atr_series / close_vals  # 归一化
                    current_atr_pct = atr_pct.iloc[-1]
                    if pd.notna(current_atr_pct) and current_atr_pct > 0:
                        rank = (atr_pct > current_atr_pct).sum()
                        atr_rank = rank / len(atr_pct)
                        score_parts.append((atr_rank, 0.25))

            if score_parts:
                total_w = sum(w for _, w in score_parts)
                scores.iloc[i] = sum(pct * w / total_w for pct, w in score_parts)

        return scores

    # =========================================================================
    # 综合打分 & 信号生成
    # =========================================================================

    def _score_to_signal(self, score):
        if score >= self.buy_threshold:
            return 1
        elif score <= self.sell_threshold:
            return -1
        return 0

    def generate_signals(self, df):
        """
        生成质量价值多因子交易信号

        Args:
            df: 行情数据 DataFrame（索引为日期，需包含 df.attrs['symbol']）

        Returns:
            pd.Series: 交易信号序列 (1/0/-1)，索引与 df 对齐
        """
        symbol = df.attrs.get('symbol', 'unknown')

        if self.stock_type == 'auto':
            stock_type = self._classify_stock(symbol)
        else:
            stock_type = self.stock_type

        # 跳过宽基指数（无股东人数/增减持数据）
        is_index = (stock_type == 'broad_index')

        # 1. 计算五个维度得分
        val_df = self._fetch_valuation(symbol, stock_type)
        if val_df is not None and not val_df.empty:
            value_scores = self._compute_value_score(df.index, val_df, stock_type)
        else:
            value_scores = pd.Series(np.nan, index=df.index)

        quality_scores = self._compute_quality_score(df.index, symbol)

        if is_index:
            shareholder_scores = pd.Series(np.nan, index=df.index)
            insider_scores = pd.Series(np.nan, index=df.index)
        else:
            shareholder_scores = self._compute_shareholder_score(df.index, symbol)
            insider_scores = self._compute_insider_score(df.index, symbol)

        volatility_scores = self._compute_volatility_score(df.index, df)

        # 2. 加权融合
        combined = pd.Series(np.nan, index=df.index)
        has_value = value_scores.notna().to_numpy()
        has_quality = quality_scores.notna().to_numpy()
        has_shareholder = shareholder_scores.notna().to_numpy()
        has_insider = insider_scores.notna().to_numpy()
        has_volatility = volatility_scores.notna().to_numpy()

        for i in range(len(df.index)):
            vw = self.value_weight if has_value[i] else 0.0
            qw = self.quality_weight if has_quality[i] else 0.0
            sw = self.shareholder_weight if has_shareholder[i] and not is_index else 0.0
            iw = self.insider_weight if has_insider[i] and not is_index else 0.0
            vw2 = self.volatility_weight if has_volatility[i] else 0.0

            total_w = vw + qw + sw + iw + vw2
            if total_w == 0:
                continue

            vs = value_scores.iloc[i] if not pd.isna(value_scores.iloc[i]) else 0.0
            qs = quality_scores.iloc[i] if not pd.isna(quality_scores.iloc[i]) else 0.0
            ss = shareholder_scores.iloc[i] if not pd.isna(shareholder_scores.iloc[i]) else 0.0
            ins = insider_scores.iloc[i] if not pd.isna(insider_scores.iloc[i]) else 0.0
            vos = volatility_scores.iloc[i] if not pd.isna(volatility_scores.iloc[i]) else 0.0

            combined.iloc[i] = (vs * vw + qs * qw + ss * sw + ins * iw + vos * vw2) / total_w

        # 3. 转换为交易信号
        signals = pd.Series(0, index=df.index)
        for i in range(len(df.index)):
            s = combined.iloc[i]
            if pd.notna(s):
                signals.iloc[i] = self._score_to_signal(s)

        signals.attrs['stock_type'] = self.STOCK_TYPES.get(stock_type, {}).get('name', '')
        signals.attrs['combined_score'] = combined

        return signals

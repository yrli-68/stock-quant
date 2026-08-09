# 可视化模块 - 图表生成器
# 使用 matplotlib 和 mplfinance 绘制各类量化分析图表
# 所有图表均支持中文字体显示

import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端，避免GUI相关错误
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.ticker as mticker
import mplfinance as mpf
import pandas as pd
import numpy as np
import os
import warnings
from datetime import datetime

# 忽略matplotlib的一些非关键警告
warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib')

# ============================================================================
# 中文字体设置
# ============================================================================


def _setup_chinese_font():
    """通过显式文件路径创建 FontProperties，确保中文正确渲染"""
    import os

    local_font_dir = os.path.expanduser('~/.local/share/fonts/noto')
    regular_path = os.path.join(local_font_dir, 'NotoSansCJKsc-Regular.otf')
    bold_path = os.path.join(local_font_dir, 'NotoSansCJKsc-Bold.otf')

    if not os.path.exists(regular_path):
        # 回退：使用 DejaVu Sans（无法显示中文）
        fp = fm.FontProperties()
        plt.rcParams['axes.unicode_minus'] = False
        return fp, fp

    # 注册字体文件
    for p in [regular_path, bold_path]:
        if os.path.exists(p):
            try:
                fm.fontManager.addfont(p)
            except Exception:
                pass

    fp_regular = fm.FontProperties(fname=regular_path)
    fp_bold = fm.FontProperties(fname=bold_path if os.path.exists(bold_path) else regular_path)
    plt.rcParams['axes.unicode_minus'] = False

    # 同时设置 rcParams（供 mplfinance 等不使用显式 FontProperties 的库使用）
    font_name = fp_regular.get_name()
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = [font_name, 'DejaVu Sans']

    return fp_regular, fp_bold


FP_REGULAR, FP_BOLD = _setup_chinese_font()

# ============================================================================
# 全局配色方案
# ============================================================================

# 配色方案：采用柔和的颜色搭配，保证图表美观
COLOR_UP = '#e74c3c'        # 上涨红色
COLOR_DOWN = '#2ecc71'      # 下跌绿色
COLOR_BLUE = '#3498db'      # 蓝色主色调
COLOR_ORANGE = '#e67e22'    # 橙色
COLOR_PURPLE = '#9b59b6'    # 紫色
COLOR_TEAL = '#1abc9c'      # 青色
COLOR_DARK = '#2c3e50'      # 深色文字
COLOR_GRAY = '#95a5a6'      # 灰色
COLOR_BG = '#fafafa'        # 背景色
COLOR_GOLD = '#f39c12'      # 金色

# 多策略配色列表
STRATEGY_COLORS = ['#3498db', '#e74c3c', '#2ecc71', '#e67e22', '#9b59b6', '#1abc9c', '#f39c12', '#34495e']

# ============================================================================
# 图表生成器类
# ============================================================================


class ChartGenerator:
    """图表生成器，用于生成各类量化分析图表"""

    def __init__(self, output_dir='./charts'):
        """
        初始化图表生成器

        Parameters
        ----------
        output_dir : str, 图表输出目录
        """
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def _get_save_path(self, filename):
        """获取完整的保存路径"""
        return os.path.join(self.output_dir, filename)

    def plot_kline_with_indicators(self, df, title='K线图与技术指标', save_path=None):
        """
        绘制K线图 + 成交量 + MACD + RSI 子图

        Parameters
        ----------
        df : pd.DataFrame
            包含 OHLCV 数据以及 'MACD', 'MACD_Signal', 'MACD_Hist', 'RSI' 列的 DataFrame
        title : str, 图表标题
        save_path : str, 保存路径，默认自动生成

        Returns
        -------
        str : 保存的文件路径
        """
        if save_path is None:
            save_path = self._get_save_path('kline_indicators.png')

        # 确保数据按日期排序，索引为DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'date' in df.columns:
                df = df.set_index('date')
            df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        # 规范化列名以兼容 mplfinance（将小写列名转为大写，指标列名映射）
        df = df.copy()
        rename_map = {}
        # OHLCV 列名规范化
        ohlc_map = {'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}
        for old_col, new_col in ohlc_map.items():
            if old_col in df.columns and new_col not in df.columns:
                rename_map[old_col] = new_col
        # 指标列名映射
        indi_map = {'MACD_DIF': 'MACD', 'MACD_DEA': 'MACD_Signal', 'MACD_BAR': 'MACD_Hist', 'RSI14': 'RSI'}
        for old_col, new_col in indi_map.items():
            if old_col in df.columns and new_col not in df.columns:
                rename_map[old_col] = new_col
        if rename_map:
            df = df.rename(columns=rename_map)

        # 检查必要的列
        required_ohlc = ['Open', 'High', 'Low', 'Close', 'Volume']
        for col in required_ohlc:
            if col not in df.columns:
                raise ValueError(f"缺少必要列: {col}")

        # 准备MACD面板
        add_plots = []
        panel_colors = []

        # MACD指标
        if 'MACD' in df.columns and 'MACD_Signal' in df.columns and 'MACD_Hist' in df.columns:
            add_plots.append(mpf.make_addplot(df['MACD'], panel=2, color=COLOR_BLUE, width=1.2, ylabel='MACD'))
            add_plots.append(mpf.make_addplot(df['MACD_Signal'], panel=2, color=COLOR_ORANGE, width=1.2))
            # MACD柱状图
            macd_hist_colors = [COLOR_UP if v >= 0 else COLOR_DOWN for v in df['MACD_Hist']]
            add_plots.append(mpf.make_addplot(df['MACD_Hist'], type='bar', panel=2, color=macd_hist_colors, width=0.8))

        # RSI指标
        if 'RSI' in df.columns:
            add_plots.append(mpf.make_addplot(df['RSI'], panel=3, color=COLOR_PURPLE, width=1.2, ylabel='RSI'))
            # 添加RSI参考线
            add_plots.append(mpf.make_addplot(pd.Series(70, index=df.index), panel=3, color=COLOR_GRAY, width=0.8, linestyle='--'))
            add_plots.append(mpf.make_addplot(pd.Series(30, index=df.index), panel=3, color=COLOR_GRAY, width=0.8, linestyle='--'))

        # 设置面板比例
        panel_ratios = (3, 1, 1.5, 1.5) if 'RSI' in df.columns else (3, 1, 1.5)

        # 自定义样式
        mc = mpf.make_marketcolors(
            up=COLOR_UP,
            down=COLOR_DOWN,
            edge='inherit',
            wick='inherit',
            volume={'up': COLOR_UP, 'down': COLOR_DOWN},
            alpha=0.9
        )
        s = mpf.make_mpf_style(
            marketcolors=mc,
            gridstyle=':',
            gridcolor='#e0e0e0',
            facecolor=COLOR_BG,
            figcolor=COLOR_BG,
            y_on_right=False,
            rc={
                'font.sans-serif': [FP_REGULAR.get_name(), 'DejaVu Sans'],
                'axes.unicode_minus': False,
            }
        )

        # 绘制图表
        fig, axes = mpf.plot(
            df,
            type='candle',
            style=s,
            volume=True,
            addplot=add_plots if add_plots else None,
            title=title,
            panel_ratios=panel_ratios,
            figsize=(16, 10),
            returnfig=True,
            warn_too_much_data=len(df) + 1
        )

        # mpf.plot 不走 FontProperties，需在画完后强制修正各轴的字体
        for ax in axes:
            if ax.get_title():
                ax.set_title(ax.get_title(), fontproperties=FP_BOLD)
            if ax.get_ylabel():
                ax.set_ylabel(ax.get_ylabel(), fontproperties=FP_REGULAR)
            for label in (ax.get_xticklabels() + ax.get_yticklabels()):
                label.set_fontproperties(FP_REGULAR)
            legend = ax.get_legend()
            if legend is not None:
                for text in legend.get_texts():
                    text.set_fontproperties(FP_REGULAR)

        # 调整布局
        fig.tight_layout()
        fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
        plt.close(fig)

        return save_path

    def plot_equity_curve(self, backtest_result, title='权益曲线与回撤', save_path=None):
        """
        绘制权益曲线和回撤曲线

        Parameters
        ----------
        backtest_result : dict
            回测结果字典，需包含 'equity_curve', 'benchmark_curve' (可选), 'drawdown' (可选)
        title : str, 图表标题
        save_path : str, 保存路径

        Returns
        -------
        str : 保存的文件路径
        """
        if save_path is None:
            save_path = self._get_save_path('equity_curve.png')

        # 创建子图
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={'height_ratios': [2.5, 1]})
        fig.patch.set_facecolor(COLOR_BG)

        # 权益曲线
        equity = backtest_result.get('equity_curve', None)
        if equity is not None:
            if isinstance(equity, pd.Series):
                ax1.plot(equity.index, equity.values, color=COLOR_BLUE, linewidth=1.8, label='策略权益')
            else:
                ax1.plot(equity, color=COLOR_BLUE, linewidth=1.8, label='策略权益')

        # 基准曲线（如果有）
        benchmark = backtest_result.get('benchmark_curve', None)
        if benchmark is not None:
            if isinstance(benchmark, pd.Series):
                ax1.plot(benchmark.index, benchmark.values, color=COLOR_GRAY, linewidth=1.2, linestyle='--', label='基准')
            else:
                ax1.plot(benchmark, color=COLOR_GRAY, linewidth=1.2, linestyle='--', label='基准')

        # 初始资金参考线
        if equity is not None:
            if isinstance(equity, pd.Series):
                initial_value = equity.values[0] if len(equity) > 0 else 0
            else:
                initial_value = equity[0] if len(equity) > 0 else 0
            ax1.axhline(y=initial_value, color=COLOR_DARK, linewidth=0.8, linestyle=':', alpha=0.5)

        ax1.set_title(title, fontsize=14, fontproperties=FP_BOLD, color=COLOR_DARK)
        ax1.set_ylabel('权益', fontsize=11, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax1.legend(loc='upper left', prop=FP_REGULAR, framealpha=0.9, edgecolor='#ddd')
        ax1.grid(True, alpha=0.3, linestyle=':')
        ax1.set_facecolor(COLOR_BG)

        # 回撤曲线
        drawdown = backtest_result.get('drawdown', None)
        if drawdown is not None:
            if isinstance(drawdown, pd.Series):
                ax2.fill_between(drawdown.index, 0, drawdown.values * 100, color=COLOR_UP, alpha=0.3)
                ax2.plot(drawdown.index, drawdown.values * 100, color=COLOR_UP, linewidth=1.2)
            else:
                ax2.fill_between(range(len(drawdown)), 0, np.array(drawdown) * 100, color=COLOR_UP, alpha=0.3)
                ax2.plot(np.array(drawdown) * 100, color=COLOR_UP, linewidth=1.2)
        else:
            # 从权益曲线计算回撤
            if equity is not None:
                if isinstance(equity, pd.Series):
                    cummax = equity.cummax()
                    dd = (equity - cummax) / cummax * 100
                    ax2.fill_between(dd.index, 0, dd.values, color=COLOR_UP, alpha=0.3)
                    ax2.plot(dd.index, dd.values, color=COLOR_UP, linewidth=1.2)
                else:
                    equity_arr = np.array(equity)
                    cummax = np.maximum.accumulate(equity_arr)
                    dd = (equity_arr - cummax) / cummax * 100
                    ax2.fill_between(range(len(dd)), 0, dd, color=COLOR_UP, alpha=0.3)
                    ax2.plot(dd, color=COLOR_UP, linewidth=1.2)

        ax2.set_ylabel('回撤 (%)', fontsize=11, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax2.set_xlabel('日期', fontsize=11, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax2.grid(True, alpha=0.3, linestyle=':')
        ax2.set_facecolor(COLOR_BG)
        ax2.axhline(y=0, color=COLOR_DARK, linewidth=0.5, linestyle='-')

        # 格式化百分比
        ax2.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.0f%%'))

        fig.tight_layout()
        fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
        plt.close(fig)

        return save_path

    def plot_trade_distribution(self, trades_df, save_path=None):
        """
        绘制交易盈亏分布饼图和柱状图

        Parameters
        ----------
        trades_df : pd.DataFrame
            交易记录DataFrame，需包含 'pnl' 或 'return' 列
        save_path : str, 保存路径

        Returns
        -------
        str : 保存的文件路径
        """
        if save_path is None:
            save_path = self._get_save_path('trade_distribution.png')

        if trades_df is None or len(trades_df) == 0:
            # 创建空图提示
            fig, ax = plt.subplots(figsize=(12, 5))
            ax.text(0.5, 0.5, '无交易记录', ha='center', va='center',
                    fontproperties=FP_REGULAR, fontsize=16, color=COLOR_GRAY)
            fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
            plt.close(fig)
            return save_path

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        fig.patch.set_facecolor(COLOR_BG)

        # 确定盈亏列
        pnl_col = 'pnl' if 'pnl' in trades_df.columns else ('return' if 'return' in trades_df.columns else None)
        if pnl_col is None:
            raise ValueError("交易记录中缺少 'pnl' 或 'return' 列")

        trades = trades_df[pnl_col].dropna()

        # 盈亏分类
        win_count = (trades > 0).sum()
        lose_count = (trades < 0).sum()
        flat_count = (trades == 0).sum()

        # 饼图 - 胜率分布
        labels = ['盈利', '亏损']
        sizes = [win_count, lose_count]
        if flat_count > 0:
            labels.append('持平')
            sizes.append(flat_count)
        colors_pie = [COLOR_UP, COLOR_DOWN, COLOR_GRAY]
        explode = (0.05, 0, 0) if flat_count > 0 else (0.05, 0)

        wedges, texts, autotexts = ax1.pie(
            sizes, labels=labels, colors=colors_pie[:len(sizes)],
            autopct='%1.1f%%', startangle=90, explode=explode,
            shadow=False, textprops={'fontsize': 11, 'fontproperties': FP_REGULAR}
        )
        for autotext in autotexts:
            autotext.set_color('white')
            autotext.set_fontweight('bold')
        ax1.set_title('交易盈亏分布', fontsize=13, fontproperties=FP_BOLD, color=COLOR_DARK)

        # 柱状图 - 盈亏金额分布
        if len(trades) > 0:
            bins = min(30, max(10, len(trades) // 3))
            n, bins_edges, patches = ax2.hist(
                trades.values, bins=bins, color=COLOR_BLUE, alpha=0.7, edgecolor='white', linewidth=0.5
            )
            # 根据正负值着色
            bin_centers = 0.5 * (bins_edges[:-1] + bins_edges[1:])
            for patch, center in zip(patches, bin_centers):
                patch.set_facecolor(COLOR_UP if center >= 0 else COLOR_DOWN)

            ax2.axvline(x=0, color=COLOR_DARK, linewidth=1, linestyle='-')
            ax2.axvline(x=trades.mean(), color=COLOR_ORANGE, linewidth=1.5, linestyle='--', label=f'均值: {trades.mean():.4f}')
            ax2.set_title('盈亏分布直方图', fontsize=13, fontproperties=FP_BOLD, color=COLOR_DARK)
            ax2.set_xlabel('盈亏金额', fontsize=11, fontproperties=FP_REGULAR, color=COLOR_DARK)
            ax2.set_ylabel('频次', fontsize=11, fontproperties=FP_REGULAR, color=COLOR_DARK)
            ax2.legend(loc='upper right', prop=FP_REGULAR, framealpha=0.9)
            ax2.grid(True, alpha=0.3, linestyle=':')

        for ax in [ax2]:
            ax.set_facecolor(COLOR_BG)

        fig.tight_layout()
        fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
        plt.close(fig)

        return save_path

    def plot_signal_on_price(self, df, signals, title='买卖信号标注', save_path=None):
        """
        在价格图上标注买卖信号

        Parameters
        ----------
        df : pd.DataFrame
            包含 'Close' 列的 OHLC 数据
        signals : pd.Series
            信号序列，1表示买入，-1表示卖出，0表示无信号
        title : str, 图表标题
        save_path : str, 保存路径

        Returns
        -------
        str : 保存的文件路径
        """
        if save_path is None:
            save_path = self._get_save_path('signal_on_price.png')

        # 确保索引为DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'date' in df.columns:
                df = df.set_index('date')
            df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        if not isinstance(signals.index, pd.DatetimeIndex):
            signals.index = pd.to_datetime(signals.index)

        # 对齐信号和数据
        common_idx = df.index.intersection(signals.index)
        df_aligned = df.loc[common_idx]
        signals_aligned = signals.loc[common_idx]

        fig, ax = plt.subplots(figsize=(16, 7))
        fig.patch.set_facecolor(COLOR_BG)

        # 绘制收盘价
        ax.plot(df_aligned.index, df_aligned['close'], color=COLOR_BLUE, linewidth=1.2, label='收盘价', alpha=0.8)

        # 标注买入信号
        buy_signals = signals_aligned[signals_aligned == 1]
        sell_signals = signals_aligned[signals_aligned == -1]

        if len(buy_signals) > 0:
            buy_prices = df_aligned.loc[buy_signals.index, 'close']
            ax.scatter(buy_signals.index, buy_prices, color=COLOR_UP, marker='^',
                       s=100, zorder=5, label=f'买入 ({len(buy_signals)}次)', edgecolors='white', linewidth=0.5)

        if len(sell_signals) > 0:
            sell_prices = df_aligned.loc[sell_signals.index, 'close']
            ax.scatter(sell_signals.index, sell_prices, color=COLOR_DOWN, marker='v',
                       s=100, zorder=5, label=f'卖出 ({len(sell_signals)}次)', edgecolors='white', linewidth=0.5)

        ax.set_title(title, fontsize=14, fontproperties=FP_BOLD, color=COLOR_DARK)
        ax.set_ylabel('价格', fontsize=11, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax.set_xlabel('日期', fontsize=11, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax.legend(loc='upper left', prop=FP_REGULAR, framealpha=0.9, edgecolor='#ddd')
        ax.grid(True, alpha=0.3, linestyle=':')
        ax.set_facecolor(COLOR_BG)

        fig.tight_layout()
        fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
        plt.close(fig)

        return save_path

    def plot_indicators_dashboard(self, df, title='技术指标仪表盘', save_path=None):
        """
        综合指标仪表盘（多子图：MA+布林带、MACD、RSI、KDJ、成交量）

        Parameters
        ----------
        df : pd.DataFrame
            包含 OHLCV 及各项技术指标数据的 DataFrame
        title : str, 图表标题
        save_path : str, 保存路径

        Returns
        -------
        str : 保存的文件路径
        """
        if save_path is None:
            save_path = self._get_save_path('indicators_dashboard.png')

        # 确保索引为DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'date' in df.columns:
                df = df.set_index('date')
            df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        # 创建5个子图
        fig, axes = plt.subplots(5, 1, figsize=(16, 14), sharex=True,
                                  gridspec_kw={'height_ratios': [2.5, 1.5, 1.5, 1.5, 1.5]})
        fig.patch.set_facecolor(COLOR_BG)

        ax1, ax2, ax3, ax4, ax5 = axes

        # ============================================================
        # 子图1: 价格 + MA + 布林带
        # ============================================================
        ax1.plot(df.index, df['close'], color=COLOR_BLUE, linewidth=1.2, label='收盘价', alpha=0.9)

        # 移动平均线
        ma_colors = {5: COLOR_ORANGE, 10: COLOR_TEAL, 20: COLOR_PURPLE, 60: COLOR_GOLD}
        for ma_period, ma_color in ma_colors.items():
            ma_col = f'MA{ma_period}'
            if ma_col in df.columns:
                ax1.plot(df.index, df[ma_col], color=ma_color, linewidth=0.8, alpha=0.8, label=f'MA{ma_period}')

        # 布林带
        if 'BB_Upper' in df.columns and 'BB_Lower' in df.columns:
            ax1.fill_between(df.index, df['BB_Lower'], df['BB_Upper'],
                             color=COLOR_GRAY, alpha=0.15, label='布林带')
            ax1.plot(df.index, df['BB_Upper'], color=COLOR_GRAY, linewidth=0.6, alpha=0.5)
            ax1.plot(df.index, df['BB_Lower'], color=COLOR_GRAY, linewidth=0.6, alpha=0.5)
        if 'BB_Middle' in df.columns:
            ax1.plot(df.index, df['BB_Middle'], color=COLOR_GRAY, linewidth=0.8, alpha=0.6, linestyle='--')

        ax1.set_title(title, fontsize=14, fontproperties=FP_BOLD, color=COLOR_DARK)
        ax1.set_ylabel('价格', fontsize=10, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax1.legend(loc='upper left', prop=FP_REGULAR, fontsize=8, ncol=3, framealpha=0.9, edgecolor='#ddd')
        ax1.grid(True, alpha=0.3, linestyle=':')
        ax1.set_facecolor(COLOR_BG)

        # ============================================================
        # 子图2: MACD
        # ============================================================
        if 'MACD' in df.columns and 'MACD_Signal' in df.columns and 'MACD_Hist' in df.columns:
            ax2.plot(df.index, df['MACD'], color=COLOR_BLUE, linewidth=1, label='MACD')
            ax2.plot(df.index, df['MACD_Signal'], color=COLOR_ORANGE, linewidth=1, label='Signal')
            # 正负柱分别着色
            for i in range(len(df)):
                val = df['MACD_Hist'].iloc[i]
                if pd.notna(val):
                    color = COLOR_UP if val >= 0 else COLOR_DOWN
                    ax2.bar(df.index[i], val, color=color, width=0.8, alpha=0.7)
            ax2.axhline(y=0, color=COLOR_DARK, linewidth=0.5, linestyle='-')
        ax2.set_ylabel('MACD', fontsize=10, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax2.legend(loc='upper left', prop=FP_REGULAR, fontsize=8, framealpha=0.9, edgecolor='#ddd')
        ax2.grid(True, alpha=0.3, linestyle=':')
        ax2.set_facecolor(COLOR_BG)

        # ============================================================
        # 子图3: RSI
        # ============================================================
        if 'RSI' in df.columns:
            ax3.plot(df.index, df['RSI'], color=COLOR_PURPLE, linewidth=1.2, label='RSI')
            ax3.axhline(y=70, color=COLOR_UP, linewidth=0.8, linestyle='--', alpha=0.6)
            ax3.axhline(y=30, color=COLOR_DOWN, linewidth=0.8, linestyle='--', alpha=0.6)
            ax3.axhline(y=50, color=COLOR_GRAY, linewidth=0.5, linestyle=':', alpha=0.4)
            ax3.fill_between(df.index, 30, 70, color=COLOR_GRAY, alpha=0.05)
            ax3.set_ylim(0, 100)
        ax3.set_ylabel('RSI', fontsize=10, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax3.legend(loc='upper left', prop=FP_REGULAR, fontsize=8, framealpha=0.9, edgecolor='#ddd')
        ax3.grid(True, alpha=0.3, linestyle=':')
        ax3.set_facecolor(COLOR_BG)

        # ============================================================
        # 子图4: KDJ
        # ============================================================
        if 'K' in df.columns and 'D' in df.columns and 'J' in df.columns:
            ax4.plot(df.index, df['K'], color=COLOR_BLUE, linewidth=1, label='K')
            ax4.plot(df.index, df['D'], color=COLOR_ORANGE, linewidth=1, label='D')
            ax4.plot(df.index, df['J'], color=COLOR_PURPLE, linewidth=0.8, alpha=0.7, label='J')
            ax4.axhline(y=80, color=COLOR_UP, linewidth=0.8, linestyle='--', alpha=0.6)
            ax4.axhline(y=20, color=COLOR_DOWN, linewidth=0.8, linestyle='--', alpha=0.6)
            ax4.set_ylim(0, 100)
        ax4.set_ylabel('KDJ', fontsize=10, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax4.legend(loc='upper left', prop=FP_REGULAR, fontsize=8, framealpha=0.9, edgecolor='#ddd')
        ax4.grid(True, alpha=0.3, linestyle=':')
        ax4.set_facecolor(COLOR_BG)

        # ============================================================
        # 子图5: 成交量
        # ============================================================
        if 'Volume' in df.columns:
            # 根据涨跌着色
            for i in range(len(df)):
                if i > 0:
                    color = COLOR_UP if df['close'].iloc[i] >= df['close'].iloc[i-1] else COLOR_DOWN
                else:
                    color = COLOR_UP if df['close'].iloc[i] >= df['open'].iloc[i] else COLOR_DOWN
                ax5.bar(df.index[i], df['volume'].iloc[i], color=color, width=0.8, alpha=0.7)

            # 成交量均线
            if 'Volume_MA' in df.columns:
                ax5.plot(df.index, df['Volume_MA'], color=COLOR_ORANGE, linewidth=1, label='量均线', alpha=0.8)
            elif 'Volume_MA5' in df.columns:
                ax5.plot(df.index, df['Volume_MA5'], color=COLOR_ORANGE, linewidth=1, label='量均线', alpha=0.8)

        ax5.set_ylabel('成交量', fontsize=10, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax5.set_xlabel('日期', fontsize=10, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax5.legend(loc='upper left', prop=FP_REGULAR, fontsize=8, framealpha=0.9, edgecolor='#ddd')
        ax5.grid(True, alpha=0.3, linestyle=':')
        ax5.set_facecolor(COLOR_BG)

        fig.tight_layout()
        fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
        plt.close(fig)

        return save_path

    def plot_compare_strategies(self, results_dict, save_path=None):
        """
        比较多个策略的权益曲线

        Parameters
        ----------
        results_dict : dict
            策略名称到回测结果的映射，如 {'MA交叉': result1, 'MACD': result2}
            每个 result 需包含 'equity_curve' 键
        save_path : str, 保存路径

        Returns
        -------
        str : 保存的文件路径
        """
        if save_path is None:
            save_path = self._get_save_path('compare_strategies.png')

        if not results_dict:
            fig, ax = plt.subplots(figsize=(14, 6))
            ax.text(0.5, 0.5, '无策略数据可比较', ha='center', va='center',
                    fontproperties=FP_REGULAR, fontsize=16, color=COLOR_GRAY)
            fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
            plt.close(fig)
            return save_path

        fig, ax = plt.subplots(figsize=(14, 7))
        fig.patch.set_facecolor(COLOR_BG)

        # 绘制每个策略的权益曲线
        for i, (name, result) in enumerate(results_dict.items()):
            equity = result.get('equity_curve', None)
            if equity is not None:
                color = STRATEGY_COLORS[i % len(STRATEGY_COLORS)]
                if isinstance(equity, pd.Series):
                    ax.plot(equity.index, equity.values, color=color, linewidth=1.5, label=name, alpha=0.9)
                else:
                    ax.plot(equity, color=color, linewidth=1.5, label=name, alpha=0.9)

        ax.set_title('策略权益曲线对比', fontsize=14, fontproperties=FP_BOLD, color=COLOR_DARK)
        ax.set_ylabel('权益', fontsize=11, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax.set_xlabel('时间', fontsize=11, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax.legend(loc='upper left', prop=FP_REGULAR, fontsize=10, framealpha=0.9, edgecolor='#ddd')
        ax.grid(True, alpha=0.3, linestyle=':')
        ax.set_facecolor(COLOR_BG)

        fig.tight_layout()
        fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
        plt.close(fig)

        return save_path

    def plot_risk_heatmap(self, risk_metrics, save_path=None):
        """
        风险指标热力图

        Parameters
        ----------
        risk_metrics : dict
            风险指标数据，格式如 {'策略1': {'夏普比率': 1.5, '最大回撤': -0.15, ...}, ...}
            或 {'夏普比率': 1.5, '最大回撤': -0.15, ...}（单个策略时）
        save_path : str, 保存路径

        Returns
        -------
        str : 保存的文件路径
        """
        if save_path is None:
            save_path = self._get_save_path('risk_heatmap.png')

        if not risk_metrics:
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.text(0.5, 0.5, '无风险指标数据', ha='center', va='center',
                    fontproperties=FP_REGULAR, fontsize=16, color=COLOR_GRAY)
            fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
            plt.close(fig)
            return save_path

        # 判断是单策略还是多策略
        first_value = list(risk_metrics.values())[0]
        if isinstance(first_value, dict):
            # 多策略格式
            strategies = list(risk_metrics.keys())
            metric_names = list(first_value.keys())
            data = np.zeros((len(strategies), len(metric_names)))
            for i, strategy in enumerate(strategies):
                for j, metric in enumerate(metric_names):
                    data[i, j] = risk_metrics[strategy].get(metric, 0)
        else:
            # 单策略格式
            strategies = ['策略']
            metric_names = list(risk_metrics.keys())
            data = np.zeros((1, len(metric_names)))
            for j, metric in enumerate(metric_names):
                data[0, j] = risk_metrics.get(metric, 0)

        fig, ax = plt.subplots(figsize=(max(8, len(metric_names) * 1.5), max(4, len(strategies) * 0.8)))
        fig.patch.set_facecolor(COLOR_BG)

        # 绘制热力图
        im = ax.imshow(data, cmap='RdYlGn', aspect='auto', vmin=None, vmax=None)

        # 标注数值
        for i in range(len(strategies)):
            for j in range(len(metric_names)):
                val = data[i, j]
                text_color = 'white' if abs(val) > (np.nanmax(np.abs(data)) * 0.5) else 'black'
                ax.text(j, i, f'{val:.3f}', ha='center', va='center',
                        fontproperties=FP_REGULAR, fontsize=10,
                        color=text_color, fontweight='bold')

        # 设置轴标签
        ax.set_xticks(range(len(metric_names)))
        ax.set_xticklabels(metric_names, fontsize=10, fontproperties=FP_REGULAR, rotation=30, ha='right')
        ax.set_yticks(range(len(strategies)))
        ax.set_yticklabels(strategies, fontsize=10, fontproperties=FP_REGULAR)

        ax.set_title('风险指标热力图', fontsize=14, fontproperties=FP_BOLD, color=COLOR_DARK)

        # 添加颜色条
        cbar = fig.colorbar(im, ax=ax, shrink=0.8)
        cbar.set_label('数值', fontsize=10, fontproperties=FP_REGULAR)

        fig.tight_layout()
        fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
        plt.close(fig)

        return save_path

    def plot_monthly_returns_heatmap(self, daily_returns, save_path=None):
        """
        月度收益热力图

        Parameters
        ----------
        daily_returns : pd.Series
            日收益率序列，索引为日期
        save_path : str, 保存路径

        Returns
        -------
        str : 保存的文件路径
        """
        if save_path is None:
            save_path = self._get_save_path('monthly_returns_heatmap.png')

        if daily_returns is None or len(daily_returns) == 0:
            fig, ax = plt.subplots(figsize=(12, 8))
            ax.text(0.5, 0.5, '无收益数据', ha='center', va='center',
                    fontproperties=FP_REGULAR, fontsize=16, color=COLOR_GRAY)
            fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
            plt.close(fig)
            return save_path

        # 确保索引为DatetimeIndex
        if not isinstance(daily_returns.index, pd.DatetimeIndex):
            daily_returns.index = pd.to_datetime(daily_returns.index)

        # 计算月度收益率
        monthly_returns = daily_returns.resample('ME').apply(lambda x: (1 + x).prod() - 1)

        # 构建年份-月份矩阵
        monthly_df = monthly_returns.to_frame('return')
        monthly_df['year'] = monthly_df.index.year
        monthly_df['month'] = monthly_df.index.month

        # 透视表
        pivot = monthly_df.pivot_table(values='return', index='year', columns='month', aggfunc='sum')

        # 确保所有月份列
        for m in range(1, 13):
            if m not in pivot.columns:
                pivot[m] = np.nan
        pivot = pivot[sorted(pivot.columns)]

        # 月份标签
        month_labels = ['1月', '2月', '3月', '4月', '5月', '6月',
                        '7月', '8月', '9月', '10月', '11月', '12月']

        fig, ax = plt.subplots(figsize=(14, max(4, len(pivot) * 0.6)))
        fig.patch.set_facecolor(COLOR_BG)

        # 绘制热力图
        im = ax.imshow(pivot.values * 100, cmap='RdYlGn', aspect='auto', vmin=-np.nanmax(np.abs(pivot.values * 100)),
                       vmax=np.nanmax(np.abs(pivot.values * 100)))

        # 标注数值
        for i in range(len(pivot)):
            for j in range(12):
                val = pivot.iloc[i, j]
                if not np.isnan(val):
                    text_color = 'white' if abs(val * 100) > 5 else 'black'
                    ax.text(j, i, f'{val*100:.1f}%', ha='center', va='center',
                            fontproperties=FP_REGULAR, fontsize=9,
                            color=text_color, fontweight='bold')

        ax.set_xticks(range(12))
        ax.set_xticklabels(month_labels, fontsize=10, fontproperties=FP_REGULAR)
        ax.set_yticks(range(len(pivot)))
        ax.set_yticklabels(pivot.index.astype(int), fontsize=10, fontproperties=FP_REGULAR)

        ax.set_title('月度收益率热力图', fontsize=14, fontproperties=FP_BOLD, color=COLOR_DARK)
        ax.set_xlabel('月份', fontsize=11, fontproperties=FP_REGULAR, color=COLOR_DARK)
        ax.set_ylabel('年份', fontsize=11, fontproperties=FP_REGULAR, color=COLOR_DARK)

        # 颜色条
        cbar = fig.colorbar(im, ax=ax, shrink=0.8)
        cbar.set_label('收益率 (%)', fontsize=10, fontproperties=FP_REGULAR)

        fig.tight_layout()
        fig.savefig(save_path, dpi=150, bbox_inches='tight', facecolor=COLOR_BG)
        plt.close(fig)

        return save_path
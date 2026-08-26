# 股票量化分析软件 - CLI 主入口
# 提供命令行交互界面，支持股票分析、回测、策略对比等功能

import click
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
import sys
import os
import json
import io
import contextlib
import fnmatch
import warnings
import re

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入核心模块
from core.data_fetcher import DataFetcher
from core.indicators import add_all_indicators, classify_trend
from core.backtest import BacktestEngine
from core.risk import risk_report
from core.strategy import Strategy
from strategies.ma_cross import MACrossStrategy
from strategies.ema_cross import EMACrossStrategy
from strategies.macd_strategy import MACDStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.bollinger_strategy import BollingerStrategy
from strategies.kdj_strategy import KDJStrategy
from strategies.quality_value_factor import QualityValueFactorStrategy
from strategies.momentum_tiered import MomentumTieredStrategy
from strategies.volatility_timing import VolatilityTimingStrategy
from strategies.breadth_confirmation import BreadthConfirmationStrategy
from visualization.charts import ChartGenerator

# 导入表格格式化库
try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

# 忽略非关键警告
warnings.filterwarnings('ignore')

# ============================================================================
# 策略注册表
# ============================================================================

STRATEGY_MAP = {
    'ma_cross': ('均线交叉', MACrossStrategy),
    'ema_cross': ('EMA交叉', EMACrossStrategy),
    'macd': ('MACD策略', MACDStrategy),
    'rsi': ('RSI策略', RSIStrategy),
    'bollinger': ('布林带策略', BollingerStrategy),
    'kdj': ('KDJ策略', KDJStrategy),
    'quality_value': ('质价融合策略', QualityValueFactorStrategy),
}

INDEX_STRATEGY_MAP = {
    'momentum': ('动量分层策略', MomentumTieredStrategy),
    'volatility': ('波动率择时策略', VolatilityTimingStrategy),
    'breadth': ('涨跌比确认策略', BreadthConfirmationStrategy),
}

ALL_STRATEGIES = ['ma_cross', 'ema_cross', 'macd', 'rsi', 'bollinger', 'kdj', 'quality_value']
ALL_INDEX_STRATEGIES = ['momentum', 'volatility', 'breadth']

# scan 多策略汇总信号表使用的短栏名（仅用于汇总表列头，不影响其它输出）
_SCAN_SHORT_NAMES = {
    'ma_cross': '均线交叉',
    'ema_cross': 'EMA交叉',
    'macd': 'MACD',
    'rsi': 'RSI',
    'bollinger': 'BOLL',
    'kdj': 'KDJ',
    'quality_value': '多因子',
}

# ETF/指数基金专用策略（适配低波动、趋势跟随特性）
ETF_STRATEGY_MAP = {
    'ma_cross': ('ETF均线交叉策略', lambda: MACrossStrategy(fast_period=10, slow_period=40, name='ETF MACross')),
    'ema_cross': ('ETF EMA交叉策略', lambda: EMACrossStrategy(fast_period=10, slow_period=40, name='ETF EMACross')),
    'macd': ('ETF MACD策略', lambda: MACDStrategy(fast=16, slow=32, signal=12, name='ETF MACD')),
    'rsi': ('ETF RSI策略', lambda: RSIStrategy(period=14, oversold=35, overbought=65, name='ETF RSI')),
    'bollinger': ('ETF布林带策略', lambda: BollingerStrategy(period=20, std=2.5, name='ETF Bollinger')),
    'kdj': ('ETF KDJ策略', lambda: KDJStrategy(n=9, m1=3, m2=3, oversold=20, overbought=80, name='ETF KDJ')),
    'quality_value': ('ETF质价融合策略', lambda: QualityValueFactorStrategy(stock_type='auto', name='ETF QualityValue')),
}
ALL_ETF_STRATEGIES = ['ma_cross', 'ema_cross', 'macd', 'rsi', 'bollinger', 'kdj', 'quality_value']

# 策略 key 到类的映射
_STRATEGY_CLASS_MAP = {
    'ma_cross': MACrossStrategy,
    'ema_cross': EMACrossStrategy,
    'macd': MACDStrategy,
    'rsi': RSIStrategy,
    'bollinger': BollingerStrategy,
    'kdj': KDJStrategy,
    'quality_value': QualityValueFactorStrategy,
    'momentum': MomentumTieredStrategy,
    'volatility': VolatilityTimingStrategy,
    'breadth': BreadthConfirmationStrategy,
}

_quant_config = None


def _load_quant_config():
    """加载 stock-quant.json 配置文件，失败时返回空字典"""
    global _quant_config
    if _quant_config is not None:
        return _quant_config
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'input', 'stock-quant.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            _quant_config = json.load(f)
    except Exception:
        _quant_config = {}
    return _quant_config


# 判断是否为 ETF/指数基金（5开头上海ETF / 159开头深圳ETF / 16开头LOF等）
def _is_etf(symbol):
    return symbol.startswith(('5', '15', '16', '51', '58', '588')) or symbol[:3] == '159'


def _is_market_open():
    """判断当前是否处于 A 股交易时段（9:30-11:30, 13:00-15:00，周一至周五）"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    morning_start = datetime.strptime('09:30', '%H:%M').time()
    morning_end = datetime.strptime('11:30', '%H:%M').time()
    afternoon_start = datetime.strptime('13:00', '%H:%M').time()
    afternoon_end = datetime.strptime('15:00', '%H:%M').time()
    return (morning_start <= t <= morning_end) or (afternoon_start <= t <= afternoon_end)

# ============================================================================
# 辅助函数
# ============================================================================


def _print_header(title, symbol=''):
    """打印格式化的报告头部"""
    click.echo()
    click.echo(click.style('=' * 56, fg='cyan', bold=True))
    click.echo(click.style(f'  {title}', fg='cyan', bold=True))
    if symbol:
        click.echo(click.style(f'  股票: {symbol}', fg='cyan', bold=True))
    click.echo(click.style('=' * 56, fg='cyan', bold=True))
    click.echo()


def _print_section(title):
    """打印章节标题"""
    click.echo(click.style(f'\n  {title}', fg='yellow', bold=True))
    click.echo(click.style('  ' + '-' * 40, fg='yellow'))


def _print_table(headers, rows, tablefmt='grid'):
    """使用 tabulate 格式化打印表格"""
    if HAS_TABULATE:
        click.echo(tabulate(rows, headers=headers, tablefmt=tablefmt, floatfmt='.4f'))
    else:
        # 简单格式化备选
        click.echo('  ' + ' | '.join(headers))
        click.echo('  ' + '-' * (len(' | '.join(headers)) + 4))
        for row in rows:
            click.echo('  ' + ' | '.join(str(v) for v in row))


def _print_metric(name, value, color='white'):
    """打印单个指标"""
    click.echo(f'  {name:<20s} {click.style(str(value), fg=color, bold=True)}')


def _run_strategy(strategy_key, df, capital=100000, is_index=False, enhance=0):
    """运行单个策略并返回回测结果"""
    strategy_map = INDEX_STRATEGY_MAP if is_index else STRATEGY_MAP
    strategy_name, strategy_class = strategy_map[strategy_key]
    strategy = _build_strategy_instance(strategy_key, strategy_map, enhance)
    signals = strategy.generate_signals(df)
    engine = BacktestEngine(initial_capital=capital)
    result = engine.run(df, signals, position_style='fraction')
    result['strategy_name'] = strategy_name
    result['strategy_key'] = strategy_key
    result['enhance'] = enhance
    result['signals'] = signals

    # 运行风险分析
    risk = risk_report(result['daily_returns'].dropna(), result['equity_curve'])
    # 合并风险指标到结果中
    result.update(risk)

    return result


def _run_single_analysis(df, strategy_key, capital, chart_gen, prefix='', strategy_map=None, enhance=0):
    """运行单个策略的完整分析流程（图表在外部统一生成）"""
    if strategy_map is None:
        strategy_map = STRATEGY_MAP
    strategy_name, strategy_class = strategy_map[strategy_key]
    strategy = _build_strategy_instance(strategy_key, strategy_map, enhance)

    signals = strategy.generate_signals(df)

    engine = BacktestEngine(initial_capital=capital)
    result = engine.run(df, signals, position_style='fraction')

    risk = risk_report(result['daily_returns'].dropna(), result['equity_curve'])
    result.update(risk)

    return result, {}, strategy_name, signals


def _parse_dates(start, end):
    """解析日期字符串，返回默认值

    - 缺少 -e 时，结束日期默认为今天。
    - 缺少 -st 时，开始日期默认为结束日期前 365 天。
    对 start/end 做格式校验并统一为 YYYY-MM-DD，非法时抛出明确错误。
    start/end 输入格式为 YYYYMMDD（如 20240815）。
    """
    def _normalize_date(d):
        d = str(d).strip()
        for fmt in ('%Y%m%d', '%Y-%m-%d'):
            try:
                return datetime.strptime(d, fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
        raise click.ClickException(f'日期格式不正确: {d}，期望格式 YYYYMMDD')

    if end is not None:
        end_date = _normalize_date(end)
    else:
        end_date = datetime.now().strftime('%Y-%m-%d')

    end_dt = datetime.strptime(end_date, '%Y-%m-%d')

    if start is not None:
        start_date = _normalize_date(start)
    else:
        start_date = (end_dt - timedelta(days=365)).strftime('%Y-%m-%d')
    return start_date, end_date


def _build_date_tag(start_date, end_date):
    """构建文件名日期标签: yyyymmdd_天数d（天数 = 起始到结束的日期差）"""
    tag = end_date.replace('-', '') if end_date else datetime.now().strftime('%Y%m%d')
    if start_date and end_date:
        try:
            days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
            tag = f'{tag}_{days}d'
        except Exception:
            pass
    return tag


def _profit_int(return_value):
    """收益率转百分数整数（0.675 -> 67），None 返回 None"""
    if return_value is None:
        return None
    return int(return_value * 100)


def _calc_sharpe_drawdown(equity_curve):
    """从权益曲线计算夏普比率与最大回撤（返回 (sharpe, max_drawdown)）"""
    eq = pd.Series([float(v) for v in equity_curve])
    if len(eq) < 2:
        return 0.0, 0.0
    rets = eq.pct_change().dropna()
    if len(rets) > 1 and np.std(rets, ddof=1) > 0:
        excess = rets - 0.03 / 252
        sharpe = float(np.mean(excess) / np.std(rets, ddof=1) * np.sqrt(252))
    else:
        sharpe = 0.0
    cummax = eq.cummax()
    drawdowns = (eq - cummax) / cummax
    max_dd = float(np.abs(np.min(drawdowns))) if len(drawdowns) else 0.0
    return sharpe, max_dd


def _build_report_filename(source, end_date, days, strategy, mode, profit=None):
    """构建报告文件名: source_yyyymmdd_days_strategy_mode_profit.html"""
    d2 = end_date.replace('-', '') if end_date else datetime.now().strftime('%Y%m%d')
    strategy_tag = str(strategy).replace('|', '_')
    parts = [str(source), d2, f'{days}d', strategy_tag, f'm{mode}']
    if profit is not None:
        parts.append(str(profit))
    return '_'.join(parts) + '.html'


def _format_cmd_params(symbol, symbol_file, hot, start, end, strategy, is_index,
                       capital, output, output_file, threads, db, mode,
                       filter_signal=None, chart=False, forecast=0):
    """构建命令选项参数摘要字符串，用于在报告中标注本次运行所用的选项"""
    parts = ['analyze']
    if symbol:
        parts.append(f'-s "{symbol}"')
    if symbol_file:
        parts.append(f'-sf "{symbol_file}"')
    if hot:
        parts.append(f'-h {hot}')
    if start:
        parts.append(f'-st {start}')
    if end:
        parts.append(f'-e {end}')
    parts.append(f'-g {strategy}')
    if is_index:
        parts.append('-i')
    parts.append(f'-c {capital / 10000:g}')
    parts.append(f'-o "{output}"')
    if output_file:
        parts.append(f'-of "{output_file}"')
    parts.append(f'-x{threads}')
    parts.append(f'-db {db}')
    parts.append(f'-m {mode}')
    if filter_signal:
        parts.append(f'-f {filter_signal}')
    if chart:
        parts.append('--chart')
    if forecast:
        parts.append('--forecast')
    return ' '.join(parts)


def _format_risk_report_rows(risk):
    """格式化风险报告为表格行"""
    rows = []

    def _safe_pct(v):
        if v is None:
            return 'N/A'
        return f'{v * 100:.2f}%'

    def _safe_float(v, fmt='.2f'):
        if v is None:
            return 'N/A'
        return f'{v:{fmt}}'

    rows.append(['总收益率', _safe_pct(risk.get('total_return'))])
    rows.append(['年化收益率', _safe_pct(risk.get('annual_return'))])
    rows.append(['最大回撤', _safe_pct(risk.get('max_drawdown'))])
    rows.append(['夏普比率', _safe_float(risk.get('sharpe_ratio'))])
    rows.append(['胜率', _safe_pct(risk.get('win_rate'))])
    rows.append(['买入/卖出次数', _fmt_trade_count(risk)])
    rows.append(['盈利因子', _safe_float(risk.get('profit_factor'))])

    return rows


def _get_signal_text(sig_series):
    """从信号序列中提取最新信号文本"""
    if sig_series is None or len(sig_series) == 0:
        return 'N/A'
    last_sig = sig_series.iloc[-1] if hasattr(sig_series, 'iloc') else sig_series[-1]
    sig_map = {1.0: '买入', 0.5: '弱买', 0.0: '观望', -0.5: '弱卖', -1.0: '卖出'}
    return sig_map.get(float(last_sig), str(last_sig))


def _get_signal_color(sig_text):
    """获取信号对应的颜色"""
    return {'买入': 'green', '弱买': 'green', '卖出': 'red', '弱卖': 'red', '观望': 'yellow'}.get(sig_text, 'white')


def _fmt_trade_count(risk, avg_buy=None, avg_sell=None):
    """格式化买入/卖出次数为 '买入/卖出'（如 2.0/3.2）"""
    buy = avg_buy if avg_buy is not None else risk.get('buy_count')
    sell = avg_sell if avg_sell is not None else risk.get('sell_count')
    if buy is None or sell is None:
        return str(risk.get('total_trades', 'N/A'))
    return f'{buy:.1f}/{sell:.1f}'


# 信号过滤映射：-f 选项字符 -> 信号文本
_FILTER_SIGNAL_TEXT = {'b': '买入', 's': '卖出', 'w': '观望'}


def _has_actionable_signal(all_signals):
    """判断是否存在至少一个策略的最新信号不是观望（买/卖/弱买/弱卖）"""
    for sig in all_signals.values():
        if sig is None or len(sig) == 0:
            continue
        last = float(sig.iloc[-1]) if hasattr(sig, 'iloc') else float(sig[-1])
        if last != 0.0:
            return True
    return False


def _get_primary_signal_value(all_signals, all_risks=None):
    """确定股票的主信号值：选最佳收益策略的信号

    Args:
        all_signals: {策略key: 信号序列}
        all_risks: {策略key: 风险指标dict}，用于选出最佳策略

    Returns:
        float: 主信号值 1/0.5/0/-0.5/-1，无法确定时返回 None
    """
    if not all_signals:
        return None
    best_sk = None
    best_ret = -999
    for key in all_signals:
        r = (all_risks or {}).get(key, {}).get('total_return')
        if r is not None and (r or 0) > best_ret:
            best_ret = r or 0
            best_sk = key
    sk = best_sk if best_sk is not None else next(iter(all_signals))
    sig = all_signals.get(sk)
    if sig is None or len(sig) == 0:
        return None
    return float(sig.iloc[-1]) if hasattr(sig, 'iloc') else float(sig[-1])


def _match_filter(filter_signal, all_signals, all_risks=None):
    """判断股票是否满足 -f 信号过滤条件（未设置过滤时恒为 True）

    b=买入(强买1/弱买0.5)，s=卖出(强卖-1/弱卖-0.5)，w=观望(0)。
    """
    if not filter_signal:
        return True
    val = _get_primary_signal_value(all_signals, all_risks)
    if val is None:
        return False
    if filter_signal == 'b':
        return val > 0
    if filter_signal == 's':
        return val < 0
    return val == 0


def _generate_html_report(symbol, sname, start_date, end_date, capital, is_index,
                          strategy_map, strategies_to_run, all_results, all_risks, all_charts,
                          all_signals=None, latest_close=None, latest_date=None,
                          trading_days=None, report_filename=None, cmd_params='',
                          strategy=None, mode=0):
    """生成 HTML 分析报告"""
    import base64
    from datetime import datetime as dt

    if all_signals is None:
        all_signals = {}

    def _safe_pct_html(v):
        if v is None:
            return 'N/A'
        return f'{v * 100:.2f}%'

    def _safe_float_html(v, fmt='.2f'):
        if v is None:
            return 'N/A'
        return f'{v:{fmt}}'

    def _img_to_b64(path):
        if not path or not os.path.exists(path):
            return ''
        with open(path, 'rb') as f:
            return base64.b64encode(f.read()).decode()

    mode_label = '指数专属' if is_index else '个股通用'
    report_time = dt.now().strftime('%Y-%m-%d %H:%M:%S')

    # 拼排名表
    rankings = []
    for sk in strategies_to_run:
        r = all_risks.get(sk, {})
        sig_text = _get_signal_text(all_signals.get(sk))
        rankings.append((
            strategy_map[sk][0],
            r.get('total_return'),
            r.get('sharpe_ratio'),
            r.get('max_drawdown'),
            r.get('buy_count'),
            r.get('sell_count'),
            r.get('annual_return'),
            r.get('win_rate'),
            r.get('profit_factor'),
            sig_text,
        ))
    rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

    rank_rows_html = ''
    for i, (name, tr, sh, dd, bc, sc, ar, wr, pf, sig) in enumerate(rankings, 1):
        rank_rows_html += f'''<tr>
            <td>{i}</td><td>{name}</td>
            <td>{_safe_pct_html(tr)}</td><td>{_safe_float_html(sh)}</td>
            <td>{_safe_pct_html(dd)}</td><td>{f'{bc or 0:.1f}/{sc or 0:.1f}'}</td>
            <td>{_safe_pct_html(ar)}</td><td>{_safe_pct_html(wr)}</td>
            <td>{_safe_float_html(pf)}</td>
            <td style="font-weight:bold;color:{'#27ae60' if sig == '买入' else '#e74c3c' if sig == '卖出' else '#f39c12'}">{sig}</td>
        </tr>'''

    # 各策略详情
    detail_html = ''
    # 已嵌入的图表路径（合并后的多策略图表只需展示一次）
    embedded_chart_paths = set()

    for sk in strategies_to_run:
        name = strategy_map[sk][0]
        risk = all_risks.get(sk, {})
        charts = all_charts.get(sk, {})

        detail_html += f'<h3>{name}</h3>'

        # 最新信号
        sig_text = _get_signal_text(all_signals.get(sk))
        sig_color_html = {'买入': '#27ae60', '卖出': '#e74c3c', '观望': '#f39c12'}.get(sig_text, '#2c3e50')
        detail_html += f'<div class="signal" style="color:{sig_color_html};font-weight:bold;font-size:16px;margin:10px 0;">最新信号: {sig_text}</div>'

        detail_html += '<table class="detail"><tr><th>指标</th><th>数值</th></tr>'
        for label, func in [
            ('总收益率', lambda: _safe_pct_html(risk.get('total_return'))),
            ('年化收益率', lambda: _safe_pct_html(risk.get('annual_return'))),
            ('最大回撤', lambda: _safe_pct_html(risk.get('max_drawdown'))),
            ('夏普比率', lambda: _safe_float_html(risk.get('sharpe_ratio'))),
            ('胜率', lambda: _safe_pct_html(risk.get('win_rate'))),
            ('买入/卖出次数', lambda: _fmt_trade_count(risk)),
            ('盈利因子', lambda: _safe_float_html(risk.get('profit_factor'))),
        ]:
            detail_html += f'<tr><td>{label}</td><td>{func()}</td></tr>'
        detail_html += '</table>'

        for cname, cpath in charts.items():
            if cpath in embedded_chart_paths:
                continue  # 合并后的多策略图表已在前面策略中展示，跳过重复
            embedded_chart_paths.add(cpath)
            b64 = _img_to_b64(cpath)
            if b64:
                ext = os.path.splitext(cpath)[1].lstrip('.')
                detail_html += f'<div class="chart"><img src="data:image/{ext};base64,{b64}" alt="{cname}"></div>'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>量化分析报告 - {symbol} {sname}</title>
<style>
body {{ font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #fafafa; color: #2c3e50; }}
h1 {{ border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
h2 {{ border-bottom: 2px solid #bdc3c7; padding-bottom: 6px; margin-top: 30px; }}
h3 {{ color: #2980b9; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: center; }}
th {{ background: #3498db; color: white; }}
tr:nth-child(even) {{ background: #f2f2f2; }}
.info {{ background: #ecf0f1; padding: 15px; border-radius: 5px; margin: 15px 0; }}
.info span {{ margin-right: 30px; }}
.chart {{ margin: 20px 0; text-align: center; }}
.chart img {{ max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; }}
.footer {{ text-align: center; color: #95a5a6; margin-top: 40px; font-size: 12px; }}
</style>
</head>
<body>
<h1>股票量化分析报告</h1>
<div class="info">
    <span><strong>股票:</strong> {symbol} {sname}</span>
    <span><strong>区间:</strong> {start_date} ~ {end_date}</span>
    <span><strong>初始资金:</strong> {capital:,.0f}</span>
    <span><strong>模式:</strong> {mode_label}</span>
    <span><strong>分析周期:</strong> {start_date} ~ {end_date}</span>
    <span><strong>生成时间:</strong> {report_time}</span>
</div>
<div class="info">
    <span><strong>最新价格:</strong> {f'{latest_close:.2f}' if latest_close is not None else 'N/A'} <small>({str(latest_date.date()) if latest_date is not None else 'N/A'})</small></span>
</div>
<div class="info">
    <span><strong>命令参数:</strong> {cmd_params}</span>
</div>

<h2>策略对比排名</h2>
<table class="rank">
<tr><th>排名</th><th>策略</th><th>总收益率</th><th>夏普比率</th><th>最大回撤</th><th>买入/卖出次数</th><th>年化收益率</th><th>胜率</th><th>盈利因子</th><th>最新信号</th></tr>
{rank_rows_html}
</table>

<h2>各策略详情</h2>
{detail_html}

<div class="footer"><p>报告由 stock-quant 自动生成 | {report_time}</p></div>
</body>
</html>'''

    # 最佳策略收益率（用于文件名）
    best_return = None
    for sk in strategies_to_run:
        tr = all_risks.get(sk, {}).get('total_return')
        if tr is not None and (best_return is None or tr > best_return):
            best_return = tr

    if report_filename:
        report_path = report_filename
    else:
        days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
        report_path = _build_report_filename(symbol, end_date, days, strategy, mode,
                                             profit=_profit_int(best_return))
    if not os.path.dirname(report_path):
        report_path = os.path.join('report', report_path)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


def _generate_scan_stock_charts(top_results, strat_keys, name_map, prefix_tag='', strat_enhance=None):
    """为 scan 的 top N 股票生成个股图表，返回可嵌入 HTML 的片段

    每只股票仅生成一个综合K线图：各策略指标子图合并到一个K线图，
    各策略权益曲线合并到一个权益子图（不同颜色曲线）。

    top_results: 扫描结果列表（含 symbol 等字段）
    strat_keys: 策略 key 列表
    name_map: symbol -> 名称
    返回: dict {symbol: [base64图html片段...]}，生成失败返回空
    """
    import base64
    from datetime import datetime as dt

    charts_by_symbol = {}
    fetcher = DataFetcher()

    def _img_b64(path):
        if not path or not os.path.exists(path):
            return ''
        with open(path, 'rb') as f:
            return base64.b64encode(f.read()).decode()

    for r in top_results:
        symbol = r.get('symbol')
        if not symbol:
            continue
        sn = name_map.get(symbol, symbol)
        try:
            start_date = (dt.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            end_date = dt.now().strftime('%Y-%m-%d')
            df = fetcher.get_stock_data(symbol, start_date, end_date)
            if df is None or len(df) < 100:
                continue
            df, _, _, _ = _apply_realtime_to_df(df, symbol, fetcher, end_date, verbose=False)
            df = add_all_indicators(df)

            # 运行各策略，收集信号与权益曲线
            strategies_data = []
            if strat_enhance is None:
                strat_enhance = {}
            for sk in strat_keys:
                try:
                    result = _run_strategy(sk, df, capital=100000, enhance=strat_enhance.get(sk, 0))
                    signals = result.get('signals')
                    if signals is None or len(signals) == 0:
                        continue
                    strategies_data.append({
                        'key': sk,
                        'name': STRATEGY_MAP[sk][0],
                        'signals': signals,
                        'equity_curve': result.get('equity_curve'),
                    })
                except Exception:
                    continue

            if not strategies_data:
                continue

            # 每只股票仅生成一个综合图表
            cg = ChartGenerator(output_dir='output', prefix=f'scan_{prefix_tag}_{symbol}', date_tag='')
            chart_path = cg.plot_multi_strategy_composite(
                df, strategies_data,
                title=f'多策略回测效果 ({symbol} {sn})'.replace('  ', ' '),
            )
            b64 = _img_b64(chart_path)
            if b64:
                ext = os.path.splitext(chart_path)[1].lstrip('.')
                charts_by_symbol[symbol] = [
                    f'<div class="chart"><img src="data:image/{ext};base64,{b64}" '
                    f'style="max-width:100%;border:1px solid #ddd;border-radius:4px;"></div>'
                ]
        except Exception:
            continue
    return charts_by_symbol


def _generate_scan_html_report(headers, table_rows, strat_names, strat_cols, charts_by_symbol=None, name_map=None, report_filename=None):
    """生成 scan 扫描结果的 HTML 报告（含 top N 表格 + 个股图表）"""
    from datetime import datetime as dt
    report_time = dt.now().strftime('%Y-%m-%d %H:%M:%S')

    html_rows = ''
    for row in table_rows:
        cells = ''.join(f'<td>{v}</td>' for v in row)
        html_rows += f'<tr>{cells}</tr>\n'

    strat_label = ', '.join(strat_cols if strat_cols else strat_names)

    # 个股图表区
    detail_html = ''
    if charts_by_symbol:
        for symbol, charts in charts_by_symbol.items():
            sn = (name_map or {}).get(symbol, symbol)
            detail_html += f'<h2>{symbol} {sn}</h2>'
            for ch in charts:
                detail_html += ch

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>股票扫描报告</title>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; margin: 20px; color: #333; }}
h1 {{ color: #1a5276; }}
h2 {{ color: #1a5276; border-bottom: 1px solid #ccc; padding-bottom: 5px; margin-top: 30px; }}
.info {{ color: #666; margin-bottom: 10px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 15px; font-size: 14px; }}
th, td {{ border: 1px solid #ccc; padding: 8px; text-align: center; }}
th {{ background: #1a5276; color: #fff; }}
tr:nth-child(even) {{ background: #f5f6fa; }}
.chart {{ margin: 15px 0; text-align: center; }}
.chart h3 {{ color: #333; margin-bottom: 5px; }}
</style>
</head>
<body>
<h1>股票扫描报告</h1>
<div class="info">
    <span><strong>策略:</strong> {strat_label}</span>
    <span style="margin-left:20px;"><strong>生成时间:</strong> {report_time}</span>
</div>
<h2>扫描结果 - Top {len(table_rows)}</h2>
<table>
<tr>{''.join(f'<th>{h}</th>' for h in headers)}</tr>
{html_rows}
</table>
{detail_html}
</body>
</html>'''

    if not report_filename:
        report_filename = f'scan_{dt.now().strftime("%Y%m%d_%H%M%S")}.html'
    os.makedirs('report', exist_ok=True)
    report_path = os.path.join('report', report_filename)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


def _build_signal_table(all_stock_data, include_all=False):
    """构建最新信号表数据，返回 (strategy_keys, strategy_names, rows, excluded)

    rows 每行为 [股票标签, 最新价格, 各策略最新信号文本(买/卖/-/N/A)...]
    include_all=False 时仅列示至少有一项策略信号不是"观望"（买/卖）的股票，
    include_all=True 时列出所有股票（不隐藏全部观望的股票）。
    excluded 为被过滤掉的股票数。
    观望单元格附带次日触发涨跌幅阈值（若启用 --forecast），如 "买≥1%/卖≤-1%"。
    """
    strategy_keys = []
    for d in all_stock_data:
        for sk in d.get('strategies_to_run', []):
            if sk not in strategy_keys:
                strategy_keys.append(sk)

    strategy_names = {}
    for d in all_stock_data:
        smap = d['strategy_map']
        for sk in strategy_keys:
            if sk not in strategy_names and sk in smap:
                strategy_names[sk] = smap[sk]

    def _signal_text(sig_series, th=None):
        if sig_series is None or len(sig_series) == 0:
            return 'N/A'
        last = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])
        if last == 1.0:
            return '买'
        if last == 0.5:
            return '弱买'
        if last == -1.0:
            return '卖'
        if last == -0.5:
            return '弱卖'
        # 观望：附带次日触发涨跌百分比阈值
        if th:
            parts = []
            if th.get('buy') is not None:
                parts.append(f'买≥{th["buy"]:.0f}%')
            if th.get('sell') is not None:
                parts.append(f'卖≤{th["sell"]:.0f}%')
            if parts:
                return '/'.join(parts)
        return '-'

    rows = []
    excluded = 0
    for d in all_stock_data:
        s = d['symbol']
        sn = d['stock_name']
        label = f'{s} {sn}'.strip()
        lc = d.get('latest_close')
        price_str = f'{lc:.2f}' if lc is not None else 'N/A'
        sigs = d.get('all_signals', {})
        fcs = d.get('forecast_thresholds', {})
        cells = [_signal_text(sigs.get(sk), fcs.get(sk)) for sk in strategy_keys]
        if not any(c in ('买', '卖', '弱买', '弱卖') for c in cells):
            excluded += 1
            if not include_all:
                continue
        rows.append([label, price_str] + cells)

    return strategy_keys, strategy_names, rows, excluded


def _print_signal_table(all_stock_data):
    """在屏幕打印最新信号表"""
    strategy_keys, strategy_names, rows, excluded = _build_signal_table(all_stock_data)
    headers = ['股票代码名称', '最新价格'] + [strategy_names.get(sk, sk) for sk in strategy_keys]
    _print_section('最新信号表')
    _print_table(headers, rows)
    if excluded:
        click.echo(click.style(f'  备注: 未列示股票 {excluded} 只，其全部策略信号均为"观望"。', fg='yellow'))


def _generate_multi_html_report(all_stock_data, strategy, is_index, capital, report_filename=None, end_date=None, start_date=None, cmd_params='', source=None, mode=0):
    """生成多股票汇总 HTML 报告"""
    import base64
    from datetime import datetime as dt

    report_time = dt.now().strftime('%Y-%m-%d %H:%M:%S')
    mode_label = '指数专属' if is_index else '个股通用'

    def _safe_pct(v):
        return f'{v * 100:.2f}%' if v is not None else 'N/A'

    def _safe_float(v, fmt='.2f'):
        return f'{v:{fmt}}' if v is not None else 'N/A'

    def _img_to_b64(path):
        if not path or not os.path.exists(path):
            return ''
        with open(path, 'rb') as f:
            return base64.b64encode(f.read()).decode()

    # 最新信号表：每行一只股票，每列一个策略，单元格为该股票该策略的最新信号
    strategy_keys, strategy_names, signal_table_rows, excluded = _build_signal_table(all_stock_data, include_all=True)
    excluded = 0  # include_all 模式下已展示全部股票，不再显示隐藏备注

    def _signal_cell(text):
        if text == '买':
            return '<td style="color:#e74c3c;font-weight:bold">买</td>'
        if text == '弱买':
            return '<td style="color:#e74c3c">弱买</td>'
        if text == '卖':
            return '<td style="color:#27ae60;font-weight:bold">卖</td>'
        if text == '弱卖':
            return '<td style="color:#27ae60">弱卖</td>'
        if text == 'N/A':
            return '<td style="color:#000000">N/A</td>'
        if text.startswith('观(') or ('≥' in text or '≤' in text):
            return f'<td style="color:#f39c12;font-weight:bold">{text}</td>'
        return '<td style="color:#000000">-</td>'

    signal_rows = ''
    for row in signal_table_rows:
        label = row[0]
        price = row[1]
        signal_rows += f'<tr><td>{label}</td><td>{price}</td>'
        for cell in row[2:]:
            signal_rows += _signal_cell(cell)
        signal_rows += '</tr>'

    signal_table_html = '<table><tr><th>股票代码名称</th><th>最新价格</th>'
    for sk in strategy_keys:
        signal_table_html += f'<th>{strategy_names.get(sk, sk)}</th>'
    signal_table_html += '</tr>' + signal_rows + '</table>'
    signal_remark_html = f'<p style="color:#f39c12;font-size:12px;">备注: 未列示股票 {excluded} 只，其全部策略信号均为"观望"。</p>' if excluded else ''

    # 各股票详情
    detail_sections = ''
    for d in all_stock_data:
        s = d['symbol']
        sn = d['stock_name']
        lc = d.get('latest_close')
        ldt = d.get('latest_date')
        price_str = f'{lc:.2f}' if lc is not None else ''
        date_str = f'({ldt.date()})' if ldt is not None else ''
        label = f'{s} {sn}'.strip()
        smap = d['strategy_map']
        risks = d.get('all_risks', {})
        sigs = d.get('all_signals', {})
        charts = d.get('all_charts', {})

        detail_sections += f'<h2>{label} <small style="color:#666;font-weight:normal">{price_str} {date_str}</small></h2>'

        # 排名表
        rankings = []
        fcs = d.get('forecast_thresholds', {})
        for sk in d['strategies_to_run']:
            r = risks.get(sk, {})
            th = fcs.get(sk)
            fc_str = ''
            if th:
                parts = []
                if th.get('buy') is not None:
                    parts.append(f'买≥{th["buy"]:.0f}%')
                if th.get('sell') is not None:
                    parts.append(f'卖≤{th["sell"]:.0f}%')
                if parts:
                    fc_str = ' / '.join(parts)
            rankings.append((
                smap[sk], r.get('total_return'), r.get('sharpe_ratio'),
                r.get('max_drawdown'), r.get('buy_count'), r.get('sell_count'),
                _get_signal_text(sigs.get(sk)),
                fc_str,
            ))
        rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

        rank_rows = ''
        for i, (name, tr, sh, dd, bc, sc, sg, fc) in enumerate(rankings, 1):
            rank_rows += f'''<tr>
                <td>{i}</td><td>{name}</td>
                <td>{_safe_pct(tr)}</td><td>{_safe_float(sh)}</td>
                <td>{_safe_pct(dd)}</td><td>{f'{bc or 0:.1f}/{sc or 0:.1f}'}</td>
                <td style="font-weight:bold;color:{'#27ae60' if sg == '买入' else '#e74c3c' if sg == '卖出' else '#f39c12'}">{sg}</td>
                <td>{fc}</td>
            </tr>'''
        detail_sections += f'''<table>
            <tr><th>排名</th><th>策略</th><th>总收益率</th><th>夏普比率</th><th>最大回撤</th><th>买入/卖出次数</th><th>最新信号</th><th>次日触发价位</th></tr>
            {rank_rows}
        </table>'''

        # 策略权益曲线对比图
        # 嵌入各策略图表（合并后的多策略图表每股票只展示一次）
        embedded_chart_paths = set()
        for sk in d['strategies_to_run']:
            ch = charts.get(sk, {})
            for cname, cpath in ch.items():
                if cpath in embedded_chart_paths:
                    continue
                embedded_chart_paths.add(cpath)
                b64 = _img_to_b64(cpath)
                if b64:
                    ext = os.path.splitext(cpath)[1].lstrip('.')
                    detail_sections += f'<div class="chart"><img src="data:image/{ext};base64,{b64}" alt="{cname}"></div>'

    # 计算各策略收益率排名
    from collections import defaultdict
    strategy_all_returns = defaultdict(list)
    strategy_all_drawdowns = defaultdict(list)
    strategy_all_buys = defaultdict(list)
    strategy_all_sells = defaultdict(list)
    for d in all_stock_data:
        smap = d['strategy_map']
        risks = d.get('all_risks', {})
        for sk in d.get('strategies_to_run', []):
            r = risks.get(sk, {})
            name = smap[sk]
            tr = r.get('total_return')
            dd = r.get('max_drawdown')
            bc = r.get('buy_count')
            sc = r.get('sell_count')
            if tr is not None:
                strategy_all_returns[name].append(tr)
            if dd is not None:
                strategy_all_drawdowns[name].append(dd)
            if bc is not None:
                strategy_all_buys[name].append(bc)
            if sc is not None:
                strategy_all_sells[name].append(sc)

    strategy_avg = []
    for name, rets in strategy_all_returns.items():
        avg = sum(rets) / len(rets)
        wr = sum(1 for r in rets if r > 0) / len(rets) * 100
        dds = strategy_all_drawdowns.get(name, [])
        avg_dd = sum(dds) / len(dds) if dds else None
        buys = strategy_all_buys.get(name, [])
        sells = strategy_all_sells.get(name, [])
        avg_buy = sum(buys) / len(buys) if buys else None
        avg_sell = sum(sells) / len(sells) if sells else None
        strategy_avg.append((name, avg, wr, max(rets), min(rets), avg_dd, avg_buy, avg_sell))
    strategy_avg.sort(key=lambda x: x[1], reverse=True)

    strategy_rank_html = '<table><tr><th>排名</th><th>策略</th><th>平均收益率</th><th>正收益占比</th><th>最高</th><th>最低</th><th>最大回撤</th><th>平均买入/卖出次数</th></tr>'
    for i, (name, avg, wr, mx, mn, avg_dd, avg_buy, avg_sell) in enumerate(strategy_avg, 1):
        if avg_buy is not None and avg_sell is not None:
            trades_str = f'{avg_buy:.1f}/{avg_sell:.1f}'
        else:
            trades_str = 'N/A'
        strategy_rank_html += f'<tr><td>{i}</td><td>{name}</td><td>{_safe_pct(avg)}</td><td>{wr:.0f}%</td><td>{_safe_pct(mx)}</td><td>{_safe_pct(mn)}</td><td>{_safe_pct(avg_dd)}</td><td>{trades_str}</td></tr>'
    strategy_rank_html += '</table>'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>量化分析报告 - 批量汇总</title>
<style>
body {{ font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #fafafa; color: #2c3e50; }}
h1 {{ border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
h2 {{ border-bottom: 2px solid #bdc3c7; padding-bottom: 6px; margin-top: 30px; }}
h3 {{ color: #2980b9; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: center; }}
th {{ background: #3498db; color: white; }}
tr:nth-child(even) {{ background: #f2f2f2; }}
.info {{ background: #ecf0f1; padding: 15px; border-radius: 5px; margin: 15px 0; }}
.info span {{ margin-right: 30px; }}
.chart {{ margin: 20px 0; text-align: center; }}
.chart img {{ max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; }}
.footer {{ text-align: center; color: #95a5a6; margin-top: 40px; font-size: 12px; }}
</style>
</head>
<body>
<h1>股票量化分析报告（批量）</h1>
<div class="info">
    <span><strong>股票数:</strong> {len(all_stock_data)}</span>
    <span><strong>初始资金:</strong> {capital:,.0f}</span>
    <span><strong>模式:</strong> {mode_label}</span>
    <span><strong>分析周期:</strong> {all_stock_data[0]['start_date'] if all_stock_data else 'N/A'} ~ {all_stock_data[0]['end_date'] if all_stock_data else 'N/A'}</span>
    <span><strong>生成时间:</strong> {report_time}</span>
</div>
<div class="info">
    <span><strong>命令参数:</strong> {cmd_params}</span>
</div>

<h2>最新信号表</h2>
{signal_table_html}
{signal_remark_html}

<h2>各策略收益率排名</h2>
{strategy_rank_html}
</table>

{detail_sections}

<div class="footer"><p>报告由 stock-quant 自动生成 | {report_time}</p></div>
</body>
</html>'''

    # 平均收益率（各股票最佳策略收益率的均值，用于文件名）
    best_returns = []
    for d in all_stock_data:
        risks = d.get('all_risks', {})
        best_ret = None
        for sk in d.get('strategies_to_run', []):
            r = risks.get(sk, {}).get('total_return')
            if r is not None and (best_ret is None or r > best_ret):
                best_ret = r
        if best_ret is not None:
            best_returns.append(best_ret)
    avg_return = (sum(best_returns) / len(best_returns)) if best_returns else None

    if report_filename:
        report_path = report_filename
    else:
        days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
        report_path = _build_report_filename(source, end_date, days, strategy, mode,
                                             profit=_profit_int(avg_return))
    if not os.path.dirname(report_path):
        report_path = os.path.join('report', report_path)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


# ============================================================================
# CLI 命令组
# ============================================================================


class _StrategyEnhanceGroup(click.Group):
    """自定义命令组：重写 -g[N]/--strategy[N] 参数，将增强级别 N 附加到各策略后

    例如：
        -g  macd, rsi   ->  -g macd, rsi      （基础判定）
        -g1 macd, rsi   ->  -g macd1, rsi1    （增强级别 1）
        -g2 macd        ->  -g macd2          （增强级别 2）
    """

    def parse_args(self, ctx, args):
        new_args = []
        i = 0
        n = len(args)
        while i < n:
            arg = args[i]
            # 匹配 -gN 或 --strategyN（N 为可选数字）
            m = re.match(r'^(-g|--strategy)(\d*)$', arg)
            if m:
                flag, num = m.group(1), m.group(2)
                # 从下一个参数取策略列表（-g 需要其值）
                if i + 1 < n:
                    strat_list = args[i + 1]
                    if num:
                        if strat_list.strip() == 'all':
                            # all 展开为全部策略并附加增强级别 N
                            new_list = ','.join(k + num for k in ALL_STRATEGIES)
                        else:
                            # 将增强级别 N 追加到每个策略 key 后
                            new_list = ','.join(
                                s.strip() + num if s.strip() and s.strip() != 'all' else s.strip()
                                for s in re.split(r'[,|]', strat_list) if s.strip()
                            )
                        new_args.append('-g')
                        new_args.append(new_list)
                    else:
                        new_args.append('-g')
                        new_args.append(strat_list)
                    i += 2
                    continue
            new_args.append(arg)
            i += 1
        args = new_args
        return super().parse_args(ctx, args)


@click.group(cls=_StrategyEnhanceGroup)
@click.version_option(version='1.0.0', prog_name='stock_quant')
def cli():
    """股票量化分析软件 - 命令行工具

    提供股票技术分析、策略回测、策略对比、股票扫描等功能。
    """
    pass


def _resolve_symbol(input_str):
    """将股票代码或名称解析为 (纯数字代码, 股票名称)

    Args:
        input_str: 股票代码或名称（如 '000725'、'京东方A'）

    Returns:
        (code, name) 或 (None, None)
    """
    import re as _re

    input_str = input_str.strip()
    for prefix in ('sh', 'sz', 'SH', 'SZ'):
        if input_str.startswith(prefix):
            input_str = input_str[len(prefix):]
            break

    # 优先从数据库读取已缓存的股票名称
    if input_str.isdigit():
        try:
            from core.db import fetch_stock_info
            info = fetch_stock_info(input_str)
            if info and info.get('name'):
                return input_str, info['name']
        except Exception:
            pass

    # 通过 Sina 搜索 API 解析
    try:
        url = f'https://suggest3.sinajs.cn/suggest/type=11&key={input_str}'
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}
        resp = requests.get(url, timeout=10, headers=headers)
        text = resp.text
        match = _re.search(r'"([^"]*)"', text)
        if match:
            items = match.group(1).split(';')
            for item in items:
                fields = item.split(',')
                if len(fields) >= 4 and fields[2].isdigit():
                    code = fields[2]
                    name = fields[4] if len(fields) > 4 and fields[4] else fields[0]
                    return code, name
    except Exception:
        pass

    # 如果是纯数字，尝试通过 Sina 实时行情获取名称（含 ETF/LOF/基金）
    if input_str.isdigit():
        try:
            symbol = input_str
            if symbol.startswith('6') or symbol.startswith('9'):
                sina_code = f'sh{symbol}'
            elif symbol.startswith(('0', '3', '2')):
                sina_code = f'sz{symbol}'
            elif symbol.startswith('15'):
                sina_code = f'sz{symbol}'
            else:
                sina_code = f'sh{symbol}'

            rt_url = f'https://hq.sinajs.cn/list={sina_code}'
            rt_headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}
            rt_resp = requests.get(rt_url, timeout=10, headers=rt_headers)
            rt_text = rt_resp.text
            rt_match = _re.search(r'"([^"]*)"', rt_text)
            if rt_match:
                rt_parts = rt_match.group(1).split(',')
                if len(rt_parts) >= 1 and rt_parts[0]:
                    return symbol, rt_parts[0]
        except Exception:
            pass
        return input_str, ''

    # 备选：akshare 全量股票列表
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        matches = df[df['名称'].str.contains(input_str, na=False)]
        if matches.empty:
            return None, None
        exact = matches[matches['名称'] == input_str]
        if not exact.empty:
            code = str(exact.iloc[0]['代码'])
            name = str(exact.iloc[0]['名称'])
            return code, name
        code = str(matches.iloc[0]['代码'])
        name = str(matches.iloc[0]['名称'])
        return code, name
    except Exception:
        return None, None


# ============================================================================
# analyze 命令 - 单只股票综合分析
# ============================================================================


def _split_strategies(s):
    """按逗号或竖线分隔策略字符串，返回去空白后的非空列表（兼容 | 与 , 两种写法）"""
    if not s:
        return []
    return [x.strip() for x in re.split(r'[,|]', s) if x.strip()]


def _parse_strategy_spec(spec):
    """解析 -g 策略说明：支持 <STRATEGY>[N] 形式（N 为信号增强判定级别）

    - 'macd'  -> ('macd', 0)   基本判定
    - 'macd1' -> ('macd', 1)   在 macd 基础上增加增强判定条件 1

    Returns:
        (base_key, enhance): base_key 为策略基础 key，enhance 为增强级别（0=不增强）
    """
    spec = spec.strip()
    m = re.match(r'^(.*?)(\d+)$', spec)
    if m:
        base, num = m.group(1), int(m.group(2))
        return base, num
    return spec, 0


def _build_strategy_instance(base_key, strategy_map, enhance=0):
    """构建策略实例，并注入增强级别 enhance（与 _run_strategy 的实例化逻辑一致）"""
    strategy_name, strategy_class = strategy_map[base_key]
    strategy = strategy_class()
    strategy.enhance = enhance
    return strategy


def _read_symbols_file(path):
    """从文件读取股票代码列表（空格/逗号/换行分隔）"""
    if not os.path.exists(path):
        click.echo(click.style(f'  错误: 文件不存在 ({path})', fg='red'))
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        return [s for s in re.split(r'[,\s]+', text) if s]
    except Exception as e:
        click.echo(click.style(f'  错误: 无法读取 {path}: {e}', fg='red'))
        return []


def _apply_realtime_to_df(df, symbol, fetcher, end_date, verbose=True):
    """将当日实时行情合并进 df（盘中处理）：

    1) 历史K线 df 已获取；
    2) -e 缺省或为今日才继续，否则返回 df 最后一根收盘价；
    3) -e 为今日且 df 最后一条日期已是今日，跳过（K线已含今日）；
    4) 最后一条非今日时获取实时行情，price<=0 或 datetime 非今日则跳过；
    5) price>0 且 datetime==今日，把实时行情追加到 df（close 取实时价）。

    Args:
        df: 历史K线 DataFrame（date 索引）
        symbol: 股票代码
        fetcher: DataFetcher 实例
        end_date: 请求结束日期（YYYY-MM-DD）
        verbose: 是否打印价格提示（批量/并行场景可关闭）

    Returns:
        (df, latest_close, latest_date, is_realtime): 合并后的 df、最新价、
        最新日期（datetime）、是否为实时价。
    """
    latest_close = df['close'].iloc[-1]
    latest_date = df.index[-1]
    is_realtime = False
    today = datetime.now()
    today_str = today.strftime('%Y-%m-%d')

    if end_date == today_str:
        last_row_date = df.index[-1]
        if last_row_date.date() != today.date():
            # 步骤4：获取实时行情
            try:
                rt = fetcher.get_realtime_quote(symbol)
            except Exception:
                rt = None
            if rt:
                rt_price = rt.get('price', 0)
                rt_ts = rt.get('datetime') or ''
                try:
                    rt_date = pd.to_datetime(rt_ts).date()
                except Exception:
                    rt_date = None
                if rt_price > 0 and rt_date == today.date():
                    # 步骤5：追加实时行情到 df
                    latest_close = rt_price
                    latest_date = today
                    is_realtime = True
                    today_idx = pd.Timestamp(today.date())
                    rt_row = pd.DataFrame({
                        'open':  [rt.get('open', rt_price)],
                        'high':  [rt.get('high', rt_price)],
                        'low':   [rt.get('low', rt_price)],
                        'close': [rt_price],
                        'volume': [rt.get('volume', 0)],
                    }, index=[today_idx])
                    for col in rt_row.columns:
                        rt_row[col] = rt_row[col].astype(df[col].dtype)
                    df = pd.concat([df, rt_row])
                    df = df.sort_index()

    if verbose:
        if is_realtime:
            click.echo(click.style(f'  实时价格: {latest_close:.2f} (已纳入分析)', fg='yellow'))
        else:
            click.echo(click.style(f'  最新价格: {latest_close:.2f} ({latest_date.date()})', fg='green'))

    return df, latest_close, latest_date, is_realtime


def _build_forecast_strategy(strategy_key, strategy_map, enhance=0):
    """构建用于预测的信号策略实例（与 _run_strategy 一致）"""
    return _build_strategy_instance(strategy_key, strategy_map, enhance)


def _signal_value_at_forecast(df, strategy, next_date, price):
    """将次日K线（open=high=low=close=price）追加到 df，返回策略最新信号值"""
    import copy as _copy
    df2 = _copy.deepcopy(df)
    rt_row = pd.DataFrame({
        'open': [price], 'high': [price], 'low': [price], 'close': [price],
        'volume': [df['volume'].iloc[-1] if 'volume' in df.columns else 0],
    }, index=[pd.Timestamp(next_date)])
    for col in rt_row.columns:
        rt_row[col] = rt_row[col].astype(df[col].dtype)
    df2 = pd.concat([df2, rt_row])
    df2 = df2.sort_index()
    df2 = add_all_indicators(df2)
    sig = strategy.generate_signals(df2)
    if sig is None or len(sig) == 0:
        return None
    return float(sig.iloc[-1]) if hasattr(sig, 'iloc') else float(sig[-1])


def _compute_forecast_thresholds(df, strategy_key, strategy_map, end_date, k=0.1, depth=5, enhance=0):
    """当日信号为观望时，计算次日触发买入/卖出信号的收盘价阈值（方案A：二分法）

    1) 在 df 末尾追加次日K线（open=high=low=close=c）；
    2) 候选区间 [today_close*(1-k), today_close*(1+k)]，k 取 0.1；
    3) 用二分法（递归 depth=5 次）找：
         - 买入阈值：最小的 c 使次日信号 > 0
         - 卖出阈值：最大的 c 使次日信号 < 0
    4) open/high/low 均取 c。

    Returns:
        dict: {'buy': float 或 None, 'sell': float 或 None}，None 表示区间内未触发。
    """
    try:
        strategy = _build_forecast_strategy(strategy_key, strategy_map, enhance)
        today_close = df['close'].iloc[-1]
        last_date = df.index[-1]

        # 次日K线日期：df 已有当天(最后一条日期==结束日)则追加到 end+1 日，否则追加到 end 日
        if last_date.strftime('%Y-%m-%d') == end_date:
            next_date = (pd.Timestamp(end_date) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            next_date = end_date

        lo = today_close * (1 - k)
        hi = today_close * (1 + k)

        def _signal_at(c):
            return _signal_value_at_forecast(df, strategy, next_date, c)

        # 二分查找买入阈值（递归 depth 次）：找使信号>0 的最小 c
        def _find_buy(l, r, depth):
            if depth <= 0:
                return None
            mid = (l + r) / 2
            sv = _signal_at(mid)
            if sv is not None and sv > 0:
                # 尝试更小值
                smaller = _find_buy(l, mid, depth - 1)
                return smaller if smaller is not None else mid
            else:
                return _find_buy(mid, r, depth - 1)

        # 二分查找卖出阈值（递归 depth 次）：找使信号<0 的最大 c
        def _find_sell(l, r, depth):
            if depth <= 0:
                return None
            mid = (l + r) / 2
            sv = _signal_at(mid)
            if sv is not None and sv < 0:
                larger = _find_sell(mid, r, depth - 1)
                return larger if larger is not None else mid
            else:
                return _find_sell(l, mid, depth - 1)

        buy = _find_buy(lo, hi, depth)
        sell = _find_sell(lo, hi, depth)

        # 将触发价格转换为相对今日收盘的涨跌百分比
        # buy: 需要涨到的百分比（buy_pct > 0 表示较今日上涨 x% 触发买入）
        # sell: 需要跌到的百分比（sell_pct < 0 表示较今日下跌 |x|% 触发卖出）
        buy_pct = ((buy / today_close) - 1) * 100 if buy is not None else None
        sell_pct = ((sell / today_close) - 1) * 100 if sell is not None else None
        return {'buy': buy_pct, 'sell': sell_pct}
    except Exception:
        return {'buy': None, 'sell': None}


@cli.command('analyze')
@click.option('--symbol', '-s', default=None, help='股票代码或名称，可多个，以空格分隔（如 "000725 京东方A 000021"）')
@click.option('--symbol-file', '-sf', default=None, help='从指定文件读取自选股列表（股票代码，多个以空格/逗号/换行分隔），与 -s 同时使用时合并')
@click.option('--hot', '-h', default=None, type=int, help='获取热门股票数量（按HotScore热度分排序，存在时忽略 -s 和 -sf）')
@click.option('--start', '-st', default=None, help='开始日期（默认365天前），格式: YYYYMMDD')
@click.option('--end', '-e', default=None, help='结束日期（默认今天），格式: YYYYMMDD')
@click.option('--strategy', '-g', default='macd', help='策略选择 [ma_cross|ema_cross|macd|rsi|bollinger|kdj|quality_value|all]，多个用逗号分隔（默认macd）；-gN 表示全部启用增强级别N（如 -g1 macd,rsi 即 macd1,rsi1）')
@click.option('--index', '-i', 'is_index', is_flag=True, default=False, help='使用指数专属策略模式（动量分层/波动率择时/涨跌比确认）')
@click.option('--capital', '-c', default=100, type=float, help='初始资金（单位：万元，默认100万元）')
@click.option('--output', '-o', default='./output', help='图表输出目录（默认./output）')
@click.option('--output-file', '-of', default=None, help='指定HTML报告文件名，默认自动生成')
@click.option('--threads', '-x', default=5, type=click.IntRange(1, 6), help='并行进程数 -xN（N=1~6，默认5）')
@click.option('--db', '-db', default=0, type=int, help='数据库缓存模式 [0=不读不写(默认)|1=只读缓存不写|2=不读缓存走网络覆盖写]')
@click.option('--mode', '-m', default=0, type=int, help='分析模式 [0=常规(默认)|1=资金利用最大化轮动选股|2=多持仓资金利用最大化|3=多持仓强化(买卖信号与选股优化)|4=多持仓强化(趋势择股卖出)]')
@click.option('--filter', '-f', 'filter_signal', default=None, type=click.Choice(['b', 's', 'w']), help='信号过滤，仅输出符合指定信号的股票报告 [b=买入|s=卖出|w=观望]')
@click.option('--chart', is_flag=True, default=False, help='生成所有股票的K线图等图形并嵌入报告（默认仅生成至少有一个策略信号非观望的股票的图表）')
@click.option('--forecast', is_flag=True, default=False, help='预测次日触发买卖信号的收盘价阈值（不指定则不预测）')
def analyze_cmd(symbol, symbol_file, hot, start, end, strategy, is_index, capital, output, output_file, threads, db, mode, filter_signal, chart, forecast):
    """量化分析：重点输出结束日当天的买卖信号

    流程：获取数据 -> 计算指标 -> 运行策略 -> 回测 -> 风险分析 -> 生成图表 -> 打印报告

    使用 --index/-i 参数可切换到指数专属策略模式，适用于分析大盘指数。
    -s 支持多个股票代码或名称，以空格分隔；多个股票时输出报告名称为 multi_日期.html。
    -s 和 -sf 可同时使用，股票列表会自动合并去重。
    都不指定时从 stock-quant.json 读取自选股。
    使用 -h 指定数量时，忽略 -s/-sf，从网络获取最热门股票。
    缺少 -st 时默认分析周期 365 天，缺少 -e 时默认今天。
    """
    capital = capital * 10000  # -c 单位为万元，内部换算为元
    # 设置数据库缓存模式
    from core.db import set_db_mode
    set_db_mode(db)
    if db == 0:
        click.echo(click.style('  数据库缓存: 不读不写（纯网络）', fg='yellow'))
    elif db == 1:
        click.echo(click.style('  数据库缓存: 只读缓存，不写数据库', fg='yellow'))
    elif db == 2:
        click.echo(click.style('  数据库缓存: 不读缓存，走网络获取并覆盖写库', fg='yellow'))

    # 命令选项参数摘要（用于在报告中标注）
    cmd_params = _format_cmd_params(symbol, symbol_file, hot, start, end, strategy,
                                    is_index, capital, output, output_file, threads, db, mode,
                                    filter_signal, chart, forecast)

    # 确定要分析的股票列表
    time_start = datetime.now()

    symbols = []
    sources = []
    s_multi = False

    if hot:
        # 热门股票模式：忽略 -s 和 -sf
        click.echo(click.style(f'\n  正在获取热门股票列表 ({hot} 只)...', fg='blue'))
        fetcher = DataFetcher()
        hot_df = fetcher.get_hot_stocks(count=hot)
        if hot_df.empty:
            click.echo(click.style('  错误: 获取热门股票失败', fg='red'))
            return
        for _, row in hot_df.iterrows():
            symbols.append(row['symbol'])
        sources.append(f'-h 热门股 ({len(symbols)}只, 按热度分排序)')
    else:
        if symbol:
            symbol_list = [s.strip() for s in symbol.split() if s.strip()]
            has_wildcard = any(any(ch in s for ch in '*?') for s in symbol_list)
            if has_wildcard:
                # 展开通配符（如 60* 表示所有 60 开头的股票）
                click.echo(click.style('  检测到通配符，正在获取全量股票列表...', fg='blue'))
                fetcher = DataFetcher()
                all_codes = fetcher.get_all_stock_codes()
                expanded = []
                for s in symbol_list:
                    if any(ch in s for ch in '*?'):
                        matched = [c for c in all_codes if fnmatch.fnmatch(c, s)]
                        if not matched:
                            click.echo(click.style(f'  警告: 通配符 "{s}" 未匹配到任何股票', fg='yellow'))
                        expanded.extend(matched)
                    else:
                        expanded.append(s)
                # 最多查询 500 只
                if len(expanded) > 500:
                    click.echo(click.style(f'  通配符匹配 {len(expanded)} 只，超过 500 只上限，仅取前 500 只', fg='yellow'))
                    expanded = expanded[:500]
                symbol_list = expanded
                symbols = symbol_list
                s_multi = True
                sources.append(f'-s ({len(symbol_list)}只, 含通配符)')
                click.echo(click.style('  查询到的股票: ' + '，'.join(symbol_list), fg='cyan'))
            else:
                symbols = symbol_list
                s_multi = len(symbol_list) > 1
                sources.append(f'-s ({len(symbol_list)}只)')

        if symbol_file:
            file_symbols = _read_symbols_file(symbol_file)
            if file_symbols:
                symbols.extend(file_symbols)
                sources.append(f'-sf ({len(file_symbols)}只)')

        if not symbols:
            config = _load_quant_config()
            if 'favorites' in config and config['favorites']:
                symbols = config['favorites']
                sources.append('stock-quant.json')
            else:
                favs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'input', 'favs.json')
                file_symbols = _read_symbols_file(favs_path)
                if not file_symbols:
                    click.echo(click.style('  错误: 没有可用的自选股来源', fg='red'))
                    return
                symbols = file_symbols
                sources.append('favs.json')

    # 去重并保持顺序
    seen = set()
    deduped = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    symbols = deduped

    click.echo(click.style(f'\n  股票来源: {", ".join(sources)} → 共 {len(symbols)} 只（已去重）', fg='blue'))

    # 确定股票源标签
    if hot:
        source_tag = f'hot{hot}'
    elif symbol_file:
        source_tag = os.path.splitext(os.path.basename(symbol_file))[0]
    elif len(symbols) > 1:
        source_tag = 'multi'
    elif len(symbols) == 1:
        source_tag = symbols[0]
    else:
        source_tag = 'favs'

    # 解析日期：缺少 -st 默认365天前，缺少 -e 默认今天
    start_date_str, end_date_str = _parse_dates(start, end)

    # 模式 1：资金利用最大化轮动选股；模式 2：多持仓资金利用最大化；模式 3/4：多持仓强化
    if mode == 1:
        _run_rotation_analysis(symbols, start_date_str, end_date_str, strategy, capital, output, output_file, cmd_params,
                               source=source_tag, mode=mode, chart=chart)
        return
    if mode == 2:
        _run_portfolio_analysis(symbols, start_date_str, end_date_str, strategy, capital, output, output_file, cmd_params=cmd_params,
                                source=source_tag, mode=mode, chart=chart)
        return
    if mode == 3:
        _run_portfolio_analysis_v3(symbols, start_date_str, end_date_str, strategy, capital, output, output_file, cmd_params,
                                   source=source_tag, mode=mode, chart=chart)
        return
    if mode == 4:
        _run_portfolio_analysis_v4(symbols, start_date_str, end_date_str, strategy, capital, output, output_file, cmd_params,
                                   source=source_tag, mode=mode, chart=chart)
        return

    all_stock_data = []
    if len(symbols) > 1 and threads > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        from concurrent.futures import TimeoutError as _FutTimeout
        import math
        n_workers = min(threads, len(symbols))
        click.echo(click.style(f'\n  使用 {n_workers} 个进程并行分析...', fg='blue'))

        tasks = [(s, start_date_str, end_date_str, strategy, is_index, capital, output, output_file, True, cmd_params, mode, filter_signal, chart, forecast) for s in symbols]
        results = [None] * len(symbols)
        # 单只股票超时（秒），避免个别股票因网络/数据库异常导致整体挂起
        per_stock_timeout = 180
        waves = max(1, math.ceil(len(symbols) / n_workers))
        executor = ProcessPoolExecutor(max_workers=n_workers)
        try:
            futures = {executor.submit(_analyze_single_worker, task): i for i, task in enumerate(tasks)}
            try:
                for fut in as_completed(futures, timeout=per_stock_timeout * waves):
                    idx = futures[fut]
                    try:
                        _, data, out = fut.result()
                    except Exception as e:
                        data, out = None, f'  异常: {e}'
                    results[idx] = data
                    if data is None:
                        if '信号过滤' in out:
                            click.echo(click.style(f'  [{idx+1}/{len(symbols)}] {symbols[idx]} 已跳过（信号过滤）', fg='yellow'))
                            click.echo(out)
                        else:
                            click.echo(click.style(f'\n  [{idx+1}/{len(symbols)}] {symbols[idx]} 分析失败', fg='red'))
                            if out.strip():
                                click.echo(out)
                    else:
                        click.echo(click.style(f'  [{idx+1}/{len(symbols)}] {symbols[idx]} 完成', fg='cyan'))
            except _FutTimeout:
                stuck = [symbols[futures[f]] for f in futures if not f.done()]
                click.echo(click.style(
                    f'\n  警告: {len(stuck)} 只股票分析超时({per_stock_timeout}秒)被跳过: {", ".join(stuck)}', fg='yellow'))
                for f in futures:
                    if not f.done():
                        f.cancel()
                # 强制终止卡住的工作进程，避免 shutdown 挂起
                for proc in list(executor._processes.values()):
                    proc.terminate()
        finally:
            executor.shutdown(wait=True)
        all_stock_data = [d for d in results if d is not None]
    else:
        for idx, raw_symbol in enumerate(symbols):
            if len(symbols) > 1:
                click.echo(click.style(f'\n  ── [{idx+1}/{len(symbols)}] ──', fg='cyan', bold=True))
            data = _analyze_single(raw_symbol, start_date_str, end_date_str, strategy, is_index, capital, output, output_file,
                                   batch_mode=len(symbols) > 1, cmd_params=cmd_params, mode=mode,
                                   filter_signal=filter_signal, chart=chart, forecast=forecast)
            if data is not None:
                all_stock_data.append(data)

    if len(symbols) > 1:
        click.echo(click.style(f'\n  批量分析完成，共 {len(symbols)} 只股票', fg='green', bold=True))

    if filter_signal:
        click.echo(click.style(
            f'  信号过滤: 共 {len(symbols)} 只股票，符合 "{_FILTER_SIGNAL_TEXT[filter_signal]}" 条件的 {len(all_stock_data)} 只', fg='cyan'))

    # 打印最新信号表到屏幕
    if all_stock_data:
        _print_signal_table(all_stock_data)

    # 生成汇总 HTML 报告
    if all_stock_data:
        try:
            html_path = _generate_multi_html_report(all_stock_data, strategy, is_index, capital,
                                                    report_filename=output_file, end_date=end_date_str,
                                                    start_date=start_date_str, cmd_params=cmd_params,
                                                    source=source_tag, mode=mode)
            click.echo(click.style(f'\n  汇总报告: {html_path}', fg='green'))
        except Exception as e:
            click.echo(click.style(f'\n  汇总报告生成失败: {e}', fg='yellow'))

    elapsed = (datetime.now() - time_start).total_seconds()
    click.echo(click.style(f'\n  总运行时间: {elapsed:.1f} 秒', fg='cyan', bold=True))


def _analyze_single(raw_symbol, start, end, strategy, is_index, capital, output, output_file=None, batch_mode=False, cmd_params='', mode=0, filter_signal=None, chart=False, forecast=0):
    """分析单只股票"""
    symbol = raw_symbol.strip()
    for prefix in ('sh', 'sz', 'SH', 'SZ'):
        if symbol.startswith(prefix):
            symbol = symbol[len(prefix):]
            break

    try:
        # 股票代码/名称解析
        stock_name = ''
        if not symbol.isdigit():
            click.echo(click.style(f'\n  正在搜索股票: {raw_symbol}...', fg='blue'))
            code, name = _resolve_symbol(raw_symbol)
            if code is None:
                click.echo(click.style(f'  错误: 未找到匹配 "{raw_symbol}" 的股票', fg='red'))
                return
            symbol = code
            stock_name = name
            click.echo(click.style(f'  找到: {symbol} {stock_name}', fg='green'))
        else:
            click.echo(click.style(f'\n  正在查找股票名称: {symbol}...', fg='blue'))
            _, name = _resolve_symbol(symbol)
            if name:
                stock_name = name
                click.echo(click.style(f'  找到: {symbol} {stock_name}', fg='green'))

        # 缓存股票名称到数据库
        if stock_name:
            try:
                from core.db import store_stock_info
                is_etf_flag = _is_etf(symbol)
                market_code = 'sh' if symbol.startswith(('6', '9', '5', '51', '58', '588')) else 'sz'
                store_stock_info(symbol, stock_name, market=market_code, is_etf=1 if is_etf_flag else 0)
            except Exception:
                pass

        # 解析日期
        start_date, end_date = _parse_dates(start, end)

        # 打印头部
        header_display = f'{symbol} {stock_name}'.strip()
        _print_header('股票量化分析报告' if not is_index else '指数量化分析报告', header_display)

        click.echo(click.style(f'  分析区间: {start_date} ~ {end_date}', fg='white'))
        click.echo(click.style(f'  初始资金: {capital:,.0f}', fg='white'))

        # 获取数据
        click.echo(click.style('\n  正在获取股票数据...', fg='blue'))
        fetcher = DataFetcher()
        df = fetcher.get_stock_data(symbol, start_date, end_date)

        if df is None or len(df) == 0:
            click.echo(click.style(f'  错误: 未能获取股票 {symbol} 的数据', fg='red'))
            return

        click.echo(click.style(f'  获取到 {len(df)} 条数据记录', fg='green'))

        # 异步缓存除权除息数据到数据库（不阻塞主流程）
        # 使用独立的 DataFetcher，避免与主流程共享 requests.Session（非线程安全）
        def _cache_dividend():
            try:
                DataFetcher().get_dividend_data(symbol)
            except Exception:
                pass
        try:
            import threading
            t = threading.Thread(target=_cache_dividend, daemon=True)
            t.start()
        except Exception:
            pass

        # 最新价格与盘中实时数据处理（合并当日实时行情，见 _apply_realtime_to_df）
        df, latest_close, latest_date, is_realtime = _apply_realtime_to_df(
            df, symbol, fetcher, end_date)

        # 判断 ETF/指数基金
        is_etf = not is_index and _is_etf(symbol)

        # 计算技术指标（DB 优先）
        click.echo(click.style('  正在获取技术指标...', fg='blue'))
        from_db = False
        try:
            from core.db import fetch_indicators
            db_rows = fetch_indicators(symbol, start_date, end_date)
            if db_rows and len(db_rows) >= len(df) * 0.9:
                indi_cols = [
                    'date', 'MA5', 'MA10', 'MA20', 'MA30', 'MA60',
                    'EMA10', 'EMA12', 'EMA26', 'EMA30', 'MACD_DIF', 'MACD_DEA', 'MACD_BAR',
                    'RSI14', 'BOLL_UPPER', 'BOLL_MIDDLE', 'BOLL_LOWER',
                    'KDJ_K', 'KDJ_D', 'KDJ_J', 'ATR14', 'OBV', 'CCI20', 'WR14',
                    'VOL_MA5', 'VWAP', 'HV20', 'MOM60'
                ]
                df_indi = pd.DataFrame(db_rows, columns=indi_cols)
                df_indi['date'] = pd.to_datetime(df_indi['date'])
                df_indi = df_indi.set_index('date')
                for c in df_indi.columns:
                    if c in df_indi.columns and not df_indi[c].isna().all():
                        df[c] = df_indi[c]
                from_db = True
                click.echo(click.style(f'  从数据库读取 {len(df_indi)} 条技术指标', fg='green'))
        except Exception:
            pass

        if not from_db:
            click.echo(click.style('  正在计算技术指标...', fg='blue'))
            df = add_all_indicators(df)
            click.echo(click.style(f'  已计算 {len(df.columns)} 项指标', fg='green'))
            try:
                from core.db import store_indicators
                store_indicators(symbol, df)
            except Exception:
                pass

        # 初始化图表生成器
        date_tag = _build_date_tag(start_date, end_date)
        chart_gen = ChartGenerator(output_dir=output, prefix=symbol, date_tag=date_tag)

        # 注入股票代码供策略使用
        df.attrs['symbol'] = symbol

        stock_label = f'{symbol}({stock_name})' if stock_name else symbol

        # 确定要运行的策略列表和模式标签
        if is_index:
            strategy_map = INDEX_STRATEGY_MAP
            all_strategies_list = ALL_INDEX_STRATEGIES
            mode_label = '指数专属'
        elif is_etf:
            strategy_map = ETF_STRATEGY_MAP
            all_strategies_list = ALL_ETF_STRATEGIES
            mode_label = 'ETF专用'
        else:
            strategy_map = STRATEGY_MAP
            all_strategies_list = ALL_STRATEGIES
            mode_label = '个股通用'

        click.echo(click.style(f'  策略模式: {mode_label}', fg='white'))

        if strategy == 'all':
            strategies_to_run = all_strategies_list
            strategy_enhance = {sk: 0 for sk in strategies_to_run}
        else:
            strategies_to_run = []
            strategy_enhance = {}
            for s in _split_strategies(strategy):
                base_key, enh = _parse_strategy_spec(s)
                if base_key not in strategy_map:
                    avail = ', '.join(strategy_map.keys())
                    click.echo(click.style(f'  错误: 未知策略 "{base_key}"，可选: {avail}, all', fg='red'))
                    return
                if base_key not in strategies_to_run:
                    strategies_to_run.append(base_key)
                strategy_enhance[base_key] = enh

        # 报告展示用策略列表：全部策略均展示（含质价融合策略）
        strategies_to_report = strategies_to_run

        # 运行策略
        all_results = {}
        all_risks = {}
        all_charts = {}
        all_signals = {}

        for sk in strategies_to_run:
            click.echo(click.style(f'  正在运行策略: {strategy_map[sk][0]}...', fg='blue'))
            result, chart_paths, sname, signals = _run_single_analysis(
                df, sk, capital, chart_gen, prefix=stock_label, strategy_map=strategy_map,
                enhance=strategy_enhance.get(sk, 0)
            )
            all_results[sk] = result
            all_risks[sk] = result
            all_charts[sk] = chart_paths
            all_signals[sk] = signals

        # 预测次日触发买卖信号的收盘价阈值（--forecast 启用时，对观望信号策略计算）
        forecast_thresholds = {}
        if forecast:
            for sk in strategies_to_report:
                sig = all_signals.get(sk)
                if sig is None or len(sig) == 0:
                    continue
                last_sig = float(sig.iloc[-1]) if hasattr(sig, 'iloc') else float(sig[-1])
                if last_sig != 0:
                    continue
                th = _compute_forecast_thresholds(df, sk, strategy_map, end_date, enhance=strategy_enhance.get(sk, 0))
                if th.get('buy') is not None or th.get('sell') is not None:
                    forecast_thresholds[sk] = th

        # 生成信号图表：--chart 输出所有股票的图表；否则仅输出至少有一个策略信号非观望的股票的图表
        if chart or _has_actionable_signal(all_signals):
            # 单个策略：生成独立图表；多个策略：合并为一个综合图表
            if len(strategies_to_run) == 1:
                sk = strategies_to_run[0]
                signals = all_signals.get(sk)
                if signals is not None and len(signals) > 0:
                    try:
                        sk_chart_gen = ChartGenerator(output_dir=chart_gen.output_dir, prefix=f'{symbol}_{sk}', date_tag=date_tag)
                        strategy_name = strategy_map[sk][0]
                        equity_curve = all_risks[sk].get('equity_curve') if all_risks.get(sk) else None
                        signal_path = sk_chart_gen.plot_signal_composite(
                            df, signals, strategy_key=sk,
                            title=f'{strategy_name}-回测效果 ({symbol} {stock_name})'.replace('  ', ' '),
                            equity_curve=equity_curve,
                        )
                        all_charts[sk]['signals'] = signal_path
                    except Exception:
                        all_charts[sk]['signals'] = ''
            else:
                # 多策略：合并为一个K线图（各策略指标子图 + 权益曲线汇总子图）
                strategies_data = []
                for sk in strategies_to_run:
                    signals = all_signals.get(sk)
                    if signals is None or len(signals) == 0:
                        continue
                    strategies_data.append({
                        'key': sk,
                        'name': strategy_map[sk][0],
                        'signals': signals,
                        'equity_curve': all_risks[sk].get('equity_curve') if all_risks.get(sk) else None,
                    })
                if strategies_data:
                    try:
                        multi_chart_gen = ChartGenerator(output_dir=chart_gen.output_dir, prefix=f'{symbol}_multi', date_tag=date_tag)
                        multi_path = multi_chart_gen.plot_multi_strategy_composite(
                            df, strategies_data,
                            title=f'多策略回测效果 ({symbol} {stock_name})'.replace('  ', ' '),
                        )
                        for sk in strategies_to_run:
                            all_charts[sk]['signals'] = multi_path
                    except Exception:
                        for sk in strategies_to_run:
                            all_charts[sk]['signals'] = ''

        # 输出每个策略的回测结果
        for sk in strategies_to_report:
            sname = strategy_map[sk][0]
            risk = all_risks[sk]
            charts = all_charts[sk]
            sig = all_signals[sk]

            _print_section(f'策略: {sname}')

            # 最新信号
            sig_text = _get_signal_text(sig)
            sig_color = _get_signal_color(sig_text)
            click.echo(click.style('    最新信号: ', fg='white', bold=True) +
                       click.style(sig_text, fg=sig_color, bold=True))

            # 次日触发价位预测
            th = forecast_thresholds.get(sk)
            if th:
                parts = []
                if th.get('buy') is not None:
                    parts.append(f'买入触发: ≥{th["buy"]:.0f}%')
                if th.get('sell') is not None:
                    parts.append(f'卖出触发: ≤{th["sell"]:.0f}%')
                if parts:
                    click.echo(click.style('    预测(次日涨跌幅): ' + '　'.join(parts), fg='cyan'))

            headers = ['指标', '数值']
            rows = _format_risk_report_rows(risk)
            _print_table(headers, rows)

            if charts:
                click.echo(click.style(f'\n    生成的图表:', fg='white'))
                for chart_name, chart_path in charts.items():
                    click.echo(click.style(f'      - {chart_name}: {chart_path}', fg='green'))

        # 如果运行了多个策略，输出对比
        if len(strategies_to_report) > 1:
            _print_section('策略对比排名')
            rankings = []
            for sk in strategies_to_report:
                rank_total_return = all_risks[sk].get('total_return')
                rank_sharpe = all_risks[sk].get('sharpe_ratio')
                rank_drawdown = all_risks[sk].get('max_drawdown')
                rank_buy = all_risks[sk].get('buy_count')
                rank_sell = all_risks[sk].get('sell_count')
                rank_signal = _get_signal_text(all_signals.get(sk))
                rankings.append((strategy_map[sk][0], rank_total_return, rank_sharpe,
                                 rank_drawdown, rank_buy, rank_sell, rank_signal))
            rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

            headers = ['排名', '策略', '总收益率', '夏普比率', '最大回撤', '买入/卖出次数', '最新信号']
            table_rows = []
            for i, (name, total_ret, sharpe, drawdown, buy_c, sell_c, signal) in enumerate(rankings, 1):
                ret_str = f'{total_ret * 100:.2f}%' if total_ret is not None else 'N/A'
                shp_str = f'{sharpe:.2f}' if sharpe is not None else 'N/A'
                dd_str = f'{drawdown * 100:.2f}%' if drawdown is not None else 'N/A'
                tr_str = f'{buy_c or 0:.1f}/{sell_c or 0:.1f}'
                table_rows.append([i, name, ret_str, shp_str, dd_str, tr_str, signal])
            _print_table(headers, table_rows)

        # 信号过滤：仅当主信号符合 -f 指定条件时才输出报告
        if filter_signal and not _match_filter(filter_signal, all_signals, all_risks):
            click.echo(click.style(
                f'  信号过滤: 主信号不符合 "{_FILTER_SIGNAL_TEXT[filter_signal]}" 条件，已跳过该股票', fg='yellow'))
            return None

        # 生成 HTML 报告（单只股票时）
        if not batch_mode:
            try:
                html_path = _generate_html_report(
                    symbol, stock_name, start_date, end_date, capital,
                    is_index, strategy_map, strategies_to_report,
                    all_results, all_risks, all_charts,
                    all_signals=all_signals,
                    latest_close=latest_close,
                    latest_date=latest_date,
                    trading_days=len(df),
                    report_filename=output_file,
                    cmd_params=cmd_params,
                    strategy=strategy,
                    mode=mode
                )
                click.echo(click.style(f'\n  HTML报告: {html_path}', fg='green'))
            except Exception as e:
                click.echo(click.style(f'\n  HTML报告生成失败: {e}', fg='yellow'))

        click.echo()
        click.echo(click.style('  分析完成!', fg='green', bold=True))
        click.echo()

        # 返回数据供批量报告使用
        return {
            'symbol': symbol,
            'stock_name': stock_name,
            'latest_close': latest_close,
            'latest_date': latest_date,
            'start_date': start_date,
            'end_date': end_date,
            'trading_days': len(df),
            'capital': capital,
            'is_index': is_index,
            # 仅保留策略名（类/闭包不可跨进程 pickle）
            'strategy_map': {k: v[0] for k, v in strategy_map.items()},
            'strategies_to_run': strategies_to_report,
            'all_results': all_results,
            'all_risks': all_risks,
            'all_charts': all_charts,
            'all_signals': all_signals,
            'forecast_thresholds': forecast_thresholds,
        }

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


def _analyze_single_worker(args):
    """多进程 worker：分析单只股票，捕获其控制台输出，返回 (raw_symbol, data, 输出文本)"""
    raw_symbol, start, end, strategy, is_index, capital, output, output_file, batch_mode, cmd_params, mode, filter_signal, chart, forecast = args
    buf = io.StringIO()
    data = None
    try:
        with contextlib.redirect_stdout(buf):
            data = _analyze_single(raw_symbol, start, end, strategy, is_index, capital,
                                   output, output_file, batch_mode=batch_mode, cmd_params=cmd_params,
                                   mode=mode, filter_signal=filter_signal, chart=chart, forecast=forecast)
    except Exception as e:
        buf.write(f'\n  异常: {e}\n')
    return raw_symbol, data, buf.getvalue()


def _run_rotation_analysis(symbols, start, end, strategy, capital, output, output_file, cmd_params='',
                           source=None, mode=0, chart=False):
    """模式 1：资金利用最大化轮动选股

    每天先检查所持股票是否有卖出信号，有则全部卖出；再遍历股票池，
    对出现买入信号(1)的股票用全部剩余资金买入；若剩余资金 < 总资金/10
    且有持仓权益 > 总资金/3，则卖出该持仓的一半释放资金后再买入。
    分析周期内持仓股票可能变化。支持 1 只及以上股票，报告格式与模式 2/3/4 一致。
    """
    if len(symbols) < 1:
        click.echo(click.style('  错误: 轮动模式至少需要 1 只股票', fg='red'))
        return

    start_date, end_date = _parse_dates(start, end)

    # 确定轮动所用策略
    if strategy == 'all':
        strategy_key = 'ma_cross'
        strategy_enhance = 0
    else:
        strategy_key, strategy_enhance = _parse_strategy_spec(_split_strategies(strategy)[0])
    if strategy_key in STRATEGY_MAP:
        strat = _build_strategy_instance(strategy_key, STRATEGY_MAP, strategy_enhance)
        strategy_name = STRATEGY_MAP[strategy_key][0]
    else:
        click.echo(click.style(f'  错误: 未知策略 "{strategy_key}"', fg='red'))
        return

    click.echo(click.style(f'\n  轮动策略: {strategy_name}', fg='white'))
    click.echo(click.style(f'  股票池: {len(symbols)} 只  区间: {start_date} ~ {end_date}', fg='white'))

    # 获取每只股票的数据与信号
    fetcher = DataFetcher()
    stock_data = {}  # symbol -> (df, signals)
    for idx, symbol in enumerate(symbols):
        click.echo(click.style(f'  [{idx+1}/{len(symbols)}] 计算 {symbol} 信号...', fg='blue'))
        try:
            df = fetcher.get_stock_data(symbol, start_date, end_date)
            if df is None or df.empty:
                continue
            df = add_all_indicators(df)
            df.attrs['symbol'] = symbol
            sig = strat.generate_signals(df)
            stock_data[symbol] = (df, sig)
        except Exception as e:
            click.echo(click.style(f'  {symbol} 信号计算失败: {e}', fg='yellow'))

    if len(stock_data) < 1:
        click.echo(click.style('  错误: 有效股票数据不足', fg='red'))
        return

    # 日期对齐（取并集）
    all_dates = sorted(set().union(*[set(df.index) for df, _ in stock_data.values()]))

    total = float(capital)          # 总资金（固定为初始资金）
    cash = float(capital)
    cash_by_date = {}               # 交易日 -> 当日剩余资金（用于按日历日累计闲置资金）
    positions = {}                  # symbol -> {'shares','entry_date','entry_price'}
    events = []                     # (date, sell_symbol, sell_amount, buy_symbol, buy_amount, equity, hold_count, cleared_symbols, cash)
    trade_stats = {}                # symbol -> {'buy_amount','sell_amount','first_buy','last_sell'}
    daily_eq_sum = {}               # symbol -> 持股期间每日市值之和（用于计算日平均权益）
    daily_eq_count = {}             # symbol -> 持股天数
    equity_curve = []               # 每日权益（用于计算夏普比率与最大回撤）

    def _close_price(symbol, d):
        edf = stock_data[symbol][0]
        p = edf['close'].asof(d)
        if pd.isna(p):
            p = edf['close'].iloc[-1]
        return float(p)

    def _equity(d):
        eq = cash
        for sym, pos in positions.items():
            eq += pos['shares'] * _close_price(sym, d)
        return eq

    def _accumulate_daily_equity(d):
        for sym, pos in positions.items():
            mv = pos['shares'] * _close_price(sym, d)
            daily_eq_sum[sym] = daily_eq_sum.get(sym, 0.0) + mv
            daily_eq_count[sym] = daily_eq_count.get(sym, 0) + 1
        cash_by_date[d.strftime('%Y-%m-%d')] = cash
        equity_curve.append(_equity(d))

    for date in all_dates:
        # 步骤0：卖出检查 —— 持仓中信号为 -1 的全部清仓
        sold_list = []
        sold_amount = 0.0
        cleared_list = []
        for symbol in list(positions.keys()):
            df, sig = stock_data[symbol]
            if date in df.index and sig.loc[date] < 0:
                pos = positions[symbol]
                price = float(df['close'].loc[date])
                amount = pos['shares'] * price
                cash += amount
                sold_amount += amount
                del positions[symbol]
                sold_list.append(symbol)
                cleared_list.append(symbol)
                st = trade_stats.setdefault(symbol, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                st['sell_amount'] += amount
                st['last_sell'] = date

        # 步骤1：买入检查 —— 遍历股票池，对买入信号(1)的股票用全部剩余资金买入；
        # 若有多个股票满足条件，只买入第一只符合条件的股票
        buy_symbols = []
        buy_amount_total = 0.0
        sell_symbols = list(sold_list)
        sell_amount_total = sold_amount
        for symbol in symbols:
            if symbol not in stock_data or symbol in positions:
                continue
            df, sig = stock_data[symbol]
            if date in df.index and sig.loc[date] > 0:
                price = float(df['close'].loc[date])
                # 剩余资金不足时，若有持仓权益 > 总资金/3，卖出该持仓的一半释放资金
                if cash < total / 10.0:
                    sell_target = None
                    sell_value = 0.0
                    for held in positions:
                        held_value = positions[held]['shares'] * _close_price(held, date)
                        if held_value > total / 3.0 and held_value > sell_value:
                            sell_target = held
                            sell_value = held_value
                    if sell_target is not None:
                        pos = positions[sell_target]
                        held_price = _close_price(sell_target, date)
                        sell_shares = pos['shares'] / 2.0
                        pos['shares'] -= sell_shares
                        cash += sell_shares * held_price
                        sell_symbols.append(sell_target)
                        sell_amount_total += sell_shares * held_price
                        st = trade_stats.setdefault(sell_target, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                        st['sell_amount'] += sell_shares * held_price
                        st['last_sell'] = date
                buy_amount = cash
                if buy_amount <= 0:
                    continue
                shares = buy_amount / price
                cash -= buy_amount
                positions[symbol] = {'shares': shares, 'entry_date': date, 'entry_price': price}
                buy_symbols.append(symbol)
                buy_amount_total += buy_amount
                st = trade_stats.setdefault(symbol, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                st['buy_amount'] += buy_amount
                break

        # 步骤2：记录当日交易事件（有买卖行为时）
        if buy_symbols or sell_symbols:
            sell_str = '/'.join(sell_symbols) if sell_symbols else None
            buy_str = '/'.join(buy_symbols) if buy_symbols else None
            events.append((date, sell_str, sell_amount_total, buy_str, buy_amount_total,
                           _equity(date), len(positions), cleared_list, cash))
        _accumulate_daily_equity(date)

    # 期末市值（不强制平仓，保留期末持仓）
    final_value = cash
    for symbol, pos in positions.items():
        edf = stock_data[symbol][0]
        final_value += pos['shares'] * float(edf['close'].iloc[-1])

    total_return = (final_value - total) / total if total else 0.0
    total_shares = sum(pos['shares'] for pos in positions.values())
    equity_curve.append(final_value)
    sharpe, max_dd = _calc_sharpe_drawdown(equity_curve)

    # 闲置资金天数 = 分析周期内每日剩余资金累计 / 资金总额
    period_days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days + 1
    cum_cash = 0.0
    carry = float(capital)
    day0 = pd.Timestamp(start_date)
    for i in range(period_days):
        day_key = (day0 + pd.Timedelta(days=i)).strftime('%Y-%m-%d')
        if day_key in cash_by_date:
            carry = cash_by_date[day_key]
        cum_cash += carry
    idle_days = (cum_cash / total) if total else 0.0

    # 解析持仓股票名称
    name_map = {}
    held = set(positions.keys()) | set(trade_stats.keys())
    for e in events:
        if e[1]:
            held.update(str(e[1]).split('/'))
        if e[3]:
            held.update(str(e[3]).split('/'))
    for s in held:
        code, name = _resolve_symbol(s)
        name_map[s] = name if name else s

    # 个股收益统计
    stock_stats = []
    end_dt = all_dates[-1] if all_dates else None
    for sym, st in trade_stats.items():
        buy_amt = st['buy_amount']
        sell_amt = st['sell_amount']
        final_mv = 0.0
        if sym in positions:
            edf = stock_data[sym][0]
            final_mv = positions[sym]['shares'] * float(edf['close'].iloc[-1])
        total_profit = sell_amt + final_mv - buy_amt
        hold_cnt = daily_eq_count.get(sym, 0)
        avg_eq = (daily_eq_sum.get(sym, 0.0) / hold_cnt) if hold_cnt > 0 else 0.0
        ret = (total_profit / avg_eq) if avg_eq else 0.0
        exit_date = st['last_sell'] if st['last_sell'] is not None else end_dt
        hold_days = (exit_date - st['first_buy']).days if st['first_buy'] is not None and exit_date is not None else 0
        stock_stats.append((sym, buy_amt, sell_amt, final_mv, total_profit, ret, hold_days))
    stock_stats.sort(key=lambda x: x[4], reverse=True)

    # 打印结果
    click.echo()
    _print_section('多持仓组合结果')
    click.echo(click.style(f'  总权益: {final_value:,.2f}', fg='green', bold=True))
    click.echo(click.style(f'  总收益率: {total_return * 100:.2f}%', fg='green' if total_return >= 0 else 'red', bold=True))
    click.echo(click.style(f'  夏普比率: {sharpe:.2f}', fg='white'))
    click.echo(click.style(f'  最大回撤: {max_dd * 100:.2f}%', fg='white'))
    click.echo(click.style(f'  持股数量: {total_shares:,.0f} 股', fg='white'))
    click.echo(click.style(f'  交易次数: {len(events)}', fg='white'))
    click.echo(click.style(f'  闲置资金天数/分析周期天数: {idle_days:.2f} / {period_days}', fg='white'))

    _print_section('个股收益统计')
    headers2 = ['股票代码', '股票名称', '买入金额', '卖出金额', '期末市值', '总收益', '收益率', '持股天数']
    rows2 = []
    for sym, buy_amt, sell_amt, final_mv, total_profit, ret, hold_days in stock_stats:
        rows2.append([sym, name_map.get(sym, sym), f'{buy_amt:,.0f}', f'{sell_amt:,.0f}',
                      f'{final_mv:,.0f}', f'{total_profit:,.0f}', f'{ret * 100:.2f}%', str(hold_days)])
    _print_table(headers2, rows2)

    # 生成 HTML 报告
    try:
        html_path = _generate_portfolio_html(
            symbols, strategy_name, start_date, end_date, capital, final_value,
            total_return, events, positions, stock_data, name_map, stock_stats,
            idle_days, period_days, output_file, cmd_params,
            source=source, strategy=strategy, mode=mode, sharpe=sharpe, max_drawdown=max_dd,
            output=output, chart=chart
        )
        click.echo(click.style(f'\n  组合报告: {html_path}', fg='green'))
    except Exception as e:
        click.echo(click.style(f'\n  组合报告生成失败: {e}', fg='yellow'))


def _run_portfolio_analysis(symbols, start, end, strategy, capital, output, output_file,
                            weak_trend_cancel=False, trend_priority_sell=False, cmd_params='',
                            source=None, mode=0, chart=False):
    """模式 2：多持仓资金利用最大化

    每天先按 symbols 顺序找卖出信号（信号 == -1）的持仓并全部卖出；
    再找第一个买入信号（多个则只取第一个）的股票，剩余资金 > 1/10 总资金时
    用剩余资金买入（单只买入不超过 1/3 总资金）；不足则从持仓中选出
    MACD 柱下降（今日 < 昨日）的一只股票，按持仓市值 > 总资金 20% 卖一半、
    否则全卖释放资金买入；若持仓中无 MACD 柱下降股票，则放弃当日买入。
    支持 1 只及以上股票（1 只作为多只的特例处理）。

    weak_trend_cancel: 若为 True，则在现金不足分支中，若买入股当前趋势为弱势，
        取消被动卖出和买入。
    trend_priority_sell: 若为 True，则在多个候选卖出股票中按趋势优先级选择
        （弱势 > 横盘震荡，取消强势股卖出），而非最早买入者。
    """
    if len(symbols) < 1:
        click.echo(click.style('  错误: 该模式至少需要 1 只股票', fg='red'))
        return

    start_date, end_date = _parse_dates(start, end)

    # 确定策略
    if strategy == 'all':
        strategy_key = 'ma_cross'
        strategy_enhance = 0
    else:
        strategy_key, strategy_enhance = _parse_strategy_spec(_split_strategies(strategy)[0])
    if strategy_key in STRATEGY_MAP:
        strat = _build_strategy_instance(strategy_key, STRATEGY_MAP, strategy_enhance)
        strategy_name = STRATEGY_MAP[strategy_key][0]
    else:
        click.echo(click.style(f'  错误: 未知策略 "{strategy_key}"', fg='red'))
        return

    click.echo(click.style(f'\n  组合策略: {strategy_name}', fg='white'))
    click.echo(click.style(f'  股票池: {len(symbols)} 只  区间: {start_date} ~ {end_date}', fg='white'))

    # 获取每只股票的数据与信号
    fetcher = DataFetcher()
    stock_data = {}
    for idx, symbol in enumerate(symbols):
        click.echo(click.style(f'  [{idx+1}/{len(symbols)}] 计算 {symbol} 信号...', fg='blue'))
        try:
            df = fetcher.get_stock_data(symbol, start_date, end_date)
            if df is None or df.empty:
                continue
            df = add_all_indicators(df)
            df.attrs['symbol'] = symbol
            sig = strat.generate_signals(df)
            stock_data[symbol] = (df, sig)
        except Exception as e:
            click.echo(click.style(f'  {symbol} 信号计算失败: {e}', fg='yellow'))

    if len(stock_data) < 1:
        click.echo(click.style('  错误: 有效股票数据不足 1 只', fg='red'))
        return

    all_dates = sorted(set().union(*[set(df.index) for df, _ in stock_data.values()]))

    total = float(capital)          # 总资金（固定为初始资金）
    cash = float(capital)
    cash_by_date = {}               # 交易日 -> 当日剩余资金（用于按日历日累计闲置资金）
    positions = {}                  # symbol -> {'shares','entry_date','avg_price'}
    events = []                     # (date, sell_symbol, sell_amount, buy_symbol, buy_amount, equity, hold_count, cleared_symbols, cash)
    trade_stats = {}                # symbol -> {'buy_amount','sell_amount','first_buy','last_sell'}
    daily_eq_sum = {}               # symbol -> 持股期间每日市值之和（用于计算日平均权益）
    daily_eq_count = {}             # symbol -> 持股天数
    equity_curve = []               # 每日权益（用于计算夏普比率与最大回撤）

    def _close_price(symbol, d):
        edf = stock_data[symbol][0]
        p = edf['close'].asof(d)
        if pd.isna(p):
            p = edf['close'].iloc[-1]
        return float(p)

    def _equity(d):
        eq = cash
        for sym, pos in positions.items():
            eq += pos['shares'] * _close_price(sym, d)
        return eq

    def _macd_declining(symbol, d):
        """判断该股在 d 当日（或之前最近交易日）MACD 柱是否较前一交易日下降"""
        edf = stock_data[symbol][0]
        sub = edf.loc[edf.index <= d]
        if len(sub) < 2 or 'MACD_BAR' not in sub.columns:
            return False
        today_bar = sub['MACD_BAR'].iloc[-1]
        yest_bar = sub['MACD_BAR'].iloc[-2]
        if pd.isna(today_bar) or pd.isna(yest_bar):
            return False
        return today_bar < yest_bar

    def _accumulate_daily_equity(d):
        """累计每个持仓股票当日市值，并记录当日剩余资金"""
        for sym, pos in positions.items():
            mv = pos['shares'] * _close_price(sym, d)
            daily_eq_sum[sym] = daily_eq_sum.get(sym, 0.0) + mv
            daily_eq_count[sym] = daily_eq_count.get(sym, 0) + 1
        cash_by_date[d.strftime('%Y-%m-%d')] = cash
        equity_curve.append(_equity(d))

    def _buy_cash(cash_avail):
        """计算实际买入金额：单只买入不超过 1/3 总资金"""
        return min(cash_avail, total / 3.0)

    def _trend_rank(symbol, d):
        """趋势卖出优先级：弱势 0 > 横盘震荡 1（强势不参与被动卖出）"""
        t = classify_trend(stock_data[symbol][0], d)
        return {'弱势': 0, '横盘震荡': 1}.get(t, 1)

    for date in all_dates:
        # 步骤0：找卖出信号 —— 按 symbols 顺序遍历股票池，信号为 -1 的持仓全部卖出
        sold_list = []
        sold_amount = 0.0
        cleared_list = []   # 本日清仓（全部卖出）的股票
        for symbol in symbols:
            if symbol not in stock_data or symbol not in positions:
                continue
            df, sig = stock_data[symbol]
            if date in df.index and sig.loc[date] < 0:
                pos = positions[symbol]
                cur_price = _close_price(symbol, date)
                amount = pos['shares'] * cur_price
                cash += amount
                sold_amount += amount
                del positions[symbol]
                sold_list.append(symbol)
                cleared_list.append(symbol)
                st = trade_stats.setdefault(symbol, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                st['sell_amount'] += amount
                st['last_sell'] = date

        # 步骤1：找第一个买入信号的股票
        buy_symbol = None
        buy_price = None
        for symbol in symbols:
            if symbol not in stock_data:
                continue
            df, sig = stock_data[symbol]
            if date in df.index and sig.loc[date] > 0:
                buy_symbol = symbol
                buy_price = float(df['close'].loc[date])
                break

        if buy_symbol is None:
            if sold_list:
                events.append((date, '/'.join(sold_list), sold_amount, None, 0.0, _equity(date), len(positions), cleared_list, cash))
            _accumulate_daily_equity(date)
            continue

        sell_symbol = None
        sell_amount = 0.0
        buy_amount = 0.0

        if cash > total * 0.1:
            # 剩余资金充足：买入（单只不超过 1/3 总资金）
            buy_amount = _buy_cash(cash)
            buy_shares = buy_amount / buy_price
            cash -= buy_amount
            if buy_symbol in positions:
                p = positions[buy_symbol]
                p['shares'] += buy_shares
                p['avg_price'] = (p['avg_price'] * (p['shares'] - buy_shares) + buy_price * buy_shares) / p['shares']
            else:
                positions[buy_symbol] = {'shares': buy_shares, 'entry_date': date, 'avg_price': buy_price}
            st = trade_stats.setdefault(buy_symbol, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
            st['buy_amount'] += buy_amount
        else:
            # 剩余资金不足：若买入股趋势为弱势则取消被动卖出和买入
            weak_cancel = (weak_trend_cancel
                           and classify_trend(stock_data[buy_symbol][0], date) == '弱势')
            if not weak_cancel:
                # 在持仓中找 MACD 柱下降的股票作为卖出对象
                # 若当日已有主动卖出（信号 == -1），则不再执行被动卖出
                if not sold_list and positions:
                    if trend_priority_sell:
                        # 模式4：取消强势股卖出，仅弱势/横盘震荡参与被动卖出
                        candidates = [s for s in positions if _macd_declining(s, date)
                                      and classify_trend(stock_data[s][0], date) != '强势']
                    else:
                        candidates = [s for s in positions if _macd_declining(s, date)]
                    if candidates:
                        if trend_priority_sell:
                            sell_target = min(candidates, key=lambda s: (_trend_rank(s, date), positions[s]['entry_date']))
                        else:
                            sell_target = min(candidates, key=lambda s: positions[s]['entry_date'])
                        pos = positions[sell_target]
                        cur_price = _close_price(sell_target, date)
                        pos_value = pos['shares'] * cur_price
                        # 被动卖出：持仓市值 > 总资金×20% 卖一半，否则全卖
                        if pos_value > total * 0.2:
                            sell_shares = pos['shares'] / 2.0
                            pos['shares'] -= sell_shares
                            cash += sell_shares * cur_price
                            sell_symbol = sell_target
                            sell_amount = sell_shares * cur_price
                        else:
                            sold_shares = pos['shares']
                            cash += sold_shares * cur_price
                            del positions[sell_target]
                            sell_symbol = sell_target
                            sell_amount = sold_shares * cur_price
                            cleared_list.append(sell_target)
                        st = trade_stats.setdefault(sell_target, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                        st['sell_amount'] += sell_amount
                        st['last_sell'] = date
                # 只有发生卖出释放资金时才买入；否则放弃当日买入
                if sell_symbol is not None and cash > 0:
                    buy_amount = _buy_cash(cash)
                    buy_shares = buy_amount / buy_price
                    cash -= buy_amount
                    if buy_symbol in positions:
                        p = positions[buy_symbol]
                        p['shares'] += buy_shares
                        p['avg_price'] = (p['avg_price'] * (p['shares'] - buy_shares) + buy_price * buy_shares) / p['shares']
                    else:
                        positions[buy_symbol] = {'shares': buy_shares, 'entry_date': date, 'avg_price': buy_price}
                    st = trade_stats.setdefault(buy_symbol, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                    st['buy_amount'] += buy_amount

        # 当日买入被放弃（无足够资金且无卖出）时，买入股票置空
        if buy_amount <= 0:
            buy_symbol = None

        # 合并本日所有卖出（卖出信号 + 资金不足时的 MACD 卖出）
        day_sell_symbols = list(sold_list)
        day_sell_amount = sold_amount
        if sell_symbol is not None:
            day_sell_symbols.append(sell_symbol)
            day_sell_amount += sell_amount
        day_sell_str = '/'.join(day_sell_symbols) if day_sell_symbols else None

        events.append((date, day_sell_str, day_sell_amount, buy_symbol, buy_amount, _equity(date), len(positions), cleared_list, cash))
        _accumulate_daily_equity(date)

    # 期末市值
    final_value = cash
    for symbol, pos in positions.items():
        edf = stock_data[symbol][0]
        final_value += pos['shares'] * float(edf['close'].iloc[-1])

    total_return = (final_value - total) / total if total else 0.0
    total_shares = sum(pos['shares'] for pos in positions.values())
    equity_curve.append(final_value)
    sharpe, max_dd = _calc_sharpe_drawdown(equity_curve)

    # 闲置资金天数 = 分析周期内每日剩余资金累计 / 资金总额
    # 按日历日累计：非交易日沿用最近交易日的剩余资金
    period_days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days + 1
    cum_cash = 0.0
    carry = float(capital)
    day0 = pd.Timestamp(start_date)
    for i in range(period_days):
        day_key = (day0 + pd.Timedelta(days=i)).strftime('%Y-%m-%d')
        if day_key in cash_by_date:
            carry = cash_by_date[day_key]
        cum_cash += carry
    idle_days = (cum_cash / total) if total else 0.0

    # 解析持仓股票名称
    name_map = {}
    held = set(positions.keys()) | set(trade_stats.keys())
    for e in events:
        if e[1]:
            held.update(str(e[1]).split('/'))
        if e[3]:
            held.add(e[3])
    for s in held:
        code, name = _resolve_symbol(s)
        name_map[s] = name if name else s

    # 个股收益统计
    stock_stats = []
    end_dt = all_dates[-1] if all_dates else None
    for sym, st in trade_stats.items():
        buy_amt = st['buy_amount']
        sell_amt = st['sell_amount']
        final_mv = 0.0
        if sym in positions:
            edf = stock_data[sym][0]
            final_mv = positions[sym]['shares'] * float(edf['close'].iloc[-1])
        total_profit = sell_amt + final_mv - buy_amt
        hold_cnt = daily_eq_count.get(sym, 0)
        avg_eq = (daily_eq_sum.get(sym, 0.0) / hold_cnt) if hold_cnt > 0 else 0.0
        ret = (total_profit / avg_eq) if avg_eq else 0.0
        exit_date = st['last_sell'] if st['last_sell'] is not None else end_dt
        hold_days = (exit_date - st['first_buy']).days if st['first_buy'] is not None and exit_date is not None else 0
        stock_stats.append((sym, buy_amt, sell_amt, final_mv, total_profit, ret, hold_days))
    stock_stats.sort(key=lambda x: x[4], reverse=True)

    # 打印结果
    click.echo()
    _print_section('多持仓组合结果')
    click.echo(click.style(f'  总权益: {final_value:,.2f}', fg='green', bold=True))
    click.echo(click.style(f'  总收益率: {total_return * 100:.2f}%', fg='green' if total_return >= 0 else 'red', bold=True))
    click.echo(click.style(f'  夏普比率: {sharpe:.2f}', fg='white'))
    click.echo(click.style(f'  最大回撤: {max_dd * 100:.2f}%', fg='white'))
    click.echo(click.style(f'  持股数量: {total_shares:,.0f} 股', fg='white'))
    click.echo(click.style(f'  交易次数: {len(events)}', fg='white'))
    click.echo(click.style(f'  闲置资金天数/分析周期天数: {idle_days:.2f} / {period_days}', fg='white'))

    _print_section('个股收益统计')
    headers2 = ['股票代码', '股票名称', '买入金额', '卖出金额', '期末市值', '总收益', '收益率', '持股天数']
    rows2 = []
    for sym, buy_amt, sell_amt, final_mv, total_profit, ret, hold_days in stock_stats:
        rows2.append([sym, name_map.get(sym, sym), f'{buy_amt:,.0f}', f'{sell_amt:,.0f}',
                      f'{final_mv:,.0f}', f'{total_profit:,.0f}', f'{ret * 100:.2f}%', str(hold_days)])
    _print_table(headers2, rows2)

    try:
        html_path = _generate_portfolio_html(
            symbols, strategy_name, start_date, end_date, capital, final_value,
            total_return, events, positions, stock_data, name_map, stock_stats,
            idle_days, period_days, output_file, cmd_params,
            source=source, strategy=strategy, mode=mode, sharpe=sharpe, max_drawdown=max_dd,
            output=output, chart=chart
        )
        click.echo(click.style(f'\n  组合报告: {html_path}', fg='green'))
    except Exception as e:
        click.echo(click.style(f'\n  组合报告生成失败: {e}', fg='yellow'))


def _run_portfolio_analysis_v3(symbols, start, end, strategy, capital, output, output_file, cmd_params='',
                               source=None, mode=3, chart=False):
    """模式 3：多持仓强化优化

    在模式 2（多持仓资金利用最大化）基础上，进一步加强对买卖信号
    与股票选择的优化。当前已引入个股趋势判断：现金不足时，若买入股当前
    趋势为弱势，则取消被动卖出和买入。
    """
    _run_portfolio_analysis(symbols, start, end, strategy, capital, output, output_file,
                            weak_trend_cancel=True, cmd_params=cmd_params, source=source, mode=mode,
                            chart=chart)


def _run_portfolio_analysis_v4(symbols, start, end, strategy, capital, output, output_file, cmd_params='',
                               source=None, mode=4, chart=False):
    """模式 4：多持仓强化优化（趋势择股卖出）

    在模式 3 基础上，修改卖出股票的选择方式：当多个持仓同时满足卖出条件时，
    取消强势股卖出，仅在弱势与横盘震荡之间按趋势值优先选择弱势的股票。
    """
    _run_portfolio_analysis(symbols, start, end, strategy, capital, output, output_file,
                            weak_trend_cancel=True, trend_priority_sell=True, cmd_params=cmd_params,
                            source=source, mode=mode, chart=chart)


def _generate_portfolio_html(pool_symbols, strategy_name, start_date, end_date, capital,
                             final_value, total_return, events, positions, stock_data,
                             name_map, stock_stats, idle_days, period_days, output_file,
                             cmd_params='', source=None, strategy=None, mode=0,
                             sharpe=0.0, max_drawdown=0.0, output='./output', chart=False):
    """生成多持仓组合 HTML 报告"""
    if output_file:
        report_path = output_file
    else:
        days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
        report_path = _build_report_filename(source, end_date, days, strategy, mode,
                                             profit=_profit_int(total_return))
    if not os.path.dirname(report_path):
        report_path = os.path.join('report', report_path)

    tx_rows = ''
    for date, sell_symbol, sell_amount, buy_symbol, buy_amount, equity, hold_count, cleared_symbols, cash in events:
        if sell_symbol:
            parts = []
            for s in sell_symbol.split('/'):
                nm = name_map.get(s, s)
                parts.append(f'{s} {nm}(清)' if s in cleared_symbols else f'{s} {nm}')
            sell_str = '<br>'.join(parts)
        else:
            sell_str = '-'
        sell_amt = f'{sell_amount:,.0f}' if sell_amount else '-'
        if buy_symbol:
            buy_str = '<br>'.join(f'{s} {name_map.get(s, s)}' for s in buy_symbol.split('/'))
        else:
            buy_str = '-'
        buy_amt = f'{buy_amount:,.0f}' if buy_amount else '-'
        tx_rows += f'''<tr>
            <td>{date.date()}</td><td>{buy_str}</td><td>{buy_amt}</td>
            <td>{sell_str}</td><td>{sell_amt}</td>
            <td>{equity:,.0f}</td><td>{cash:,.0f}</td><td>{hold_count}</td>
        </tr>'''

    stat_rows = ''
    for sym, buy_amt, sell_amt, final_mv, total_profit, ret, hold_days in stock_stats:
        ret_color = '#27ae60' if total_profit >= 0 else '#e74c3c'
        stat_rows += f'''<tr>
            <td>{sym}</td><td>{name_map.get(sym, sym)}</td>
            <td>{buy_amt:,.0f}</td><td>{sell_amt:,.0f}</td><td>{final_mv:,.0f}</td>
            <td style="color:{ret_color};font-weight:bold">{total_profit:,.0f}</td>
            <td style="color:{ret_color}">{ret * 100:.2f}%</td><td>{hold_days}</td>
        </tr>'''

    profit_map = {s: tp for s, _, _, _, tp, _, _ in stock_stats}
    hold_rows = ''
    total_shares = 0.0
    total_market = 0.0
    for sym, pos in positions.items():
        edf = stock_data[sym][0]
        last_price = float(edf['close'].iloc[-1])
        val = pos['shares'] * last_price
        total_shares += pos['shares']
        total_market += val
        total_profit = profit_map.get(sym, 0.0)
        cost_price = (val - total_profit) / val * last_price if val > 0 else 0.0
        hold_rows += f'<tr><td>{sym}</td><td>{name_map.get(sym, sym)}</td><td>{pos["shares"]:.0f}</td>'
        hold_rows += f'<td>{cost_price:.2f}</td><td>{last_price:.2f}</td><td>{val:,.0f}</td></tr>'
    hold_rows += f'<tr style="font-weight:bold;background:#eaf2f8"><td>合计</td><td>-</td><td>{total_shares:,.0f}</td><td>-</td><td>-</td><td>{total_market:,.0f}</td></tr>'

    pool_str = '，'.join(str(s) for s in pool_symbols)
    ret_color = '#27ae60' if total_return >= 0 else '#e74c3c'

    # 生成所有买入股票的 K 线图（价格+买卖点、成交量、MACD），内嵌到报告末尾
    import base64 as _b64

    def _img_to_b64(path):
        if not path or not os.path.exists(path):
            return ''
        with open(path, 'rb') as f:
            return _b64.b64encode(f.read()).decode()

    date_tag = _build_date_tag(start_date, end_date)
    bought_symbols = [sym for sym, buy_amt, *_ in stock_stats if buy_amt > 0]
    charts_html = ''
    if chart:
        for sym in bought_symbols:
            if sym not in stock_data:
                continue
            df, sig = stock_data[sym]
            label = f'{sym} {name_map.get(sym, sym)}'.strip()
            try:
                cg = ChartGenerator(output_dir=output, prefix=f'{sym}_portfolio', date_tag=date_tag)
                chart_path = cg.plot_signal_composite(
                    df, sig, strategy_key='macd', title=f'{label} K线与买卖点'
                )
                b64 = _img_to_b64(chart_path)
                if b64:
                    ext = os.path.splitext(chart_path)[1].lstrip('.')
                    charts_html += f'<div class="chart"><h3>{label}</h3>'
                    charts_html += f'<img src="data:image/{ext};base64,{b64}" alt="{label}" style="max-width:100%;border:1px solid #ddd;border-radius:4px;"></div>'
            except Exception as e:
                charts_html += f'<div class="chart">图表生成失败 {label}: {e}</div>'

    chart_section = '<h2>买入股票 K 线图</h2>' + charts_html if chart else ''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>多持仓组合报告</title>
<style>
body {{ font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; max-width: 1100px; margin: 0 auto; padding: 20px; background: #fafafa; color: #2c3e50; }}
h1 {{ border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
h2 {{ border-bottom: 2px solid #bdc3c7; padding-bottom: 6px; margin-top: 30px; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: center; }}
th {{ background: #3498db; color: white; }}
tr:nth-child(even) {{ background: #f2f2f2; }}
.info {{ background: #ecf0f1; padding: 15px; border-radius: 5px; margin: 15px 0; }}
.info span {{ margin-right: 30px; }}
.chart {{ margin: 20px 0; text-align: center; }}
.footer {{ text-align: center; color: #95a5a6; margin-top: 40px; font-size: 12px; }}
</style>
</head>
<body>
<h1>多持仓资金利用最大化报告</h1>
<div class="info">
    <span><strong>策略:</strong> {strategy_name}</span>
    <span><strong>区间:</strong> {start_date} ~ {end_date}</span>
    <span><strong>初始资金:</strong> {capital:,.0f}</span>
    <span><strong>总权益:</strong> {final_value:,.0f}</span>
    <span><strong>持股数量:</strong> {total_shares:,.0f} 股</span>
    <span><strong>总收益率:</strong> <span style="color:{ret_color};font-weight:bold">{total_return * 100:.2f}%</span></span>
    <span><strong>夏普比率:</strong> {sharpe:.2f}</span>
    <span><strong>最大回撤:</strong> {max_drawdown * 100:.2f}%</span>
    <span><strong>交易次数:</strong> {len(events)}</span>
    <span><strong>闲置资金天数/分析周期:</strong> {idle_days:.2f} / {period_days}</span>
</div>
<div class="info">
    <span><strong>股票池({len(pool_symbols)}只):</strong> {pool_str}</span>
</div>
<div class="info">
    <span><strong>命令参数:</strong> {cmd_params}</span>
</div>

<h2>交易记录</h2>
<table>
<tr><th>日期</th><th>买入股票</th><th>买入资金</th><th>卖出股票</th><th>卖出资金</th><th>总权益</th><th>剩余资金</th><th>持股种类数</th></tr>
{tx_rows}
</table>

<h2>个股收益统计</h2>
<table>
<tr><th>股票代码</th><th>股票名称</th><th>买入金额</th><th>卖出金额</th><th>期末市值</th><th>总收益</th><th>收益率</th><th>持股天数</th></tr>
{stat_rows}
</table>

<h2>期末持仓</h2>
<table>
<tr><th>股票代码</th><th>股票名称</th><th>持股数</th><th>成本价</th><th>最新价</th><th>市值</th></tr>
{hold_rows}
</table>

{chart_section}

<div class="footer"><p>报告由 stock-quant 自动生成</p></div>
</body>
</html>'''

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


# ============================================================================
# scan 命令 - 股票扫描筛选
# ============================================================================


def _scan_single(symbol, strategy, capital=100000, forecast=0, enhance=0):
    """扫描单只股票：获取近一年数据 -> 运行策略 -> 返回信号、最新价及风险指标
    在独立进程中调用时自建 DataFetcher（requests.Session 非线程/进程安全）。
    forecast=True 时对观望信号计算次日触发买卖信号的收盘价阈值。
    """
    try:
        fetcher = DataFetcher()
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        end_date = datetime.now().strftime('%Y-%m-%d')
        df = fetcher.get_stock_data(symbol, start_date, end_date)
        if df is None or len(df) < 100:
            return None
        df, latest_close, _, _ = _apply_realtime_to_df(df, symbol, fetcher, end_date, verbose=False)
        df = add_all_indicators(df)
        result = _run_strategy(strategy, df, capital=capital, enhance=enhance)
        risk = result  # 结果已包含风险指标

        # 取最新信号值（1=强买, 0.5=弱买, 0=观望, -0.5=弱卖, -1=强卖）
        sig_series = result.get('signals')
        signal_value = None
        if sig_series is not None and len(sig_series) > 0:
            signal_value = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])

        if signal_value is None:
            return None

        # 观望信号：计算次日触发买卖信号的收盘价阈值（仅 --forecast 启用时）
        forecast_val = None
        if forecast and signal_value == 0:
            th = _compute_forecast_thresholds(df, strategy, STRATEGY_MAP, end_date, enhance=enhance)
            if th.get('buy') is not None or th.get('sell') is not None:
                forecast_val = th

        return {
            'symbol': symbol,
            'latest_close': latest_close,
            'signal': signal_value,
            'forecast': forecast_val,
            'total_return': risk.get('total_return'),
            'sharpe': risk.get('sharpe_ratio'),
            'max_dd': risk.get('max_drawdown'),
            'win_rate': risk.get('win_rate'),
            'trades': risk.get('total_trades'),
            'buy_count': risk.get('buy_count'),
            'sell_count': risk.get('sell_count'),
        }
    except Exception:
        return None


def _scan_single_worker(args):
    """多进程 worker：扫描单只股票"""
    symbol, strategy, capital, forecast, enhance = args
    return _scan_single(symbol, strategy, capital, forecast, enhance)


def _scan_multi(symbol, strategies, capital=100000, forecast=0, strat_enhance=None):
    """扫描单只股票并运行多个策略，返回每个策略的最新信号值、预测阈值及信号合计
    在独立进程中调用时自建 DataFetcher（requests.Session 非线程/进程安全）。
    forecast=True 时对观望信号计算次日触发买卖信号的收盘价阈值。
    """
    if strat_enhance is None:
        strat_enhance = {}
    try:
        fetcher = DataFetcher()
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        end_date = datetime.now().strftime('%Y-%m-%d')
        df = fetcher.get_stock_data(symbol, start_date, end_date)
        if df is None or len(df) < 100:
            return None
        df, latest_close, _, _ = _apply_realtime_to_df(df, symbol, fetcher, end_date, verbose=False)
        df = add_all_indicators(df)
        sig_map = {}
        forecast_map = {}
        for sk in strategies:
            try:
                result = _run_strategy(sk, df, capital=capital, enhance=strat_enhance.get(sk, 0))
                sig_series = result.get('signals')
                if sig_series is not None and len(sig_series) > 0:
                    v = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])
                else:
                    v = None
                sig_map[sk] = v
                # 观望信号：计算次日触发买卖信号的收盘价阈值（仅 --forecast 启用时）
                if forecast and v == 0:
                    th = _compute_forecast_thresholds(df, sk, STRATEGY_MAP, end_date, enhance=strat_enhance.get(sk, 0))
                    if th.get('buy') is not None or th.get('sell') is not None:
                        forecast_map[sk] = th
            except Exception:
                sig_map[sk] = None
        if all(v is None for v in sig_map.values()):
            return None
        total = sum(v for v in sig_map.values() if v is not None)
        return {'symbol': symbol, 'latest_close': latest_close, 'signals': sig_map, 'forecasts': forecast_map, 'total': total}
    except Exception:
        return None


def _scan_multi_worker(args):
    """多进程 worker：扫描单只股票（多策略）"""
    symbol, strategies, capital, forecast, strat_enhance = args
    return _scan_multi(symbol, strategies, capital, forecast, strat_enhance)


def _run_scan(scan_symbols, strategies, threads, forecast=0, strat_enhance=None):
    """运行扫描（串行/并行），返回每只股票的结果列表"""
    if strat_enhance is None:
        strat_enhance = {}
    results = []
    if len(scan_symbols) > 1 and threads > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        n_workers = min(threads, len(scan_symbols))
        click.echo(click.style(f'  使用 {n_workers} 个进程并行扫描...', fg='blue'))
        tasks = [(s, strategies, 100000, forecast, strat_enhance) for s in scan_symbols]
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_scan_multi_worker, task): i for i, task in enumerate(tasks)}
            for fut in as_completed(futures):
                data = fut.result()
                if data is not None:
                    results.append(data)
    else:
        for s in scan_symbols:
            data = _scan_multi(s, strategies, 100000, forecast, strat_enhance)
            if data is not None:
                results.append(data)
    return results


@cli.command('scan')
@click.option('--symbol-file', '-sf', default=None, help='从文件顺序读取股票代码（空格/逗号/换行分隔），最多取前 20*N 只（N 为 -tN 值）')
@click.option('--strategy', '-g', default='macd', help='策略选择，多个策略用逗号分隔，all表示全部（默认macd）；-gN 表示全部启用增强级别N（如 -g1 macd,rsi 即 macd1,rsi1）')
@click.option('--top', '-t', default=10, type=click.IntRange(1, 100), help='筛选排名前N只股票 -tN（N=1~100，默认10）')
@click.option('--offset', '-o', default=0, type=click.IntRange(0, 1000000), help='从获取到的股票中第N条记录开始分析 -oN（N=0起始，默认0=第一条）')
@click.option('--min-volume', default=None, type=float, help='最小成交量过滤')
@click.option('--threads', '-x', default=5, type=click.IntRange(1, 6), help='并行进程数 -xN（N=1~6，默认5）')
@click.option('--forecast', is_flag=True, default=False, help='预测次日触发买卖信号的收盘价阈值（不指定则不预测）')
def scan_cmd(symbol_file, strategy, top, offset, min_volume, threads, forecast):
    """股票筛选：从给定股票集中，重点输出符合要求或最佳的股票

    流程：获取股票列表 -> 筛选 -> 逐个运行策略（可用 -x 并行）-> 输出结果
    指定 -sf 时直接从文件顺序读取股票代码（最多 20*N 只）进行分析。
    指定多个策略（逗号分隔）或 all 时，输出多策略汇总信号表（每只股票一行，
    列为策略名，值为买/卖/观，按信号合计值降序排序）。
    """
    try:
        # 解析策略列表
        strat_specs = _split_strategies(strategy)
        if any(k == 'all' for k in strat_specs):
            strat_keys = ALL_STRATEGIES
            strat_enhance = {k: 0 for k in strat_keys}
        else:
            strat_keys = []
            strat_enhance = {}
            for spec in strat_specs:
                base_key, enh = _parse_strategy_spec(spec)
                if base_key not in STRATEGY_MAP:
                    click.echo(click.style(f'  错误: 未知策略 "{base_key}"，可选: {", ".join(STRATEGY_MAP.keys())}', fg='red'))
                    return
                if base_key not in strat_keys:
                    strat_keys.append(base_key)
                strat_enhance[base_key] = enh

        multi = len(strat_keys) > 1
        strat_names = [STRATEGY_MAP[k][0] for k in strat_keys]
        # 汇总信号表使用的短栏名（其余策略沿用完整名称）
        strat_cols = [_SCAN_SHORT_NAMES.get(k, STRATEGY_MAP[k][0]) for k in strat_keys]

        _print_header('股票扫描筛选')
        click.echo(click.style(f'  策略: {", ".join(strat_names)}', fg='cyan', bold=True))

        fetcher = DataFetcher()

        if symbol_file:
            # -sf 模式：直接从文件顺序读取股票代码，跳过前 offset 条，最多取 20*N 只
            file_symbols = _read_symbols_file(symbol_file)
            if not file_symbols:
                return
            file_symbols = file_symbols[offset:]
            scan_symbols = file_symbols[:top * 20]
            click.echo(click.style(f'  从文件读取 {len(file_symbols)} 只股票（跳过前 {offset} 条），取前 {len(scan_symbols)} 只', fg='green'))
            name_map = {s: s for s in scan_symbols}
        else:
            click.echo(click.style('  正在获取股票列表...', fg='blue'))
            stock_list = fetcher.get_stock_list()

            if stock_list is None or len(stock_list) == 0:
                click.echo(click.style('  错误: 未能获取股票列表', fg='red'))
                return

            click.echo(click.style(f'  获取到 {len(stock_list)} 只股票', fg='green'))

            # 按成交量过滤
            if min_volume is not None:
                click.echo(click.style(f'  正在按成交量过滤 (>= {min_volume:,.0f})...', fg='blue'))
                stock_list = fetcher.filter_by_volume(stock_list, min_volume)
                click.echo(click.style(f'  过滤后剩余 {len(stock_list)} 只股票', fg='green'))

            # 限制扫描数量（先跳过前 offset 条，再取前 top*20 只）
            stock_list = stock_list[offset:]
            if len(stock_list) > top * 20:
                stock_list = stock_list[:top * 20]
                click.echo(click.style(f'  跳过前 {offset} 条后，限制扫描范围为前 {len(stock_list)} 只股票', fg='yellow'))

            # get_stock_list 返回 DataFrame，转成记录列表以便逐行迭代
            scan_records = stock_list.to_dict('records') if hasattr(stock_list, 'to_dict') else stock_list
            scan_symbols = [(s.get('symbol', '') or s.get('code', '')) for s in scan_records]
            scan_symbols = [s for s in scan_symbols if s]

            # 代码 -> 名称 映射（用于输出展示）
            name_map = {}
            for stock_info in scan_records:
                sym = stock_info.get('symbol', '') or stock_info.get('code', '')
                name_map[sym] = stock_info.get('name', sym)

        if multi:
            # 多策略：运行所有策略并输出汇总信号表
            click.echo(click.style(f'  正在扫描股票（策略: {", ".join(strat_names)}）...', fg='blue'))
            results = _run_scan(scan_symbols, strat_keys, threads, forecast, strat_enhance)
        else:
            # 单策略：运行单个策略，返回信号及风险指标
            sk = strat_keys[0]
            sk_enhance = strat_enhance.get(sk, 0)
            click.echo(click.style(f'  正在扫描股票（策略: {strat_names[0]}）...', fg='blue'))
            results = []
            if len(scan_symbols) > 1 and threads > 1:
                from concurrent.futures import ProcessPoolExecutor, as_completed
                n_workers = min(threads, len(scan_symbols))
                click.echo(click.style(f'  使用 {n_workers} 个进程并行扫描...', fg='blue'))
                tasks = [(s, sk, 100000, forecast, sk_enhance) for s in scan_symbols]
                with ProcessPoolExecutor(max_workers=n_workers) as executor:
                    futures = {executor.submit(_scan_single_worker, task): i for i, task in enumerate(tasks)}
                    for fut in as_completed(futures):
                        data = fut.result()
                        if data is not None:
                            results.append(data)
            else:
                for s in scan_symbols:
                    data = _scan_single(s, sk, 100000, forecast, sk_enhance)
                    if data is not None:
                        results.append(data)

        if not results:
            click.echo(click.style('  未找到符合条件的股票', fg='yellow'))
            return

        if multi:
            # 多策略汇总信号表：每只股票一行，列为策略名，按信号合计降序
            results.sort(key=lambda x: x.get('total', -999), reverse=True)
            top_results = results[:top]
            _print_section(f'多策略汇总信号表 - Top {len(top_results)}')

            headers = ['排名', '代码', '名称', '最新价格'] + strat_cols + ['信号合计']
            table_rows = []
            for i, r in enumerate(top_results, 1):
                lc = r.get('latest_close')
                price_str = f'{lc:.2f}' if lc is not None else 'N/A'
                row = [i, r['symbol'], name_map.get(r['symbol'], r['symbol']), price_str]
                for sk in strat_keys:
                    v = r['signals'].get(sk)
                    if v == 1:
                        cell = '买'
                    elif v == 0.5:
                        cell = '弱买'
                    elif v == -1:
                        cell = '卖'
                    elif v == -0.5:
                        cell = '弱卖'
                    elif v == 0:
                        th = r.get('forecasts', {}).get(sk)
                        if th:
                            parts = []
                            if th.get('buy') is not None:
                                parts.append(f'买≥{th["buy"]:.0f}%')
                            if th.get('sell') is not None:
                                parts.append(f'卖≤{th["sell"]:.0f}%')
                            if parts:
                                cell = '/'.join(parts)
                            else:
                                cell = '观'
                        else:
                            cell = '观'
                    else:
                        cell = 'N/A'
                    row.append(cell)
                row.append(r.get('total'))
                table_rows.append(row)

            _print_table(headers, table_rows)
        else:
            # 单策略：按信号值排序（1=买入 优先，其次 0=观望，最后 -1=卖出），同级按总收益率降序
            results.sort(key=lambda x: (x['signal'] if x['signal'] is not None else -99,
                                        x['total_return'] if x['total_return'] is not None else -999),
                         reverse=True)
            top_results = results[:top]
            _print_section(f'扫描结果 - Top {len(top_results)}')

            headers = ['排名', '代码', '名称', '最新价格', '信号', '总收益率', '夏普比率', '最大回撤', '胜率', '买入/卖出次数']
            table_rows = []
            for i, r in enumerate(top_results, 1):
                lc = r.get('latest_close')
                price_str = f'{lc:.2f}' if lc is not None else 'N/A'
                if r['signal'] == 1:
                    sig_text = '买入'
                elif r['signal'] == 0.5:
                    sig_text = '弱买'
                elif r['signal'] == -1:
                    sig_text = '卖出'
                elif r['signal'] == -0.5:
                    sig_text = '弱卖'
                else:
                    th = r.get('forecast')
                    if th:
                        parts = []
                        if th.get('buy') is not None:
                            parts.append(f'买≥{th["buy"]:.0f}%')
                        if th.get('sell') is not None:
                            parts.append(f'卖≤{th["sell"]:.0f}%')
                        if parts:
                            sig_text = '/'.join(parts)
                        else:
                            sig_text = '观望'
                    else:
                        sig_text = '观望'
                ret_str = f'{r["total_return"] * 100:.2f}%' if r['total_return'] is not None else 'N/A'
                shp_str = f'{r["sharpe"]:.2f}' if r['sharpe'] is not None else 'N/A'
                dd_str = f'{r["max_dd"] * 100:.2f}%' if r['max_dd'] is not None else 'N/A'
                wr_str = f'{r["win_rate"] * 100:.2f}%' if r['win_rate'] is not None else 'N/A'
                tr_str = f"{r.get('buy_count') or 0:.1f}/{r.get('sell_count') or 0:.1f}"
                table_rows.append([i, r['symbol'], name_map.get(r['symbol'], r['symbol']), price_str, sig_text, ret_str, shp_str, dd_str, wr_str, tr_str])

            _print_table(headers, table_rows)

        # 保存筛选出的股票代码到 scan_yyyymmdd.txt（每行一条）
        try:
            txt_path = os.path.join('report', f'scan_{datetime.now().strftime("%Y%m%d")}.txt')
            os.makedirs(os.path.dirname(txt_path), exist_ok=True)
            with open(txt_path, 'w', encoding='utf-8') as f:
                for r in top_results:
                    f.write(r['symbol'] + '\n')
            click.echo(click.style(f'  筛选代码已保存到: {txt_path}', fg='green'))
        except Exception as e:
            click.echo(click.style(f'  保存筛选代码失败: {e}', fg='yellow'))

        # 保存结果到 HTML 报告（top N 表格 + 个股图表）
        try:
            strat_tag = '_'.join(strat_keys)
            charts_by_symbol = _generate_scan_stock_charts(top_results, strat_keys, name_map, prefix_tag=strat_tag, strat_enhance=strat_enhance)
            html_path = _generate_scan_html_report(headers, table_rows, strat_names, strat_cols,
                                                   charts_by_symbol=charts_by_symbol, name_map=name_map,
                                                   report_filename=f'scan_{strat_tag}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html')
            click.echo(click.style(f'  HTML报告已保存到: {html_path}', fg='green'))
        except Exception as e:
            click.echo(click.style(f'  保存HTML失败: {e}', fg='yellow'))

        click.echo()
        click.echo(click.style('  扫描完成!', fg='green', bold=True))
        click.echo()

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


# ============================================================================
# backtest 命令 - 回测指定策略
# ============================================================================


@cli.command('backtest')
@click.option('--symbol', '-s', default=None, help='股票代码（与 -sf 至少指定一个）')
@click.option('--symbol-file', '-sf', default=None, help='从指定文件读取多个股票代码进行批量回测（空格/逗号/换行分隔），与 -s 同时使用时合并')
@click.option('--strategy', '-g', default='macd', help='策略选择 [ma_cross|macd|rsi|bollinger]，多个用逗号分隔（默认macd）；-gN 表示全部启用增强级别N（如 -g1 macd,rsi 即 macd1,rsi1）')
@click.option('--start', '-st', default=None, help='开始日期（默认365天前），格式: YYYYMMDD')
@click.option('--end', '-e', default=None, help='结束日期，格式: YYYYMMDD')
@click.option('--capital', '-c', default=100, type=float, help='初始资金（单位：万元，默认100万元）')
@click.option('--output', '-o', default='./output', help='图表输出目录（默认./output）')
@click.option('--chart', is_flag=True, default=False, help='生成K线图等图形（默认仅输出表格报告）')
def backtest_cmd(symbol, symbol_file, strategy, start, end, capital, output, chart):
    """回测：重点输出开始日期至结束日期之间的买卖点及收益率、夏普比率、回撤等信息

    流程：获取数据 -> 运行策略 -> 回测 -> 展示详细结果 -> 生成图表
    指定 -sf 时对多个股票批量回测，输出表格（每只股票一行，各项指标作为列）。
    -g 支持多个策略（逗号或竖线分隔）。
    """
    try:
        capital = capital * 10000  # -c 单位为万元，内部换算为元
        strat_specs = _split_strategies(strategy)
        strat_keys = []
        strat_enhance = {}
        for spec in strat_specs:
            base_key, enh = _parse_strategy_spec(spec)
            if base_key not in STRATEGY_MAP:
                click.echo(click.style(f'  错误: 未知策略 "{base_key}"，可选: {", ".join(STRATEGY_MAP.keys())}', fg='red'))
                return
            if base_key not in strat_keys:
                strat_keys.append(base_key)
            strat_enhance[base_key] = enh

        start_date, end_date = _parse_dates(start, end)
        strat_names = [STRATEGY_MAP[k][0] for k in strat_keys]

        # 收集股票代码（-s 与 -sf 合并去重）
        symbols = []
        if symbol:
            symbols.extend(s.strip() for s in symbol.split() if s.strip())
        if symbol_file:
            file_symbols = _read_symbols_file(symbol_file)
            symbols.extend(file_symbols)
        symbols = list(dict.fromkeys(symbols))

        if not symbols:
            click.echo(click.style('  错误: 请使用 -s 或 -sf 指定至少一个股票代码', fg='red'))
            return

        # -sf 批量回测：每只股票每策略一行，各项指标作为列
        if symbol_file:
            _print_header('批量回测', f'{len(symbols)} 只股票 - {", ".join(strat_names)}')
            click.echo(click.style(f'  回测区间: {start_date} ~ {end_date}', fg='white'))
            click.echo(click.style(f'  初始资金: {capital:,.0f}', fg='white'))

            fetcher = DataFetcher()
            batch_results = []

            def _pct(v):
                return f'{v * 100:.2f}%' if v is not None else 'N/A'

            def _f(v):
                return f'{v:.2f}' if v is not None else 'N/A'

            with click.progressbar(symbols, label='  回测进度') as bar:
                for sym in bar:
                    try:
                        df = fetcher.get_stock_data(sym, start_date, end_date)
                        if df is None or len(df) == 0:
                            continue
                        df, _, _, _ = _apply_realtime_to_df(df, sym, fetcher, end_date, verbose=False)
                        df = add_all_indicators(df)
                        for sk in strat_keys:
                            result = _run_strategy(sk, df, capital, enhance=strat_enhance.get(sk, 0))
                            batch_results.append({'symbol': sym, 'strategy': STRATEGY_MAP[sk][0], 'risk': result})
                    except Exception:
                        continue

            if not batch_results:
                click.echo(click.style('  未获取到任何可回测的股票', fg='yellow'))
                return

            _print_section('批量回测结果')

            headers = ['代码', '策略', '总收益率', '年化收益率', '最大回撤', '夏普比率', '胜率', '买入/卖出次数', '盈利因子', '结束信号']
            table_rows = []
            for r in batch_results:
                risk = r['risk']
                sig_series = risk.get('signals')
                sig_value = None
                if sig_series is not None and len(sig_series) > 0:
                    sig_value = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])
                sig_text = {1.0: '买入', 0.5: '弱买', -1.0: '卖出', -0.5: '弱卖', 0.0: '观望'}.get(sig_value, 'N/A')
                table_rows.append([
                    r['symbol'],
                    r['strategy'],
                    _pct(risk.get('total_return')),
                    _pct(risk.get('annual_return')),
                    _pct(risk.get('max_drawdown')),
                    _f(risk.get('sharpe_ratio')),
                    _pct(risk.get('win_rate')),
                    _fmt_trade_count(risk),
                    _f(risk.get('profit_factor')),
                    sig_text,
                ])

            _print_table(headers, table_rows)

            click.echo()
            click.echo(click.style('  回测完成!', fg='green', bold=True))
            click.echo()
            return

        # 单只股票详细回测
        _print_header('策略回测', f'{symbol} - {", ".join(strat_names)}')

        click.echo(click.style(f'  回测区间: {start_date} ~ {end_date}', fg='white'))
        click.echo(click.style(f'  初始资金: {capital:,.0f}', fg='white'))

        # 获取数据
        click.echo(click.style('\n  正在获取股票数据...', fg='blue'))
        fetcher = DataFetcher()
        df = fetcher.get_stock_data(symbol, start_date, end_date)

        if df is None or len(df) == 0:
            click.echo(click.style(f'  错误: 未能获取股票 {symbol} 的数据', fg='red'))
            return

        click.echo(click.style(f'  获取到 {len(df)} 条数据记录', fg='green'))

        # 合并当日实时行情
        df, _, _, _ = _apply_realtime_to_df(df, symbol, fetcher, end_date)

        # 计算指标
        click.echo(click.style('  正在计算技术指标...', fg='blue'))
        df = add_all_indicators(df)
        click.echo(click.style(f'  已计算 {len(df.columns)} 项指标', fg='green'))

        # 运行策略（支持多策略）
        if len(strat_keys) > 1:
            # 多策略：输出对比表（每策略一行）
            all_results = {}
            for sk in strat_keys:
                click.echo(click.style(f'  正在运行策略: {STRATEGY_MAP[sk][0]}...', fg='blue'))
                all_results[sk] = _run_strategy(sk, df, capital, enhance=strat_enhance.get(sk, 0))

            _print_section('多策略回测结果')

            def _pct(v):
                return f'{v * 100:.2f}%' if v is not None else 'N/A'

            def _f(v):
                return f'{v:.2f}' if v is not None else 'N/A'

            headers = ['策略', '总收益率', '年化收益率', '最大回撤', '夏普比率', '胜率', '买入/卖出次数', '盈利因子', '结束信号']
            table_rows = []
            for sk in strat_keys:
                risk = all_results[sk]
                sig_series = risk.get('signals')
                sig_value = None
                if sig_series is not None and len(sig_series) > 0:
                    sig_value = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])
                sig_text = {1.0: '买入', 0.5: '弱买', -1.0: '卖出', -0.5: '弱卖', 0.0: '观望'}.get(sig_value, 'N/A')
                table_rows.append([
                    STRATEGY_MAP[sk][0],
                    _pct(risk.get('total_return')),
                    _pct(risk.get('annual_return')),
                    _pct(risk.get('max_drawdown')),
                    _f(risk.get('sharpe_ratio')),
                    _pct(risk.get('win_rate')),
                    _fmt_trade_count(risk),
                    _f(risk.get('profit_factor')),
                    sig_text,
                ])
            _print_table(headers, table_rows)

            click.echo()
            click.echo(click.style('  回测完成!', fg='green', bold=True))
            click.echo()
            return

        # 单策略详细回测
        strategy_key = strat_keys[0]
        strategy_name = STRATEGY_MAP[strategy_key][0]
        click.echo(click.style(f'  正在运行策略: {strategy_name}...', fg='blue'))
        date_tag = _build_date_tag(start_date, end_date)
        chart_gen = ChartGenerator(output_dir=output, date_tag=date_tag)
        result, chart_paths, sname, sig_series = _run_single_analysis(df, strategy_key, capital, chart_gen,
                                                                      enhance=strat_enhance.get(strategy_key, 0))
        # 为 backtest 命令单独生成信号图表（无其他策略背景）
        if chart:
            try:
                bt_chart_gen = ChartGenerator(output_dir=output, prefix=f'{symbol}_{strategy_key}', date_tag=date_tag)
                chart_paths['signals'] = bt_chart_gen.plot_signal_on_price(
                    df, sig_series, title=f'{symbol} {strategy_name} - 买卖信号'
                )
            except Exception:
                chart_paths['signals'] = ''
        risk = result  # 结果已包含风险指标

        # 输出详细回测结果
        _print_section('回测结果')

        headers = ['指标', '数值']
        rows = _format_risk_report_rows(risk)

        # 补充更多指标
        rows.append(['年化波动率', f'{risk.get("annual_volatility", 0) * 100:.2f}%' if risk.get('annual_volatility') is not None else 'N/A'])
        rows.append(['卡玛比率', f'{risk.get("calmar_ratio", 0):.2f}' if risk.get('calmar_ratio') is not None else 'N/A'])
        rows.append(['平均持仓天数', f'{risk.get("avg_holding_days", 0):.1f}' if risk.get('avg_holding_days') is not None else 'N/A'])

        _print_table(headers, rows)

        # 图表路径
        if chart_paths:
            click.echo(click.style(f'\n    生成的图表:', fg='white'))
            for chart_name, chart_path in chart_paths.items():
                click.echo(click.style(f'      - {chart_name}: {chart_path}', fg='green'))

        click.echo()
        click.echo(click.style('  回测完成!', fg='green', bold=True))
        click.echo()

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


# ============================================================================
# compare 命令 - 策略对比
# ============================================================================


@cli.command('compare')
@click.option('--symbol', '-s', required=True, help='股票代码（必需）')
@click.option('--start', '-st', default=None, help='开始日期（默认365天前），格式: YYYYMMDD')
@click.option('--end', '-e', default=None, help='结束日期，格式: YYYYMMDD')
@click.option('--capital', '-c', default=100, type=float, help='初始资金（单位：万元，默认100万元）')
@click.option('--chart', is_flag=True, default=False, help='生成策略对比图（默认仅输出表格报告）')
def compare_cmd(symbol, start, end, capital, chart):
    """策略对比：重点输出策略的对比结果

    流程：同时运行所有策略 -> 对比回测结果 -> 生成对比图表 -> 输出排名
    """
    try:
        capital = capital * 10000  # -c 单位为万元，内部换算为元
        start_date, end_date = _parse_dates(start, end)

        _print_header('策略对比分析', symbol)

        click.echo(click.style(f'  分析区间: {start_date} ~ {end_date}', fg='white'))
        click.echo(click.style(f'  初始资金: {capital:,.0f}', fg='white'))

        # 获取数据
        click.echo(click.style('\n  正在获取股票数据...', fg='blue'))
        fetcher = DataFetcher()
        df = fetcher.get_stock_data(symbol, start_date, end_date)

        if df is None or len(df) == 0:
            click.echo(click.style(f'  错误: 未能获取股票 {symbol} 的数据', fg='red'))
            return

        click.echo(click.style(f'  获取到 {len(df)} 条数据记录', fg='green'))

        # 合并当日实时行情
        df, _, _, _ = _apply_realtime_to_df(df, symbol, fetcher, end_date)

        # 计算指标
        click.echo(click.style('  正在计算技术指标...', fg='blue'))
        df = add_all_indicators(df)
        click.echo(click.style(f'  已计算 {len(df.columns)} 项指标', fg='green'))

        # 运行所有策略
        all_results = {}
        all_risks = {}

        for sk in ALL_STRATEGIES:
            click.echo(click.style(f'  正在运行策略: {STRATEGY_MAP[sk][0]}...', fg='blue'))
            result = _run_strategy(sk, df, capital)
            risk = result  # 结果已包含风险指标
            all_results[sk] = result
            all_risks[sk] = risk

        # 输出对比表格
        _print_section('策略对比结果')

        headers = ['策略', '结束信号', '总收益率', '年化收益率', '最大回撤', '夏普比率', '胜率', '买入/卖出次数', '盈利因子']
        table_rows = []
        for sk in ALL_STRATEGIES:
            risk = all_risks[sk]
            name = STRATEGY_MAP[sk][0]

            # 结束日期信号值（1=强买, 0.5=弱买, 0=观望, -0.5=弱卖, -1=强卖）
            sig_series = all_results[sk].get('signals')
            sig_value = None
            if sig_series is not None and len(sig_series) > 0:
                sig_value = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])
            sig_text = {1.0: '买入', 0.5: '弱买', -1.0: '卖出', -0.5: '弱卖', 0.0: '观望'}.get(sig_value, 'N/A')

            def _pct(v):
                return f'{v * 100:.2f}%' if v is not None else 'N/A'

            def _f(v):
                return f'{v:.2f}' if v is not None else 'N/A'

            table_rows.append([
                name,
                sig_text,
                _pct(risk.get('total_return')),
                _pct(risk.get('annual_return')),
                _pct(risk.get('max_drawdown')),
                _f(risk.get('sharpe_ratio')),
                _pct(risk.get('win_rate')),
                _fmt_trade_count(risk),
                _f(risk.get('profit_factor')),
            ])

        _print_table(headers, table_rows)

        # 排名
        _print_section('综合排名（按夏普比率）')

        def _end_signal(sk):
            sig_series = all_results[sk].get('signals')
            if sig_series is not None and len(sig_series) > 0:
                v = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])
                return v
            return None

        rankings = [(STRATEGY_MAP[sk][0], all_risks[sk].get('sharpe_ratio'),
                     all_risks[sk].get('total_return'),
                     all_risks[sk].get('max_drawdown'),
                     all_risks[sk].get('buy_count'),
                     all_risks[sk].get('sell_count'),
                     _end_signal(sk)) for sk in ALL_STRATEGIES]
        rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

        headers = ['排名', '策略', '夏普比率', '总收益率', '最大回撤', '买入/卖出次数', '结束信号']
        rank_rows = []
        for i, (name, sharpe, total_ret, drawdown, buy_c, sell_c, sig) in enumerate(rankings, 1):
            shp_str = f'{sharpe:.2f}' if sharpe is not None else 'N/A'
            ret_str = f'{total_ret * 100:.2f}%' if total_ret is not None else 'N/A'
            dd_str = f'{drawdown * 100:.2f}%' if drawdown is not None else 'N/A'
            tr_str = f'{buy_c or 0:.1f}/{sell_c or 0:.1f}'
            sig_str = {1.0: '买入', 0.5: '弱买', -1.0: '卖出', -0.5: '弱卖', 0.0: '观望'}.get(sig, 'N/A')
            rank_rows.append([i, name, shp_str, ret_str, dd_str, tr_str, sig_str])
        _print_table(headers, rank_rows)

        # 生成对比图表
        if chart:
            click.echo(click.style('\n  正在生成策略对比图表...', fg='blue'))
            chart_gen = ChartGenerator(output_dir='./output', date_tag=_build_date_tag(start_date, end_date))
            try:
                compare_data = {STRATEGY_MAP[sk][0]: all_results[sk] for sk in ALL_STRATEGIES}
                compare_path = chart_gen.plot_compare_strategies(compare_data)
                click.echo(click.style(f'    策略对比图: {compare_path}', fg='green'))
            except Exception as e:
                click.echo(click.style(f'    对比图生成失败: {e}', fg='yellow'))

        click.echo()
        click.echo(click.style('  对比分析完成!', fg='green', bold=True))
        click.echo()

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


# ============================================================================
# indicators 命令 - 计算技术指标
# ============================================================================


@cli.command('indicators')
@click.option('--symbol', '-s', required=True, help='股票代码（必需）')
@click.option('--start', '-st', default=None, help='开始日期（默认365天前），格式: YYYYMMDD')
@click.option('--end', '-e', default=None, help='结束日期，格式: YYYYMMDD')
@click.option('--output', '-o', default=None, help='输出CSV路径（可选，不指定则打印到屏幕）')
def indicators_cmd(symbol, start, end, output):
    """计算各个因子：重点输出各因子、指标的值

    流程：获取数据 -> 计算所有指标 -> 保存到CSV或打印
    """
    try:
        start_date, end_date = _parse_dates(start, end)

        _print_header('技术指标计算', symbol)
        click.echo(click.style(f'  计算区间: {start_date} ~ {end_date}', fg='white'))

        # 获取数据
        click.echo(click.style('\n  正在获取股票数据...', fg='blue'))
        fetcher = DataFetcher()
        df = fetcher.get_stock_data(symbol, start_date, end_date)

        if df is None or len(df) == 0:
            click.echo(click.style(f'  错误: 未能获取股票 {symbol} 的数据', fg='red'))
            return

        click.echo(click.style(f'  获取到 {len(df)} 条数据记录', fg='green'))

        # 计算指标
        click.echo(click.style('  正在计算所有技术指标...', fg='blue'))
        df = add_all_indicators(df)

        if df is None or len(df) == 0:
            click.echo(click.style('  错误: 指标计算失败', fg='red'))
            return

        click.echo(click.style(f'  已计算 {len(df.columns)} 项指标', fg='green'))

        # 列出所有指标
        _print_section('指标列表')
        indicator_cols = [c for c in df.columns if c not in ['Open', 'High', 'Low', 'Close', 'Volume', 'date']]
        click.echo(click.style(f'  共 {len(indicator_cols)} 项指标:', fg='white'))
        for i, col in enumerate(indicator_cols):
            click.echo(f'    {i+1:>3d}. {col}')

        # 输出数据
        if output:
            click.echo(click.style(f'\n  正在保存到 {output}...', fg='blue'))
            # 确保输出目录存在
            output_dir = os.path.dirname(output)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
            df.to_csv(output, index=True, encoding='utf-8-sig')
            click.echo(click.style(f'  已保存到: {output}', fg='green'))
            click.echo(click.style(f'  文件大小: {os.path.getsize(output):,} 字节', fg='white'))
        else:
            # 打印尾部数据预览
            _print_section('数据预览（最近10条）')
            click.echo(click.style(df.tail(10).to_string(), fg='white'))

        click.echo()
        click.echo(click.style('  指标计算完成!', fg='green', bold=True))
        click.echo()

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


# ============================================================================
# 主入口
# ============================================================================


if __name__ == '__main__':
    cli()
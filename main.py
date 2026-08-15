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

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入核心模块
from core.data_fetcher import DataFetcher
from core.indicators import add_all_indicators
from core.backtest import BacktestEngine
from core.risk import risk_report
from core.strategy import Strategy
from strategies.ma_cross import MACrossStrategy
from strategies.macd_strategy import MACDStrategy
from strategies.enhanced_macd import EnhancedMACDStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.bollinger_strategy import BollingerStrategy
from strategies.composite_strategy import CompositeStrategy
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
    'ma_cross': ('均线交叉策略', MACrossStrategy),
    'macd': ('MACD策略', MACDStrategy),
    'enhanced_macd': ('Enhanced-MACD策略', EnhancedMACDStrategy),
    'rsi': ('RSI策略', RSIStrategy),
    'bollinger': ('布林带策略', BollingerStrategy),
    'quality_value': ('质量价值融合策略', QualityValueFactorStrategy),
    'composite': ('综合策略', CompositeStrategy),
}

INDEX_STRATEGY_MAP = {
    'momentum': ('动量分层策略', MomentumTieredStrategy),
    'volatility': ('波动率择时策略', VolatilityTimingStrategy),
    'breadth': ('涨跌比确认策略', BreadthConfirmationStrategy),
}

ALL_STRATEGIES = ['ma_cross', 'macd', 'enhanced_macd', 'rsi', 'bollinger', 'quality_value', 'composite']
ALL_INDEX_STRATEGIES = ['momentum', 'volatility', 'breadth']

# ETF/指数基金专用策略（适配低波动、趋势跟随特性）
ETF_STRATEGY_MAP = {
    'ma_cross': ('ETF均线交叉策略', lambda: MACrossStrategy(fast_period=10, slow_period=40, name='ETF MACross')),
    'macd': ('ETF MACD策略', lambda: MACDStrategy(fast=16, slow=32, signal=12, name='ETF MACD')),
    'enhanced_macd': ('ETF Enhanced-MACD策略', lambda: EnhancedMACDStrategy(fast=16, slow=32, signal=12, name='ETF EnhancedMACD')),
    'rsi': ('ETF RSI策略', lambda: RSIStrategy(period=14, oversold=35, overbought=65, name='ETF RSI')),
    'bollinger': ('ETF布林带策略', lambda: BollingerStrategy(period=20, std=2.5, name='ETF Bollinger')),
    'quality_value': ('ETF质量价值融合策略', lambda: QualityValueFactorStrategy(stock_type='auto', name='ETF QualityValue')),
    'composite': ('ETF综合策略', lambda: CompositeStrategy(
        [MACrossStrategy(fast_period=10, slow_period=40),
         MACDStrategy(fast=16, slow=32, signal=12),
         RSIStrategy(period=14, oversold=35, overbought=65),
         BollingerStrategy(period=20, std=2.5)],
        threshold=0.4, name='ETF Composite')),
}
ALL_ETF_STRATEGIES = ['ma_cross', 'macd', 'enhanced_macd', 'rsi', 'bollinger', 'quality_value', 'composite']

# 策略 key 到类的映射（不含 composite，composite 由配置动态构建）
_STRATEGY_CLASS_MAP = {
    'ma_cross': MACrossStrategy,
    'macd': MACDStrategy,
    'enhanced_macd': EnhancedMACDStrategy,
    'rsi': RSIStrategy,
    'bollinger': BollingerStrategy,
    'quality_value': QualityValueFactorStrategy,
    'momentum': MomentumTieredStrategy,
    'volatility': VolatilityTimingStrategy,
    'breadth': BreadthConfirmationStrategy,
}

# 综合策略默认权重（stock-quant.json 不存在时的回退值）
_DEFAULT_COMPOSITE_WEIGHTS = {
    'ma_cross': 0.18,
    'macd': 0.18,
    'rsi': 0.18,
    'bollinger': 0.18,
    'quality_value': 0.16,
    'momentum': 0.04,
    'volatility': 0.04,
    'breadth': 0.04,
}
_DEFAULT_COMPOSITE_THRESHOLD = 0.25

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


def _build_composite_from_config():
    """根据 stock-quant.json 配置构建 CompositeStrategy 实例"""
    config = _load_quant_config()
    composite_cfg = config.get('composite', {})
    weights_dict = composite_cfg.get('strategies', _DEFAULT_COMPOSITE_WEIGHTS)
    threshold = composite_cfg.get('threshold', _DEFAULT_COMPOSITE_THRESHOLD)

    strategies = []
    weights = []
    for key, weight in weights_dict.items():
        cls = _STRATEGY_CLASS_MAP.get(key)
        if cls is not None:
            strategies.append(cls())
            weights.append(weight)

    if not strategies:
        strategies = [MACrossStrategy(), MACDStrategy(), RSIStrategy(), BollingerStrategy()]
        weights = [0.25, 0.25, 0.25, 0.25]

    return CompositeStrategy(strategies, weights=weights, threshold=threshold, name='Composite')


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


def _run_strategy(strategy_key, df, capital=100000, is_index=False):
    """运行单个策略并返回回测结果"""
    strategy_map = INDEX_STRATEGY_MAP if is_index else STRATEGY_MAP
    strategy_name, strategy_class = strategy_map[strategy_key]
    if strategy_key == 'composite':
        strategy = _build_composite_from_config()
    else:
        strategy = strategy_class()
    signals = strategy.generate_signals(df)
    engine = BacktestEngine(initial_capital=capital)
    result = engine.run(df, signals)
    result['strategy_name'] = strategy_name
    result['strategy_key'] = strategy_key

    # 运行风险分析
    risk = risk_report(result['daily_returns'].dropna(), result['equity_curve'])
    # 合并风险指标到结果中
    result.update(risk)

    return result


def _run_single_analysis(df, strategy_key, capital, chart_gen, prefix='', strategy_map=None):
    """运行单个策略的完整分析流程（图表在外部统一生成）"""
    if strategy_map is None:
        strategy_map = STRATEGY_MAP
    strategy_name, strategy_class = strategy_map[strategy_key]

    if strategy_key == 'composite':
        if callable(strategy_class) and not isinstance(strategy_class, type):
            strategy = strategy_class()
        else:
            strategy = _build_composite_from_config()
    else:
        strategy = strategy_class()

    signals = strategy.generate_signals(df)

    engine = BacktestEngine(initial_capital=capital)
    result = engine.run(df, signals)

    risk = risk_report(result['daily_returns'].dropna(), result['equity_curve'])
    result.update(risk)

    return result, {}, strategy_name, signals


def _parse_dates(start, end):
    """解析日期字符串，返回默认值"""
    if end is None:
        end_date = datetime.now().strftime('%Y-%m-%d')
    else:
        end_date = end
    if start is None:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        start_date = (end_dt - timedelta(days=365)).strftime('%Y-%m-%d')
    else:
        start_date = start
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
    rows.append(['总交易次数', str(risk.get('total_trades', 'N/A'))])
    rows.append(['盈利因子', _safe_float(risk.get('profit_factor'))])

    return rows


def _get_signal_text(sig_series):
    """从信号序列中提取最新信号文本"""
    if sig_series is None or len(sig_series) == 0:
        return 'N/A'
    last_sig = sig_series.iloc[-1] if hasattr(sig_series, 'iloc') else sig_series[-1]
    sig_map = {1: '买入', -1: '卖出', 0: '观望'}
    return sig_map.get(int(last_sig), str(last_sig))


def _get_signal_color(sig_text):
    """获取信号对应的颜色"""
    return {'买入': 'green', '卖出': 'red', '观望': 'yellow'}.get(sig_text, 'white')


def _generate_html_report(symbol, sname, start_date, end_date, capital, is_index,
                          strategy_map, strategies_to_run, all_results, all_risks, all_charts,
                          all_signals=None, latest_close=None, latest_date=None,
                          trading_days=None, report_filename=None):
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
            r.get('total_trades'),
            r.get('annual_return'),
            r.get('win_rate'),
            r.get('profit_factor'),
            sig_text,
        ))
    rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

    rank_rows_html = ''
    for i, (name, tr, sh, dd, nt, ar, wr, pf, sig) in enumerate(rankings, 1):
        rank_rows_html += f'''<tr>
            <td>{i}</td><td>{name}</td>
            <td>{_safe_pct_html(tr)}</td><td>{_safe_float_html(sh)}</td>
            <td>{_safe_pct_html(dd)}</td><td>{nt if nt is not None else 'N/A'}</td>
            <td>{_safe_pct_html(ar)}</td><td>{_safe_pct_html(wr)}</td>
            <td>{_safe_float_html(pf)}</td>
            <td style="font-weight:bold;color:{'#27ae60' if sig == '买入' else '#e74c3c' if sig == '卖出' else '#f39c12'}">{sig}</td>
        </tr>'''

    # 各策略详情
    detail_html = ''

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
            ('总交易次数', lambda: str(risk.get('total_trades', 'N/A'))),
            ('盈利因子', lambda: _safe_float_html(risk.get('profit_factor'))),
        ]:
            detail_html += f'<tr><td>{label}</td><td>{func()}</td></tr>'
        detail_html += '</table>'

        for cname, cpath in charts.items():
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

<h2>策略对比排名</h2>
<table class="rank">
<tr><th>排名</th><th>策略</th><th>总收益率</th><th>夏普比率</th><th>最大回撤</th><th>交易次数</th><th>年化收益率</th><th>胜率</th><th>盈利因子</th><th>最新信号</th></tr>
{rank_rows_html}
</table>

<h2>各策略详情</h2>
{detail_html}

<div class="footer"><p>报告由 stock-quant 自动生成 | {report_time}</p></div>
</body>
</html>'''

    date_tag = _build_date_tag(start_date, end_date)
    report_path = report_filename if report_filename else os.path.join('report', f'{symbol}_{date_tag}.html')
    if not os.path.dirname(report_path):
        report_path = os.path.join('report', report_path)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


def _generate_multi_html_report(all_stock_data, strategy, is_index, capital, report_filename=None, end_date=None, start_date=None):
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

    # 汇总表：每只股票各策略的排名（取每只股票的最佳策略）
    summary_rows = ''
    for d in all_stock_data:
        s = d['symbol']
        sn = d['stock_name']
        lc = d.get('latest_close')
        ldt = d.get('latest_date')
        price_str = f'{lc:.2f}' if lc is not None else 'N/A'
        label = f'{s} {sn}'.strip()
        smap = d['strategy_map']
        risks = d.get('all_risks', {})
        sigs = d.get('all_signals', {})

        # 找最佳策略
        best_sk = None
        best_ret = -999
        for sk in smap:
            r = risks.get(sk, {}).get('total_return')
            if r is not None and (r or 0) > best_ret:
                best_ret = r or 0
                best_sk = sk

        # 统计所有策略的买卖信号数量
        buy_count = 0
        hold_count = 0
        sell_count = 0
        for sk in d.get('strategies_to_run', []):
            sig = sigs.get(sk)
            if sig is not None and len(sig) > 0:
                last = int(sig.iloc[-1])
                if last == 1:
                    buy_count += 1
                elif last == -1:
                    sell_count += 1
                else:
                    hold_count += 1

        # 最新信号栏加粗基准：优先综合策略，其次最佳策略
        bold_sk = 'composite' if 'composite' in sigs else best_sk
        bold_sig_val = None
        if bold_sk and bold_sk in sigs:
            sig_series = sigs[bold_sk]
            if sig_series is not None and len(sig_series) > 0:
                bold_sig_val = int(sig_series.iloc[-1])

        # 构建信号数量显示，综合策略对应的数字加粗
        bs = {1: 0, -1: 1, 0: 2}.get(bold_sig_val, -1)
        parts = []
        for idx, (cnt, _) in enumerate([(buy_count, '买'), (sell_count, '卖'), (hold_count, '观')]):
            if idx == bs:
                parts.append(f'<b>{cnt}</b>')
            else:
                parts.append(str(cnt))
        sig_display = f'{parts[0]}/{parts[2]}/{parts[1]}'

        if best_sk:
            r = risks[best_sk] if best_sk in risks else {}
            summary_rows += f'''<tr>
                <td>{label}</td><td>{price_str}</td><td>{smap[best_sk]}</td>
                <td>{_safe_pct(r.get('total_return'))}</td>
                <td>{_safe_float(r.get('sharpe_ratio'))}</td>
                <td>{_safe_pct(r.get('max_drawdown'))}</td>
                <td>{r.get('total_trades', 'N/A')}</td>
                <td>{sig_display}</td>
            </tr>'''

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
        for sk in d['strategies_to_run']:
            r = risks.get(sk, {})
            rankings.append((
                smap[sk], r.get('total_return'), r.get('sharpe_ratio'),
                r.get('max_drawdown'), r.get('total_trades'),
                _get_signal_text(sigs.get(sk)),
            ))
        rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

        rank_rows = ''
        for i, (name, tr, sh, dd, nt, sg) in enumerate(rankings, 1):
            rank_rows += f'''<tr>
                <td>{i}</td><td>{name}</td>
                <td>{_safe_pct(tr)}</td><td>{_safe_float(sh)}</td>
                <td>{_safe_pct(dd)}</td><td>{nt if nt is not None else 'N/A'}</td>
                <td style="font-weight:bold;color:{'#27ae60' if sg == '买入' else '#e74c3c' if sg == '卖出' else '#f39c12'}">{sg}</td>
            </tr>'''
        detail_sections += f'''<table>
            <tr><th>排名</th><th>策略</th><th>总收益率</th><th>夏普比率</th><th>最大回撤</th><th>交易次数</th><th>最新信号</th></tr>
            {rank_rows}
        </table>'''

        # 策略权益曲线对比图
        # 嵌入各策略图表
        for sk in d['strategies_to_run']:
            ch = charts.get(sk, {})
            for cname, cpath in ch.items():
                b64 = _img_to_b64(cpath)
                if b64:
                    ext = os.path.splitext(cpath)[1].lstrip('.')
                    detail_sections += f'<div class="chart"><img src="data:image/{ext};base64,{b64}" alt="{cname}"></div>'

    # 计算各策略收益率排名
    from collections import defaultdict
    strategy_all_returns = defaultdict(list)
    strategy_all_drawdowns = defaultdict(list)
    strategy_all_trades = defaultdict(list)
    for d in all_stock_data:
        smap = d['strategy_map']
        risks = d.get('all_risks', {})
        for sk in d.get('strategies_to_run', []):
            r = risks.get(sk, {})
            name = smap[sk]
            tr = r.get('total_return')
            dd = r.get('max_drawdown')
            tt = r.get('total_trades')
            if tr is not None:
                strategy_all_returns[name].append(tr)
            if dd is not None:
                strategy_all_drawdowns[name].append(dd)
            if tt is not None:
                strategy_all_trades[name].append(tt)

    strategy_avg = []
    for name, rets in strategy_all_returns.items():
        avg = sum(rets) / len(rets)
        wr = sum(1 for r in rets if r > 0) / len(rets) * 100
        dds = strategy_all_drawdowns.get(name, [])
        avg_dd = sum(dds) / len(dds) if dds else None
        trades = strategy_all_trades.get(name, [])
        avg_trades = sum(trades) / len(trades) if trades else None
        strategy_avg.append((name, avg, wr, max(rets), min(rets), avg_dd, avg_trades))
    strategy_avg.sort(key=lambda x: x[1], reverse=True)

    strategy_rank_html = '<table><tr><th>排名</th><th>策略</th><th>平均收益率</th><th>正收益占比</th><th>最高</th><th>最低</th><th>最大回撤</th><th>平均交易次数</th></tr>'
    for i, (name, avg, wr, mx, mn, avg_dd, avg_trades) in enumerate(strategy_avg, 1):
        trades_str = f'{avg_trades:.1f}' if avg_trades is not None else 'N/A'
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

<h2>汇总排名（各股票最佳策略）</h2>
<table>
<tr><th>股票</th><th>最新价格</th><th>最佳策略</th><th>总收益率</th><th>夏普比率</th><th>最大回撤</th><th>交易次数</th><th>最新信号(买/观/卖)</th></tr>
{summary_rows}
</table>

<h2>各策略收益率排名</h2>
{strategy_rank_html}
</table>

{detail_sections}

<div class="footer"><p>报告由 stock-quant 自动生成 | {report_time}</p></div>
</body>
</html>'''

    date_tag = _build_date_tag(start_date, end_date)
    report_path = report_filename if report_filename else os.path.join('report', f'report_{date_tag}.html')
    if not os.path.dirname(report_path):
        report_path = os.path.join('report', report_path)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


# ============================================================================
# CLI 命令组
# ============================================================================


@click.group()
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


@cli.command('analyze')
@click.option('--symbol', '-s', default=None, help='股票代码或名称，可多个，以空格分隔（如 "000725 京东方A 000021"）')
@click.option('--symbol-file', '-sf', default=None, help='从指定文件读取自选股列表（JSON数组），与 -s 同时使用时合并')
@click.option('--hot', '-h', default=None, type=int, help='获取热门股票数量（按HotScore热度分排序，存在时忽略 -s 和 -sf）')
@click.option('--start', '-st', default=None, help='开始日期（默认1年前），格式: YYYY-MM-DD')
@click.option('--end', '-e', default=None, help='结束日期（默认今天），格式: YYYY-MM-DD')
@click.option('--strategy', '-g', default='all', help='策略选择 [ma_cross|macd|enhanced_macd|rsi|bollinger|quality_value|composite|all]，多个以|分隔')
@click.option('--index', '-i', 'is_index', is_flag=True, default=False, help='使用指数专属策略模式（动量分层/波动率择时/涨跌比确认）')
@click.option('--capital', '-c', default=100000, type=float, help='初始资金（默认100000）')
@click.option('--output', '-o', default='./output', help='图表输出目录（默认./output）')
@click.option('--output-file', '-of', default=None, help='指定HTML报告文件名，默认自动生成')
@click.option('--threads', '-t', default=1, type=int, help='并行进程数（默认1，多股票时可设为4等以加速）')
@click.option('--db', '-db', default=1, type=int, help='数据库缓存模式 [0=不读不写|1=只读缓存不写(默认)|2=不读缓存走网络覆盖写]')
@click.option('--mode', '-m', default=0, type=int, help='分析模式 [0=常规(默认)|1=资金利用最大化轮动选股|2=多持仓资金利用最大化|3=多持仓强化(买卖信号与选股优化)]')
def analyze_cmd(symbol, symbol_file, hot, start, end, strategy, is_index, capital, output, output_file, threads, db, mode):
    """单只/批量股票综合分析

    流程：获取数据 -> 计算指标 -> 运行策略 -> 回测 -> 风险分析 -> 生成图表 -> 打印报告

    使用 --index/-i 参数可切换到指数专属策略模式，适用于分析大盘指数。
    -s 支持多个股票代码或名称，以空格分隔；多个股票时输出报告名称为 multi_日期.html。
    -s 和 -sf 可同时使用，股票列表会自动合并去重。
    都不指定时从 stock-quant.json 读取自选股。
    使用 -h 指定数量时，忽略 -s/-sf，从网络获取最热门股票。
    """
    # 设置数据库缓存模式
    from core.db import set_db_mode
    set_db_mode(db)
    if db == 0:
        click.echo(click.style('  数据库缓存: 不读不写（纯网络）', fg='yellow'))
    elif db == 1:
        click.echo(click.style('  数据库缓存: 只读缓存，不写数据库', fg='yellow'))
    elif db == 2:
        click.echo(click.style('  数据库缓存: 不读缓存，走网络获取并覆盖写库', fg='yellow'))

    # 确定要分析的股票列表
    time_start = datetime.now()
    def _read_symbols_file(path):
        if not os.path.exists(path):
            click.echo(click.style(f'  错误: 文件不存在 ({path})', fg='red'))
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(s).strip() for s in data if str(s).strip()]
            click.echo(click.style(f'  错误: {path} 内容不是 JSON 数组', fg='red'))
            return []
        except Exception as e:
            click.echo(click.style(f'  错误: 无法读取 {path}: {e}', fg='red'))
            return []

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
                # 最多查询 100 只
                if len(expanded) > 100:
                    click.echo(click.style(f'  通配符匹配 {len(expanded)} 只，超过 100 只上限，仅取前 100 只', fg='yellow'))
                    expanded = expanded[:100]
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

    # 模式 1：资金利用最大化轮动选股；模式 2：多持仓资金利用最大化；模式 3：多持仓强化
    if mode == 1:
        _run_rotation_analysis(symbols, start, end, strategy, capital, output, output_file)
        return
    if mode == 2:
        _run_portfolio_analysis(symbols, start, end, strategy, capital, output, output_file)
        return
    if mode == 3:
        _run_portfolio_analysis_v3(symbols, start, end, strategy, capital, output, output_file)
        return

    # 确定输出文件名（日期部分取结束日期）
    start_date_str, end_date_str = _parse_dates(start, end)
    date_tag = _build_date_tag(start_date_str, end_date_str)
    if not output_file:
        if hot:
            output_file = f'hot{hot}_{date_tag}.html'
        elif s_multi:
            output_file = f'multi_{date_tag}.html'
        elif symbol and symbol_file:
            sf_basename = os.path.splitext(os.path.basename(symbol_file))[0]
            output_file = f'{symbols[0]}_{sf_basename}_{date_tag}.html'
        elif symbol:
            output_file = f'{symbols[0]}_{date_tag}.html'
        elif symbol_file:
            sf_basename = os.path.splitext(os.path.basename(symbol_file))[0]
            output_file = f'{sf_basename}_{date_tag}.html'
        else:
            output_file = f'report_{date_tag}.html'

    all_stock_data = []
    if len(symbols) > 1 and threads > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        n_workers = min(threads, len(symbols))
        click.echo(click.style(f'\n  使用 {n_workers} 个进程并行分析...', fg='blue'))

        tasks = [(s, start, end, strategy, is_index, capital, output, output_file, True) for s in symbols]
        results = [None] * len(symbols)
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_analyze_single_worker, task): i for i, task in enumerate(tasks)}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    _, data, out = fut.result()
                except Exception as e:
                    data, out = None, f'  异常: {e}'
                results[idx] = data
                if data is None:
                    click.echo(click.style(f'\n  [{idx+1}/{len(symbols)}] {symbols[idx]} 分析失败', fg='red'))
                    if out.strip():
                        click.echo(out)
                else:
                    click.echo(click.style(f'  [{idx+1}/{len(symbols)}] {symbols[idx]} 完成', fg='cyan'))
        all_stock_data = [d for d in results if d is not None]
    else:
        for idx, raw_symbol in enumerate(symbols):
            if len(symbols) > 1:
                click.echo(click.style(f'\n  ── [{idx+1}/{len(symbols)}] ──', fg='cyan', bold=True))
            data = _analyze_single(raw_symbol, start, end, strategy, is_index, capital, output, output_file,
                                   batch_mode=len(symbols) > 1)
            if data is not None:
                all_stock_data.append(data)

    if len(symbols) > 1:
        click.echo(click.style(f'\n  批量分析完成，共 {len(symbols)} 只股票', fg='green', bold=True))

    # 生成汇总 HTML 报告
    if all_stock_data:
        try:
            html_path = _generate_multi_html_report(all_stock_data, strategy, is_index, capital,
                                                    report_filename=output_file, end_date=end_date_str,
                                                    start_date=start_date_str)
            click.echo(click.style(f'\n  汇总报告: {html_path}', fg='green'))
        except Exception as e:
            click.echo(click.style(f'\n  汇总报告生成失败: {e}', fg='yellow'))

    elapsed = (datetime.now() - time_start).total_seconds()
    click.echo(click.style(f'\n  总运行时间: {elapsed:.1f} 秒', fg='cyan', bold=True))


def _analyze_single(raw_symbol, start, end, strategy, is_index, capital, output, output_file=None, batch_mode=False):
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
        def _cache_dividend():
            try:
                fetcher.get_dividend_data(symbol)
            except Exception:
                pass
        try:
            import threading
            t = threading.Thread(target=_cache_dividend, daemon=True)
            t.start()
        except Exception:
            pass

        # 最新价格：请求终点为今日时尝试实时价格，否则取上一交易日收盘价
        latest_close = df['close'].iloc[-1]
        latest_date = df.index[-1]
        is_realtime = False
        is_today = (end_date == datetime.now().strftime('%Y-%m-%d'))
        if is_today:
            try:
                rt = fetcher.get_realtime_quote(symbol)
                if rt and rt.get('price') and rt['price'] > 0:
                    latest_close = rt['price']
                    latest_date = datetime.now()
                    is_realtime = True

                    today_idx = pd.to_datetime(latest_date)
                    if today_idx not in df.index:
                        rt_row = pd.DataFrame({
                            'open':  [rt.get('open', latest_close)],
                            'high':  [rt.get('high', latest_close)],
                            'low':   [rt.get('low', latest_close)],
                            'close': [latest_close],
                            'volume': [rt.get('volume', 0)],
                        }, index=[today_idx])
                        for col in rt_row.columns:
                            rt_row[col] = rt_row[col].astype(df[col].dtype)
                        df = pd.concat([df, rt_row])
                        df = df.sort_index()

                    click.echo(click.style(f'  实时价格: {latest_close:.2f} (已纳入分析)', fg='yellow'))
            except Exception:
                pass
        if not is_realtime:
            click.echo(click.style(f'  最新价格: {latest_close:.2f} ({latest_date.date()})', fg='green'))

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
                    'date', 'MA5', 'MA10', 'MA20', 'MA60',
                    'EMA12', 'EMA26', 'MACD_DIF', 'MACD_DEA', 'MACD_BAR',
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
        else:
            strategies_to_run = []
            for s in strategy.split('|'):
                s = s.strip()
                if s not in strategy_map:
                    avail = ', '.join(strategy_map.keys())
                    click.echo(click.style(f'  错误: 未知策略 "{s}"，可选: {avail}, all', fg='red'))
                    return
                if s not in strategies_to_run:
                    strategies_to_run.append(s)

        # 报告展示用策略列表：-g all 时隐藏质量价值融合策略的独立报告，
        # 但其仍参与内部计算（综合策略需要用到）
        if strategy == 'all' and 'quality_value' in strategies_to_run:
            strategies_to_report = [sk for sk in strategies_to_run if sk != 'quality_value']
        else:
            strategies_to_report = strategies_to_run

        # 运行策略
        all_results = {}
        all_risks = {}
        all_charts = {}
        all_signals = {}

        for sk in strategies_to_run:
            click.echo(click.style(f'  正在运行策略: {strategy_map[sk][0]}...', fg='blue'))
            result, chart_paths, sname, signals = _run_single_analysis(
                df, sk, capital, chart_gen, prefix=stock_label, strategy_map=strategy_map
            )
            all_results[sk] = result
            all_risks[sk] = result
            all_charts[sk] = chart_paths
            all_signals[sk] = signals

        # 生成信号图表
        for sk in strategies_to_run:
            signals = all_signals.get(sk)
            if signals is None:
                continue
            sk_chart_gen = ChartGenerator(output_dir=chart_gen.output_dir, prefix=f'{symbol}_{sk}', date_tag=date_tag)
            strategy_name = strategy_map[sk][0]
            equity_curve = all_risks[sk].get('equity_curve') if all_risks.get(sk) else None

            try:
                signal_path = sk_chart_gen.plot_signal_composite(
                    df, signals, strategy_key=sk,
                    title=f'{strategy_name}-回测效果 ({symbol} {stock_name})'.replace('  ', ' '),
                    equity_curve=equity_curve,
                )
                all_charts[sk]['signals'] = signal_path
            except Exception:
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
            click.echo(click.style(f'    最新信号: ', fg='white', bold=True) +
                       click.style(sig_text, fg=sig_color, bold=True))

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
                rank_trades = all_risks[sk].get('total_trades')
                rank_signal = _get_signal_text(all_signals.get(sk))
                rankings.append((strategy_map[sk][0], rank_total_return, rank_sharpe,
                                 rank_drawdown, rank_trades, rank_signal))
            rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

            headers = ['排名', '策略', '总收益率', '夏普比率', '最大回撤', '交易次数', '最新信号']
            table_rows = []
            for i, (name, total_ret, sharpe, drawdown, trades, signal) in enumerate(rankings, 1):
                ret_str = f'{total_ret * 100:.2f}%' if total_ret is not None else 'N/A'
                shp_str = f'{sharpe:.2f}' if sharpe is not None else 'N/A'
                dd_str = f'{drawdown * 100:.2f}%' if drawdown is not None else 'N/A'
                tr_str = str(int(trades)) if trades is not None else 'N/A'
                table_rows.append([i, name, ret_str, shp_str, dd_str, tr_str, signal])
            _print_table(headers, table_rows)

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
                    report_filename=output_file
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
        }

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


def _analyze_single_worker(args):
    """多进程 worker：分析单只股票，捕获其控制台输出，返回 (raw_symbol, data, 输出文本)"""
    raw_symbol, start, end, strategy, is_index, capital, output, output_file, batch_mode = args
    buf = io.StringIO()
    data = None
    try:
        with contextlib.redirect_stdout(buf):
            data = _analyze_single(raw_symbol, start, end, strategy, is_index, capital,
                                   output, output_file, batch_mode=batch_mode)
    except Exception as e:
        buf.write(f'\n  异常: {e}\n')
    return raw_symbol, data, buf.getvalue()


def _run_rotation_analysis(symbols, start, end, strategy, capital, output, output_file):
    """模式 1：资金利用最大化轮动选股

    每天先检查所持股票是否有卖出信号，有则全部卖出；再遍历股票池，
    若某股票出现买入信号则全额买入。分析周期内持仓股票可能变化，
    报告列出各持仓时间段及所购股票。
    """
    if len(symbols) < 2:
        click.echo(click.style('  错误: 轮动模式至少需要 2 只股票', fg='red'))
        return

    start_date, end_date = _parse_dates(start, end)
    date_tag = _build_date_tag(start_date, end_date)

    # 确定轮动所用策略
    if strategy == 'all':
        strategy_key = 'composite'
    else:
        strategy_key = strategy.split('|')[0].strip()
    if strategy_key == 'composite':
        strat = _build_composite_from_config()
        strategy_name = '综合策略'
    elif strategy_key in STRATEGY_MAP:
        strat = STRATEGY_MAP[strategy_key][1]()
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

    if len(stock_data) < 2:
        click.echo(click.style('  错误: 有效股票数据不足 2 只', fg='red'))
        return

    # 日期对齐（取并集）
    all_dates = sorted(set().union(*[set(df.index) for df, _ in stock_data.values()]))

    # 轮动回测
    cash = float(capital)
    holding = None
    shares = 0.0
    entry_price = 0.0
    entry_date = None
    periods = []  # (start_date, end_date, symbol, entry_price, exit_price)
    trade_count = 0

    for date in all_dates:
        # 1. 卖出检查：所持股票出现卖出信号则全部卖出
        if holding is not None:
            df, sig = stock_data[holding]
            if date in df.index:
                s = int(sig.loc[date])
                price = float(df['close'].loc[date])
                if s == -1:
                    cash += shares * price
                    periods.append((entry_date, date, holding, entry_price, price))
                    trade_count += 1
                    holding = None
                    shares = 0.0
        # 2. 买入检查：股票池中某股票出现买入信号则全额买入
        if holding is None:
            for symbol in symbols:
                if symbol not in stock_data:
                    continue
                df, sig = stock_data[symbol]
                if date in df.index:
                    s = int(sig.loc[date])
                    price = float(df['close'].loc[date])
                    if s == 1:
                        shares = cash / price
                        cash = 0.0
                        holding = symbol
                        entry_price = price
                        entry_date = date
                        trade_count += 1
                        break

    # 期末平仓
    if holding is not None:
        df, _ = stock_data[holding]
        last_price = float(df['close'].iloc[-1])
        cash += shares * last_price
        periods.append((entry_date, df.index[-1], holding, entry_price, last_price))
        holding = None

    final_value = cash
    total_return = (final_value - capital) / capital if capital else 0.0

    # 解析持仓股票名称
    name_map = {}
    held_symbols = set(p[2] for p in periods)
    for s in held_symbols:
        code, name = _resolve_symbol(s)
        name_map[s] = name if name else s

    # 打印结果
    click.echo()
    _print_section('轮动选股结果')
    click.echo(click.style(f'  最终资金: {final_value:,.2f}', fg='green', bold=True))
    click.echo(click.style(f'  总收益率: {total_return * 100:.2f}%', fg='green' if total_return >= 0 else 'red', bold=True))
    click.echo(click.style(f'  交易次数: {trade_count}', fg='white'))

    headers = ['序号', '开始日期', '结束日期', '股票代码', '股票名称', '买入价', '卖出价', '区间收益率', '持有天数']
    table_rows = []
    for i, (sd, ed, sym, ep, xp) in enumerate(periods, 1):
        ret = (xp - ep) / ep if ep else 0.0
        days = (pd.Timestamp(ed) - pd.Timestamp(sd)).days
        table_rows.append([i, str(sd.date()), str(ed.date()), sym, name_map.get(sym, sym),
                           f'{ep:.2f}', f'{xp:.2f}', f'{ret * 100:.2f}%', days])
    _print_table(headers, table_rows)

    # 生成 HTML 报告
    try:
        html_path = _generate_rotation_html(
            symbols, strategy_name, start_date, end_date, capital, final_value,
            total_return, trade_count, periods, name_map, output_file
        )
        click.echo(click.style(f'\n  轮动报告: {html_path}', fg='green'))
    except Exception as e:
        click.echo(click.style(f'\n  轮动报告生成失败: {e}', fg='yellow'))


def _generate_rotation_html(pool_symbols, strategy_name, start_date, end_date, capital,
                            final_value, total_return, trade_count, periods, name_map, output_file):
    """生成轮动选股 HTML 报告"""
    date_tag = _build_date_tag(start_date, end_date)
    report_path = output_file if output_file else os.path.join('report', f'rotation_{date_tag}.html')
    if not os.path.dirname(report_path):
        report_path = os.path.join('report', report_path)

    rows = ''
    for i, (sd, ed, sym, ep, xp) in enumerate(periods, 1):
        ret = (xp - ep) / ep if ep else 0.0
        days = (pd.Timestamp(ed) - pd.Timestamp(sd)).days
        color = '#27ae60' if ret >= 0 else '#e74c3c'
        rows += f'''<tr>
            <td>{i}</td><td>{sd.date()}</td><td>{ed.date()}</td>
            <td>{sym}</td><td>{name_map.get(sym, sym)}</td>
            <td>{ep:.2f}</td><td>{xp:.2f}</td>
            <td style="color:{color};font-weight:bold">{ret * 100:.2f}%</td>
            <td>{days}</td>
        </tr>'''

    pool_str = '，'.join(str(s) for s in pool_symbols)
    ret_color = '#27ae60' if total_return >= 0 else '#e74c3c'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>轮动选股报告</title>
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
.footer {{ text-align: center; color: #95a5a6; margin-top: 40px; font-size: 12px; }}
</style>
</head>
<body>
<h1>资金利用最大化轮动选股报告</h1>
<div class="info">
    <span><strong>策略:</strong> {strategy_name}</span>
    <span><strong>区间:</strong> {start_date} ~ {end_date}</span>
    <span><strong>初始资金:</strong> {capital:,.0f}</span>
    <span><strong>最终资金:</strong> {final_value:,.0f}</span>
    <span><strong>总收益率:</strong> <span style="color:{ret_color};font-weight:bold">{total_return * 100:.2f}%</span></span>
    <span><strong>交易次数:</strong> {trade_count}</span>
</div>
<div class="info">
    <span><strong>股票池({len(pool_symbols)}只):</strong> {pool_str}</span>
</div>

<h2>持仓时间段</h2>
<table>
<tr><th>序号</th><th>开始日期</th><th>结束日期</th><th>股票代码</th><th>股票名称</th><th>买入价</th><th>卖出价</th><th>区间收益率</th><th>持有天数</th></tr>
{rows}
</table>

<div class="footer"><p>报告由 stock-quant 自动生成</p></div>
</body>
</html>'''

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


def _run_portfolio_analysis(symbols, start, end, strategy, capital, output, output_file):
    """模式 2：多持仓资金利用最大化

    每天先按 symbols 顺序找卖出信号（信号 == -1）的持仓并全部卖出；
    再找第一个买入信号（多个则只取第一个）的股票，剩余资金 > 1/10 总资金时
    用剩余资金买入；不足则从持仓中选出 MACD 柱下降（今日 < 昨日）的一只股票，
    按其资金 < 1/2 总资金全卖、否则卖一半释放资金买入；
    若持仓中无 MACD 柱下降股票，则放弃当日买入。
    支持 1 只及以上股票（1 只作为多只的特例处理）。
    """
    if len(symbols) < 1:
        click.echo(click.style('  错误: 该模式至少需要 1 只股票', fg='red'))
        return

    start_date, end_date = _parse_dates(start, end)

    # 确定策略
    if strategy == 'all':
        strategy_key = 'composite'
    else:
        strategy_key = strategy.split('|')[0].strip()
    if strategy_key == 'composite':
        strat = _build_composite_from_config()
        strategy_name = '综合策略'
    elif strategy_key in STRATEGY_MAP:
        strat = STRATEGY_MAP[strategy_key][1]()
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

    for date in all_dates:
        # 步骤0：找卖出信号 —— 按 symbols 顺序遍历股票池，信号为 -1 的持仓全部卖出
        sold_list = []
        sold_amount = 0.0
        cleared_list = []   # 本日清仓（全部卖出）的股票
        for symbol in symbols:
            if symbol not in stock_data or symbol not in positions:
                continue
            df, sig = stock_data[symbol]
            if date in df.index and int(sig.loc[date]) == -1:
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
            if date in df.index and int(sig.loc[date]) == 1:
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
            # 剩余资金充足：全额买入
            buy_amount = cash
            buy_shares = cash / buy_price
            cash = 0.0
            if buy_symbol in positions:
                p = positions[buy_symbol]
                p['shares'] += buy_shares
                p['avg_price'] = (p['avg_price'] * (p['shares'] - buy_shares) + buy_price * buy_shares) / p['shares']
            else:
                positions[buy_symbol] = {'shares': buy_shares, 'entry_date': date, 'avg_price': buy_price}
            st = trade_stats.setdefault(buy_symbol, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
            st['buy_amount'] += buy_amount
        else:
            # 剩余资金不足：在持仓中找 MACD 柱下降的股票作为卖出对象
            if positions:
                candidates = [s for s in positions if _macd_declining(s, date)]
                if candidates:
                    sell_target = min(candidates, key=lambda s: positions[s]['entry_date'])
                    pos = positions[sell_target]
                    cur_price = _close_price(sell_target, date)
                    pos_value = pos['shares'] * cur_price
                    if pos_value < total * 0.5:
                        sold_shares = pos['shares']
                        cash += sold_shares * cur_price
                        del positions[sell_target]
                        sell_symbol = sell_target
                        sell_amount = sold_shares * cur_price
                        cleared_list.append(sell_target)
                    else:
                        sell_shares = pos['shares'] / 2.0
                        pos['shares'] -= sell_shares
                        cash += sell_shares * cur_price
                        sell_symbol = sell_target
                        sell_amount = sell_shares * cur_price
                    st = trade_stats.setdefault(sell_target, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                    st['sell_amount'] += sell_amount
                    st['last_sell'] = date
            # 只有发生卖出释放资金时才买入；否则放弃当日买入
            if sell_symbol is not None and cash > 0:
                buy_amount = cash
                buy_shares = cash / buy_price
                cash = 0.0
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
            idle_days, period_days, output_file
        )
        click.echo(click.style(f'\n  组合报告: {html_path}', fg='green'))
    except Exception as e:
        click.echo(click.style(f'\n  组合报告生成失败: {e}', fg='yellow'))


def _run_portfolio_analysis_v3(symbols, start, end, strategy, capital, output, output_file):
    """模式 3：多持仓强化优化

    在模式 2（多持仓资金利用最大化）基础上，进一步加强对买卖信号
    与股票选择的优化。当前为模式 2 的基线实现，后续在此迭代增强。
    """
    _run_portfolio_analysis(symbols, start, end, strategy, capital, output, output_file)


def _generate_portfolio_html(pool_symbols, strategy_name, start_date, end_date, capital,
                             final_value, total_return, events, positions, stock_data,
                             name_map, stock_stats, idle_days, period_days, output_file):
    """生成多持仓组合 HTML 报告"""
    date_tag = _build_date_tag(start_date, end_date)
    report_path = output_file if output_file else os.path.join('report', f'portfolio_{date_tag}.html')
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
        buy_str = f'{buy_symbol} {name_map.get(buy_symbol, buy_symbol)}' if buy_symbol else '-'
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
    <span><strong>交易次数:</strong> {len(events)}</span>
    <span><strong>闲置资金天数/分析周期:</strong> {idle_days:.2f} / {period_days}</span>
</div>
<div class="info">
    <span><strong>股票池({len(pool_symbols)}只):</strong> {pool_str}</span>
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

<div class="footer"><p>报告由 stock-quant 自动生成</p></div>
</body>
</html>'''

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


# ============================================================================
# scan 命令 - 股票扫描筛选
# ============================================================================


@cli.command('scan')
@click.option('--strategy', '-g', default='ma_cross', help='策略选择（默认ma_cross）')
@click.option('--top', '-t', default=20, type=int, help='返回前N只股票（默认20）')
@click.option('--min-volume', default=None, type=float, help='最小成交量过滤')
def scan_cmd(strategy, top, min_volume):
    """股票扫描筛选

    流程：获取股票列表 -> 筛选 -> 逐个运行策略 -> 按收益排序 -> 输出结果
    """
    try:
        if strategy not in STRATEGY_MAP:
            click.echo(click.style(f'  错误: 未知策略 "{strategy}"，可选: {", ".join(STRATEGY_MAP.keys())}', fg='red'))
            return

        strategy_name = STRATEGY_MAP[strategy][0]
        _print_header('股票扫描筛选', f'策略: {strategy_name}')

        click.echo(click.style('  正在获取股票列表...', fg='blue'))
        fetcher = DataFetcher()
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

        # 限制扫描数量
        if len(stock_list) > top * 3:
            stock_list = stock_list[:top * 3]
            click.echo(click.style(f'  限制扫描范围为前 {len(stock_list)} 只股票', fg='yellow'))

        # 逐个运行策略
        results = []
        click.echo(click.style(f'  正在扫描股票（策略: {strategy_name}）...', fg='blue'))

        with click.progressbar(stock_list, label='  扫描进度') as bar:
            for stock_info in bar:
                try:
                    symbol = stock_info.get('symbol', '') or stock_info.get('code', '')
                    name = stock_info.get('name', symbol)
                    if not symbol:
                        continue

                    # 获取近一年数据
                    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
                    end_date = datetime.now().strftime('%Y-%m-%d')
                    df = fetcher.get_stock_data(symbol, start_date, end_date)

                    if df is None or len(df) < 100:
                        continue

                    df = add_all_indicators(df)
                    result = _run_strategy(strategy, df, capital=100000)
                    risk = result  # 结果已包含风险指标

                    total_return = risk.get('total_return', None)
                    if total_return is not None:
                        results.append({
                            'symbol': symbol,
                            'name': name,
                            'total_return': total_return,
                            'sharpe': risk.get('sharpe_ratio'),
                            'max_dd': risk.get('max_drawdown'),
                            'win_rate': risk.get('win_rate'),
                            'trades': risk.get('total_trades'),
                        })
                except Exception:
                    continue

        # 按总收益率排序
        results.sort(key=lambda x: x['total_return'] if x['total_return'] is not None else -999, reverse=True)

        if not results:
            click.echo(click.style('  未找到符合条件的股票', fg='yellow'))
            return

        # 输出前N只
        top_results = results[:top]
        _print_section(f'扫描结果 - Top {len(top_results)}')

        headers = ['排名', '代码', '名称', '总收益率', '夏普比率', '最大回撤', '胜率', '交易次数']
        table_rows = []
        for i, r in enumerate(top_results, 1):
            ret_str = f'{r["total_return"] * 100:.2f}%' if r['total_return'] is not None else 'N/A'
            shp_str = f'{r["sharpe"]:.2f}' if r['sharpe'] is not None else 'N/A'
            dd_str = f'{r["max_dd"] * 100:.2f}%' if r['max_dd'] is not None else 'N/A'
            wr_str = f'{r["win_rate"] * 100:.2f}%' if r['win_rate'] is not None else 'N/A'
            tr_str = str(r['trades']) if r['trades'] is not None else 'N/A'
            table_rows.append([i, r['symbol'], r['name'], ret_str, shp_str, dd_str, wr_str, tr_str])

        _print_table(headers, table_rows)

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
@click.option('--symbol', '-s', required=True, help='股票代码（必需）')
@click.option('--strategy', '-g', required=True, help='策略选择（必需）[ma_cross|macd|rsi|bollinger|composite]')
@click.option('--start', '-st', default=None, help='开始日期，格式: YYYY-MM-DD')
@click.option('--end', '-e', default=None, help='结束日期，格式: YYYY-MM-DD')
@click.option('--capital', '-c', default=100000, type=float, help='初始资金（默认100000）')
@click.option('--output', '-o', default='./output', help='图表输出目录（默认./output）')
def backtest_cmd(symbol, strategy, start, end, capital, output):
    """回测指定策略

    流程：获取数据 -> 运行策略 -> 回测 -> 展示详细结果 -> 生成图表
    """
    try:
        if strategy not in STRATEGY_MAP:
            click.echo(click.style(f'  错误: 未知策略 "{strategy}"，可选: {", ".join(STRATEGY_MAP.keys())}', fg='red'))
            return

        start_date, end_date = _parse_dates(start, end)
        strategy_name = STRATEGY_MAP[strategy][0]

        _print_header('策略回测', f'{symbol} - {strategy_name}')

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

        # 计算指标
        click.echo(click.style('  正在计算技术指标...', fg='blue'))
        df = add_all_indicators(df)
        click.echo(click.style(f'  已计算 {len(df.columns)} 项指标', fg='green'))

        # 运行策略
        click.echo(click.style(f'  正在运行策略: {strategy_name}...', fg='blue'))
        date_tag = _build_date_tag(start_date, end_date)
        chart_gen = ChartGenerator(output_dir=output, date_tag=date_tag)
        result, chart_paths, sname, sig_series = _run_single_analysis(df, strategy, capital, chart_gen)
        # 为 backtest 命令单独生成信号图表（无其他策略背景）
        try:
            bt_chart_gen = ChartGenerator(output_dir=output, prefix=f'{symbol}_{strategy}', date_tag=date_tag)
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
@click.option('--start', '-st', default=None, help='开始日期，格式: YYYY-MM-DD')
@click.option('--end', '-e', default=None, help='结束日期，格式: YYYY-MM-DD')
@click.option('--capital', '-c', default=100000, type=float, help='初始资金（默认100000）')
def compare_cmd(symbol, start, end, capital):
    """策略对比

    流程：同时运行所有策略 -> 对比回测结果 -> 生成对比图表 -> 输出排名
    """
    try:
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

        headers = ['策略', '总收益率', '年化收益率', '最大回撤', '夏普比率', '胜率', '交易次数', '盈利因子']
        table_rows = []
        for sk in ALL_STRATEGIES:
            risk = all_risks[sk]
            name = STRATEGY_MAP[sk][0]

            def _pct(v):
                return f'{v * 100:.2f}%' if v is not None else 'N/A'

            def _f(v):
                return f'{v:.2f}' if v is not None else 'N/A'

            table_rows.append([
                name,
                _pct(risk.get('total_return')),
                _pct(risk.get('annual_return')),
                _pct(risk.get('max_drawdown')),
                _f(risk.get('sharpe_ratio')),
                _pct(risk.get('win_rate')),
                str(risk.get('total_trades', 'N/A')),
                _f(risk.get('profit_factor')),
            ])

        _print_table(headers, table_rows)

        # 排名
        _print_section('综合排名（按夏普比率）')

        rankings = [(STRATEGY_MAP[sk][0], all_risks[sk].get('sharpe_ratio'),
                     all_risks[sk].get('total_return'),
                     all_risks[sk].get('max_drawdown'),
                     all_risks[sk].get('total_trades')) for sk in ALL_STRATEGIES]
        rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

        headers = ['排名', '策略', '夏普比率', '总收益率', '最大回撤', '交易次数']
        rank_rows = []
        for i, (name, sharpe, total_ret, drawdown, trades) in enumerate(rankings, 1):
            shp_str = f'{sharpe:.2f}' if sharpe is not None else 'N/A'
            ret_str = f'{total_ret * 100:.2f}%' if total_ret is not None else 'N/A'
            dd_str = f'{drawdown * 100:.2f}%' if drawdown is not None else 'N/A'
            tr_str = str(int(trades)) if trades is not None else 'N/A'
            rank_rows.append([i, name, shp_str, ret_str, dd_str, tr_str])
        _print_table(headers, rank_rows)

        # 生成对比图表
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
@click.option('--start', '-st', default=None, help='开始日期，格式: YYYY-MM-DD')
@click.option('--end', '-e', default=None, help='结束日期，格式: YYYY-MM-DD')
@click.option('--output', '-o', default=None, help='输出CSV路径（可选，不指定则打印到屏幕）')
def indicators_cmd(symbol, start, end, output):
    """计算技术指标

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
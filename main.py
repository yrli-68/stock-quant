# 股票量化分析软件 - CLI 主入口
# 提供命令行交互界面，支持股票分析、回测、策略对比等功能

import click
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os
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
from strategies.rsi_strategy import RSIStrategy
from strategies.bollinger_strategy import BollingerStrategy
from strategies.composite_strategy import CompositeStrategy
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
    'rsi': ('RSI策略', RSIStrategy),
    'bollinger': ('布林带策略', BollingerStrategy),
    'composite': ('综合策略', CompositeStrategy),
}

INDEX_STRATEGY_MAP = {
    'momentum': ('动量分层策略', MomentumTieredStrategy),
    'volatility': ('波动率择时策略', VolatilityTimingStrategy),
    'breadth': ('涨跌比确认策略', BreadthConfirmationStrategy),
}

ALL_STRATEGIES = ['ma_cross', 'macd', 'rsi', 'bollinger', 'composite']
ALL_INDEX_STRATEGIES = ['momentum', 'volatility', 'breadth']

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


def _run_single_analysis(df, strategy_key, capital, chart_gen, prefix='', is_index=False):
    """运行单个策略的完整分析流程"""
    strategy_map = INDEX_STRATEGY_MAP if is_index else STRATEGY_MAP
    strategy_name, strategy_class = strategy_map[strategy_key]
    strategy = strategy_class()

    # 生成交易信号
    signals = strategy.generate_signals(df)

    # 运行回测
    engine = BacktestEngine(initial_capital=capital)
    result = engine.run(df, signals)

    # 风险分析
    risk = risk_report(result['daily_returns'].dropna(), result['equity_curve'])
    # 合并风险指标
    result.update(risk)

    # 生成图表
    chart_paths = {}
    title = f'{prefix} {strategy_name}'
    try:
        chart_paths['kline'] = chart_gen.plot_kline_with_indicators(
            df, title=f'{title} - K线图与技术指标'
        )
    except Exception:
        pass
    try:
        chart_paths['equity'] = chart_gen.plot_equity_curve(
            result, title=f'{title} - 权益曲线'
        )
    except Exception:
        pass
    try:
        chart_paths['signals'] = chart_gen.plot_signal_on_price(
            df, signals, title=f'{title} - 买卖信号'
        )
    except Exception:
        pass

    return result, chart_paths, strategy_name


def _parse_dates(start, end):
    """解析日期字符串，返回默认值"""
    if end is None:
        end_date = datetime.now().strftime('%Y-%m-%d')
    else:
        end_date = end
    if start is None:
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    else:
        start_date = start
    return start_date, end_date


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


# ============================================================================
# analyze 命令 - 单只股票分析
# ============================================================================


@cli.command('analyze')
@click.option('--symbol', '-s', required=True, help='股票代码（必需）')
@click.option('--start', '-st', default=None, help='开始日期（默认1年前），格式: YYYY-MM-DD')
@click.option('--end', '-e', default=None, help='结束日期（默认今天），格式: YYYY-MM-DD')
@click.option('--strategy', '-g', default='all', help='策略选择 [ma_cross|macd|rsi|bollinger|composite|all]')
@click.option('--index', '-i', 'is_index', is_flag=True, default=False, help='使用指数专属策略模式（动量分层/波动率择时/涨跌比确认）')
@click.option('--capital', '-c', default=100000, type=float, help='初始资金（默认100000）')
@click.option('--output', '-o', default='./charts', help='图表输出目录（默认./charts）')
def analyze_cmd(symbol, start, end, strategy, is_index, capital, output):
    """单只股票综合分析

    流程：获取数据 -> 计算指标 -> 运行策略 -> 回测 -> 风险分析 -> 生成图表 -> 打印报告

    使用 --index/-i 参数可切换到指数专属策略模式，适用于分析大盘指数。
    """
    try:
        # 解析日期
        start_date, end_date = _parse_dates(start, end)

        # 打印头部
        _print_header('股票量化分析报告' if not is_index else '指数量化分析报告', symbol)

        click.echo(click.style(f'  分析区间: {start_date} ~ {end_date}', fg='white'))
        click.echo(click.style(f'  初始资金: {capital:,.0f}', fg='white'))
        click.echo(click.style(f'  策略模式: {"指数专属" if is_index else "个股通用"}', fg='white'))
        if is_index:
            click.echo(click.style(f'  策略选择: {strategy} (动量分层/波动率择时/涨跌比确认)', fg='white'))
        else:
            click.echo(click.style(f'  策略选择: {strategy}', fg='white'))

        # 获取数据
        click.echo(click.style('\n  正在获取股票数据...', fg='blue'))
        fetcher = DataFetcher()
        df = fetcher.get_stock_data(symbol, start_date, end_date)

        if df is None or len(df) == 0:
            click.echo(click.style(f'  错误: 未能获取股票 {symbol} 的数据', fg='red'))
            return

        click.echo(click.style(f'  获取到 {len(df)} 条数据记录', fg='green'))

        # 计算技术指标
        click.echo(click.style('  正在计算技术指标...', fg='blue'))
        df = add_all_indicators(df)
        click.echo(click.style(f'  已计算 {len(df.columns)} 项指标', fg='green'))

        # 初始化图表生成器
        chart_gen = ChartGenerator(output_dir=output)

        # 确定要运行的策略列表
        strategy_map = INDEX_STRATEGY_MAP if is_index else STRATEGY_MAP
        all_strategies_list = ALL_INDEX_STRATEGIES if is_index else ALL_STRATEGIES

        if strategy == 'all':
            strategies_to_run = all_strategies_list
        else:
            if strategy not in strategy_map:
                avail = ', '.join(strategy_map.keys())
                click.echo(click.style(f'  错误: 未知策略 "{strategy}"，可选: {avail}, all', fg='red'))
                return
            strategies_to_run = [strategy]

        # 运行策略
        all_results = {}
        all_risks = {}
        all_charts = {}

        for sk in strategies_to_run:
            click.echo(click.style(f'  正在运行策略: {strategy_map[sk][0]}...', fg='blue'))
            result, chart_paths, sname = _run_single_analysis(
                df, sk, capital, chart_gen, is_index=is_index
            )
            all_results[sk] = result
            all_risks[sk] = result
            all_charts[sk] = chart_paths

        # 输出每个策略的回测结果
        for sk in strategies_to_run:
            sname = strategy_map[sk][0]
            risk = all_risks[sk]
            charts = all_charts[sk]

            _print_section(f'策略: {sname}')

            headers = ['指标', '数值']
            rows = _format_risk_report_rows(risk)
            _print_table(headers, rows)

            if charts:
                click.echo(click.style(f'\n    生成的图表:', fg='white'))
                for chart_name, chart_path in charts.items():
                    click.echo(click.style(f'      - {chart_name}: {chart_path}', fg='green'))

        # 如果运行了多个策略，输出对比
        if len(strategies_to_run) > 1:
            _print_section('策略对比排名')
            rankings = []
            for sk in strategies_to_run:
                rank_total_return = all_risks[sk].get('total_return', -999)
                rank_sharpe = all_risks[sk].get('sharpe_ratio', -999)
                rankings.append((strategy_map[sk][0], rank_total_return, rank_sharpe))
            rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

            headers = ['排名', '策略', '总收益率', '夏普比率']
            table_rows = []
            for i, (name, total_ret, sharpe) in enumerate(rankings, 1):
                ret_str = f'{total_ret * 100:.2f}%' if total_ret is not None else 'N/A'
                shp_str = f'{sharpe:.2f}' if sharpe is not None else 'N/A'
                table_rows.append([i, name, ret_str, shp_str])
            _print_table(headers, table_rows)

            # 生成策略对比图表
            click.echo(click.style('\n  正在生成策略对比图表...', fg='blue'))
            try:
                compare_result = {strategy_map[sk][0]: all_results[sk] for sk in strategies_to_run}
                compare_path = chart_gen.plot_compare_strategies(compare_result)
                click.echo(click.style(f'    策略对比图: {compare_path}', fg='green'))
            except Exception as e:
                click.echo(click.style(f'    对比图生成失败: {e}', fg='yellow'))

        click.echo()
        click.echo(click.style('  分析完成!', fg='green', bold=True))
        click.echo()

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


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
@click.option('--output', '-o', default='./charts', help='图表输出目录（默认./charts）')
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
        chart_gen = ChartGenerator(output_dir=output)
        result, chart_paths, sname = _run_single_analysis(df, strategy, capital, chart_gen)
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

        rankings = [(STRATEGY_MAP[sk][0], all_risks[sk].get('sharpe_ratio', -999),
                     all_risks[sk].get('total_return', -999)) for sk in ALL_STRATEGIES]
        rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

        headers = ['排名', '策略', '夏普比率', '总收益率']
        rank_rows = []
        for i, (name, sharpe, total_ret) in enumerate(rankings, 1):
            shp_str = f'{sharpe:.2f}' if sharpe is not None else 'N/A'
            ret_str = f'{total_ret * 100:.2f}%' if total_ret is not None else 'N/A'
            rank_rows.append([i, name, shp_str, ret_str])
        _print_table(headers, rank_rows)

        # 生成对比图表
        click.echo(click.style('\n  正在生成策略对比图表...', fg='blue'))
        chart_gen = ChartGenerator(output_dir='./charts')
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
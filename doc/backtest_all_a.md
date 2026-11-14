# 全 A 股批量回测导出

`test/backtest_all_a.py` 用 `main.py` 同样的策略与回测引擎，对全 A 股逐只
回测，并把结果导出为 CSV，适合做策略横截面统计、因子研究和二次分析。

## 1. 原理

对每只股票独立执行：

```text
SQLite/网络行情
  -> add_all_indicators（MA/EMA/MACD/RSI/BOLL/KDJ/SAR/ATR/ADX/OBV/...）
  -> 策略 generate_signals（1=买 / 0=观望 / -1=卖）
  -> 信号过滤（涨幅、RSI、SAR、EMA60、ADX、指数、最大持股等）
  -> BacktestEngine（佣金 0.03%、滑点 0.01%、按仓位模式买卖）
  -> 总收益/年化/最大回撤/夏普/胜率/盈亏比/交易明细
```

多只股票是**独立资金**回测，不是组合资金曲线；汇总统计是横截面平均。

## 2. 使用

```bash
# 默认：全部 A 股、近 1 年、boll,macd、6 进程
.venv/bin/python test/backtest_all_a.py

# 指定区间
.venv/bin/python test/backtest_all_a.py --start 20251106 --end 20261106

# 全部策略（ALL_SELECTION）
.venv/bin/python test/backtest_all_a.py -g all

# 排除 ST / 小于 50 亿市值
.venv/bin/python test/backtest_all_a.py --exclude-st --min-mktcap 50

# 先小样本验证
.venv/bin/python test/backtest_all_a.py --limit 100

# 额外导出每日权益曲线（文件较大）
.venv/bin/python test/backtest_all_a.py --equity
```

## 3. 输出

默认写到 `report/`：

| 文件 | 内容 |
|------|------|
| `backtest_all_a_<tag>_summary.csv` | 每只股票 × 每个策略一行：收益、回撤、夏普、胜率、交易次数、期末权益、最新信号等 |
| `backtest_all_a_<tag>_strategy_summary.csv` | 按策略汇总：股票数、平均/中位收益、平均回撤、平均夏普、正收益比例等 |
| `backtest_all_a_<tag>_trades.csv` | 全部交易明细：买入/卖出日期、价格、股数、持仓天数、收益率、盈亏额、退出原因 |
| `backtest_all_a_<tag>_errors.csv` | 无数据/异常标的 |
| `backtest_all_a_<tag>_top20_<策略>.csv` | 每个策略收益最高的 20 只（`--top 0` 可关闭） |
| `backtest_all_a_<tag>_bottom20_<策略>.csv` | 每个策略收益最低的 20 只 |

读取 CSV 时建议把代码列指定为字符串，避免丢失前导零：

```python
import pandas as pd
df = pd.read_csv(
    'report/backtest_all_a_xxx_summary.csv',
    dtype={'code': str},
)
```

## 4. 注意

- 股票池来自当前 SQLite 的 A 股列表，已排除 `delisted` 表中的退市代码，
  因此存在**生存者偏差**。
- 每只股票独立使用初始资金，汇总平均收益不代表一个真实组合的收益。
- 默认 `boll,macd`；`quality_value` 依赖估值/财务数据，可能明显更慢。
- 数据源默认 `sqlite`，本地缺失的标的会自动回退新浪/东财/AkShare，
  因此少量标的会使用网络数据。

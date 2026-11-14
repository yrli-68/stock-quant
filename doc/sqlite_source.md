# stock_predict SQLite 数据源接入

`stock-quant` 可以直接读取 **stock_predict** 项目维护的本地 SQLite 缓存库，
把原来「MySQL + 网络」的数据获取流程升级为「本地库优先，缺失再回退」。

## 1. 数据来源

默认读取：

```text
<stock-quant 同级目录>/stock_predict/stock_cache.db
```

即 `/home/lan/桌面/stock_predict/stock_cache.db`。只读打开，不会修改
stock_predict 的数据。

用到的表：

| 表 | 内容 | stock-quant 中的用途 |
|----|------|----------------------|
| `daily_bars` | 日 K：code/date/open/high/low/close/vol | 行情、指标、回测 |
| `adjust` | k = 不复权价 ÷ 后复权价 | 把库内 hfq 转成乘法前复权 qfq |
| `stocks` | 代码/名称/行业/市值/换手率/PE/PB | 股票列表、名称解析、扫描 |
| `delisted` | 退市代码 | 股票列表过滤 |

- `daily_bars` 的 `vol` 单位是「手」，读取时已 ×100 换算为「股」。
- 库内价格是后复权（hfq），读取时乘以 `adjust.k` 得到与现价同一水平的前复权
  （乘法前复权），因此 `最新价格`、布林带、均线等展示值都与行情软件一致。
- 没有 `adjust` 记录的极少数新上市标的会保留 hfq，收益率不受影响。
- `scan` 的股票列表来自 `stocks` 快照，价格/涨跌幅取库内最后一个交易日；
  单只股票的 K 线仍会在分析时按需补齐实时/网络尾部数据。

## 2. 配置

`squant.toml`：

```toml
[tool.stock-quant.sqlite]
enabled = true
path = "../stock_predict/stock_cache.db"
```

- `path` 支持绝对路径或相对项目根目录的路径。
- 留空时自动探测：`stock_predict/stock_cache.db`、
  `../stock_predict/stock_cache.db`、`stock_cache.db` 等。
- 环境变量 `STOCK_QUANT_SQLITE` 优先级最高；也兼容 stock_predict 的
  `STOCK_DB` 环境变量。
- `enabled = false` 可完全关闭 SQLite，恢复原来的 MySQL/网络流程。

## 3. 命令用法

```bash
# 默认 auto：有 SQLite 就优先读库
python main.py analyze -s 000725 -g macd
python main.py backtest -sf input/pool.txt -g all

# 显式指定 SQLite（本地缺数据时仍会回退网络）
python main.py analyze -s 600519 -g boll --src sqlite
python main.py scan -t 10 --changepercent --src sqlite
python main.py compare -s 300750 --src sqlite
python main.py indicators -s 000725 --src sqlite
```

`--src auto` 与 `--src sqlite` 的区别：

- `auto`：SQLite 可用 → 本地优先；不可用 → 启动时测速选择网络源。
- `sqlite`：强制先把 SQLite 当首选；本地没有该标的或数据过旧时再回退网络。

## 4. 回退与新鲜度

- 请求结束日在库内最新日期之后：自动用网络/实时行情补齐尾部。
- 本地库末日距请求结束超过阈值（交易日 20 天、当天 90 天）：判定为过旧，
  回退 MySQL/网络完整流程。
- 本地库没有该代码：回退 MySQL/网络。
- MySQL 未启动时 CLI 会提示：

```text
提示: MySQL 未连接，使用 SQLite 数据源: .../stock_predict/stock_cache.db
```

## 5. 覆盖范围

stock_predict 库当前包含约 1.1 万个标的、2700 万根日 K：

- A 股：沪 `sh`、深 `sz`、北交所 `bj`
- 指数、ETF、基金
- 港股 `hk`、美股 `us`、期货 `fu`、加密货币 `cr`

`stock-quant` 的 `analyze/backtest/scan/compare/indicators` 使用 A 股与指数
部分；港股/美股/加密货币仍走原有网络通道。

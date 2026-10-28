# Changelog
本项目版本号统一维护在 `_version.py`，发布时只需修改该文件。
## [1.2.0] - 2026-10-26
- 增加daemon.py代码，默认选项下在14：30和14：50分别执行一次python3 main.py analyze -sf input/big.txt -g boll,macd, --notify。
- 数据库结构升级：行情、指标、估值、财务、股东、增减持、信号与回测表统一增加 `code/exchange/asset_type` 资产维度。
- `financial_reports` 增加 `announce_date` 与 `data_version`，质量因子回测改为按公告日对齐，禁止用报告期代替公告日。
- 存量库升级执行 `data/migrations/20261026_add_instrument_dimension.sql`。

## [2.0.0] - 2026-10-28
- 恢复使用数据库，默认先访问数据库，数据缺失再从网络获取，速度提升。
- 对数据表的内容错误进行修正，完善数据表结构。
- 增加数据库触发器，自动生成market_breadth数据（宽基指标）。
- 优化命令选项默认值。

## [1.1.1] - 2026-10-22
- 修正 --onestock 选项时的年化收益率计算bug。

## [1.1.0] - 2026-10-22
- 将选项 --onestock 修改为 --onestock [n], 若n=1，当前持仓 RSI > 75 时，出现其他股票买入信号的情况下卖出换股（回测效果较好）。

## [1.0.0] - 2026-10-21
- 初始版本。

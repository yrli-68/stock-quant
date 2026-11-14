# Changelog
本项目版本号统一维护在 `_version.py`，发布时只需修改该文件。

## [3.0.0] - 2026-11-08
- 增加大盘趋势分析，根据大盘趋势进行买卖过滤。回测收益率大幅提升！
- 增加--onestock模式下的权益曲线图输出。

## [2.4.0] - 2026-11-08
- 新增图形界面 `gui.py`，深色桌面风格参考 stock_predict/stock_gui.py。
- GUI 覆盖 analyze / scan / backtest / compare / indicators 全部命令与参数，后台调用同一套 CLI，行为与命令行一致。
- 支持命令预览、后台运行/停止、日志流、报告自动识别、打开报告/输出目录、环境状态检测。
- 标签页懒加载，窗口先显示再构建表单；`python main.py` 不带子命令时启动 GUI，`--gui` 可显式启动。
- CLI 完全保留：`python main.py --help`、`python main.py analyze ...` 等用法不变。

## [2.3.0] - 2026-11-08
- 接入 stock_predict 的 SQLite 缓存库（`daily_bars`/`adjust`/`stocks`），默认路径 `../stock_predict/stock_cache.db`。
- 新增 `--src sqlite`；`auto` 模式下 SQLite 可用时优先读本地库，缺失/过旧自动回退 MySQL/网络。
- SQLite 库内 hfq 通过 `adjust.k` 转为前复权，成交量由「手」换算为「股」。
- 股票列表、名称解析、指数数据同步支持 SQLite；扫描/回测/分析可离线运行。
- 分析报告自动创建 `report/` 目录。

## [2.2.0] - 2026-11-06
- 增加指标打分score，作为买卖信号的辅助参考。

## [2.1.0] - 2026-11-04
- 优化数据库访问方式。
- 增加daemon、pollmgr等工具，及buildxxx.sh编译脚本。
- 增加macd1（6，13，5）策略。

## [2.0.0] - 2026-10-28
- 恢复使用数据库，默认先访问数据库，数据缺失再从网络获取，速度提升。
- 对数据表的内容错误进行修正，完善数据表结构。
- 增加数据库触发器，自动生成market_breadth数据（宽基指标）。
- 优化命令选项默认值。

## [1.2.0] - 2026-10-26
- 增加daemon.py代码，默认选项下在14：30和14：50分别执行一次python3 main.py analyze -sf input/big.txt -g boll,macd, --notify。
- 数据库结构升级：行情、指标、估值、财务、股东、增减持、信号与回测表统一增加 `code/exchange/asset_type` 资产维度。
- `financial_reports` 增加 `announce_date` 与 `data_version`，质量因子回测改为按公告日对齐，禁止用报告期代替公告日。
- 存量库升级执行 `data/migrations/20261026_add_instrument_dimension.sql`。

## [1.1.1] - 2026-10-22
- 修正 --onestock 选项时的年化收益率计算bug。

## [1.1.0] - 2026-10-22
- 将选项 --onestock 修改为 --onestock [n], 若n=1，当前持仓 RSI > 75 时，出现其他股票买入信号的情况下卖出换股（回测效果较好）。

## [1.0.0] - 2026-10-21
- 初始版本。

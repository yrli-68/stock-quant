# 图形界面使用说明

`gui.py` 是 `stock-quant` 的桌面界面，布局与配色参考
`stock_predict/stock_gui.py`。界面不复制分析算法，而是把 `main.py`
的全部 CLI 参数封装成表单，后台调用同一套命令，因此：

- GUI 与 CLI 的策略、数据源、报告完全一致；
- CLI 仍然完整可用，适合脚本、守护进程和自动化。

## 1. 启动

```bash
# 不带子命令：启动图形界面
python main.py

# 显式启动图形界面
python main.py --gui

# 命令行方式不变
python main.py --help
python main.py analyze -s 000725 -g macd
python main.py scan --random --nost
```

如果当前环境没有图形显示（SSH/无桌面），`python main.py` 会打印 CLI
帮助并提示无法启动图形界面，不会阻塞脚本。

## 2. 界面功能

| 标签页 | 对应命令 | 覆盖的参数 |
|--------|----------|------------|
| 分析 Analyze | `analyze` | `-s/-sf/-h/-st/-e/-g/-i/-c/-o/-of/-x/-m/-f/--chart/--forecast/--notify/--plan-time/-sn/--maxholddays/--src` |
| 扫描 Scan | `scan` | `-g/-t/-o/--min-volume/-x/--forecast/--changepercent/--turnoverratio/--amount/--mktcap/--pe/--random/--peplus/--nost/--nolowcap/--notify/-sn/--maxholddays/--src` |
| 回测 Backtest | `backtest` | `-s/-sf/--random/-g/-st/-e/-c/-o/--chart/--onestock/--fixcapital/--verbose/-sn/--maxholddays/--src` |
| 策略对比 Compare | `compare` | `-s/-st/-e/-c/--chart/-sn/--maxholddays/--src` |
| 因子指标 Indicators | `indicators` | `-s/-st/-e/-o/--chart/--src` |
| 环境 / 工具 | — | SQLite/MySQL 状态、报告/输出目录、股票池、配置、CLI 帮助 |

公共交互：

- **命令预览**：底部实时显示即将执行的 CLI 命令，可复制。
- **运行 / 停止**：后台子进程执行，界面不冻结；支持 `F5` 运行、
  `Ctrl+Enter` 运行、`Ctrl+L` 清空日志、`Ctrl+S` 保存表单。
- **日志流**：命令输出实时滚动，错误/警告/成功分色显示。
- **报告识别**：日志里出现 `report/*.html` 时自动记录，点“打开报告”
  直接用系统浏览器打开。
- **标签页懒加载**：窗口先显示，表单首次点击时构建，避免低配/高分屏
  环境下启动等待过久。
- **表单记忆**：路径、日期、策略等保存到
  `~/.config/stock-quant/gui.json`（可用 `XDG_CONFIG_HOME` 覆盖）。

## 3. 策略选择

- 分析页支持“指数模式 -i”，勾选后策略列表切换为
  动量分层 / 波动率择时 / 涨跌比确认。
- 扫描、回测页使用个股策略列表。
- “全部（all）”对应 CLI 的 `-g all`。

## 4. 线程与停止

GUI 只启动一个子进程；运行中再次点击运行会提示先停止。停止按钮先发送
`terminate`，2.5 秒后仍未退出则强制结束。退出窗口时如有任务运行会二次
确认。

## 5. 与 stock_predict GUI 的关系

复用其深色主题色值、字体与桌面布局习惯；数据层复用
`core/sqlite_source.py` 读取 `stock_predict/stock_cache.db`，因此两个工具
可以共享同一份本地行情缓存，互不修改对方数据。

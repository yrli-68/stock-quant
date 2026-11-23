# stock-quant 部署安装指导

本文说明从零部署 `stock-quant` 的步骤，包括：

- 运行环境与依赖
- MySQL 数据库初始化与升级
- `squant.toml` 配置
- 预编译 `squant` / `poolmgr` 部署
- 源码 + venv 部署
- systemd 定时服务
- 日常运维、升级、排障

---

## 1. 部署方式选择

| 方式 | 适用场景 | 优点 | 注意 |
|------|----------|------|------|
| 预编译可执行文件 | 生产服务器、无 Python 环境 | 无需安装 Python 依赖 | 当前产物为 Linux x86-64；配置/股票池需放在运行目录 |
| 源码 + venv | 开发、调试、二次开发 | 可直接修改代码、升级依赖 | 需要 Python 3.10+ 和网络安装依赖 |
| 源码 + systemd | 每日自动分析/通知 | 开机自启、日志统一 | 需按实际路径修改 unit 文件 |

当前仓库中的预编译文件：

```text
dist/squant
dist/poolmgr
```

> `dist/` 已被 `.gitignore` 忽略，部署时应确认该目录中的文件是目标版本。
> 如果源码有更新，建议在目标机器重新构建，或使用源码 + venv 方式部署。

---

## 2. 环境要求

### 2.1 操作系统

- Linux x86-64
- 预编译 `squant` 在 glibc 2.39 环境构建；过旧的发行版可能无法直接运行
- 如需在其他架构/系统运行，请在目标机器重新构建

### 2.2 Python（源码部署）

- Python 3.10+
- 推荐 Python 3.12
- 必须支持 `venv`

### 2.3 MySQL

- MySQL 8.0+
- 字符集：`utf8mb4`
- 需要开启事件调度器：`event_scheduler=ON`

### 2.4 网络

需要访问以下数据源：

- 新浪财经
- 东方财富
- Baostock
- AkShare 相关接口
- 钉钉 Webhook（如启用 `--notify`）

如服务器使用代理，请配置 `HTTPS_PROXY` / `HTTP_PROXY`。Baostock 使用独立 socket 端口，必要时需放行。

### 2.5 中文字体

图表依赖中文字体。代码优先读取：

```text
~/.local/share/fonts/noto/NotoSansCJKsc-Regular.otf
~/.local/share/fonts/noto/NotoSansCJKsc-Bold.otf
```

安装示例：

```bash
sudo apt update
sudo apt install -y fonts-noto-cjk
fc-cache -fv
```

若缺少中文字体，图表中的中文可能显示为方块。

### 2.6 时区

定时任务依赖本地时间，建议：

```bash
sudo timedatectl set-timezone Asia/Shanghai
timedatectl
```

---

## 3. 目录规划

推荐生产目录：

```text
/opt/stock-quant/
├── squant.toml            # 主配置（数据库、Webhook、阈值等）
├── input/
│   └── pool.txt           # 默认股票池
├── output/                # 图表输出
├── report/                # HTML 报告
├── logs/                  # 可选：服务日志
├── bin/
│   ├── squant             # 预编译主程序
│   └── poolmgr            # 预编译股票池工具
└── deploy/
    └── service_runner.py  # 源码部署时的定时运行器
```

说明：

- `squant.toml` 必须位于运行目录或可执行文件目录；
- `input/pool.txt` 默认从配置根目录查找；
- 建议通过包装脚本先 `cd /opt/stock-quant` 再执行 `bin/squant`，确保配置和股票池路径正确。

---

## 4. 数据库部署

### 4.1 创建数据库和用户

以 MySQL 管理员身份执行：

```sql
CREATE DATABASE IF NOT EXISTS stock_quant
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS 'smit'@'localhost' IDENTIFIED BY '请替换为强密码';
GRANT ALL PRIVILEGES ON stock_quant.* TO 'smit'@'localhost';
FLUSH PRIVILEGES;
```

生产环境建议：

- 使用独立数据库账号；
- 不要使用 root；
- 密码写入 `squant.toml` 后设置文件权限为 `600`。

### 4.2 新库：导入完整 DDL

```bash
mysql -u root -p < data/stock_quant.sql
```

或：

```bash
mysql -u smit -p stock_quant < data/stock_quant.sql
```

> `data/stock_quant.sql` 包含建库、建表、视图、触发器、存储过程和事件定义。

### 4.3 存量库：按顺序执行迁移

已有数据库按文件名顺序执行：

```bash
mysql -u smit -p stock_quant < data/migrations/20261026_add_instrument_dimension.sql
mysql -u smit -p stock_quant < data/migrations/20261027_add_market_breadth_and_broad_index.sql
mysql -u smit -p stock_quant < data/migrations/20261028_add_market_breadth_trigger.sql
mysql -u smit -p stock_quant < data/migrations/20261028_breadth_trigger_all_columns.sql
```

说明：

- `20261028_breadth_trigger_all_columns.sql` 是最终版触发器/存储过程/事件定义；
- `20261028_backfill_market_breadth.sql` 是可选的历史回填 SQL，执行较慢；
- 推荐使用 pandas 版本回填：

```bash
venv/bin/python test/backfill_market_breadth.py --apply
```

### 4.4 开启事件调度器

```sql
SET GLOBAL event_scheduler = ON;
```

持久化配置：在 MySQL 配置文件（如 `/etc/mysql/mysql.conf.d/mysqld.cnf`）中加入：

```ini
[mysqld]
event_scheduler = ON
```

重启 MySQL 后验证：

```sql
SHOW VARIABLES LIKE 'event_scheduler';
SHOW EVENTS FROM stock_quant;
```

预期能看到：

```text
ev_market_breadth_sync
```

### 4.5 数据库健康检查

```sql
USE stock_quant;

SHOW TABLES;
SHOW TRIGGERS;
SHOW EVENTS;

SELECT COUNT(*) FROM daily_kline;
SELECT COUNT(*) FROM market_breadth_dirty;
SELECT COUNT(*) FROM stocks WHERE listed_date IS NULL;
```

`market_breadth_dirty` 应逐步被事件消费；若长期堆积，检查事件调度器或手动执行：

```sql
CALL process_market_breadth_dirty();
```

### 4.6 安装后的数据维护

#### 回填上市日期

```bash
venv/bin/python test/backfill_listed_date.py --apply
```

作用：

- 从 AkShare 获取 A 股上市日期；
- 回填 `stocks.listed_date`；
- 为 `daily_kline` 中缺失的股票补建 `stocks` 资产记录。

#### 清理节假日/周末脏数据

```bash
venv/bin/python test/clean_daily_kline_holidays.py
venv/bin/python test/clean_daily_kline_holidays.py --apply
```

作用：

- 以基准股票（默认 `601398`）的交易日历为参照；
- 删除 `daily_kline` 中不在交易日历且符合周末/节假日规则的脏数据；
- 自动重算受影响日期的 `market_breadth`。

建议在首次导入历史数据后执行一次，之后定期检查。

---

## 5. 配置 `squant.toml`

主配置示例：

```toml
[tool.stock-quant]
position_style = "full"
market_trend_index = "000852"   # 大盘趋势指数代码，默认中证1000

[tool.stock-quant.service]
SQ_RUN_TIME = "1445"
SQ_STRATEGY = "boll,macd"

[tool.stock-quant.database]
host = "localhost"
port = 3306
user = "smit"
password = "请替换为强密码"
database = "stock_quant"

[tool.stock-quant.WEBHOOK]
URL = "https://oapi.dingtalk.com/robot/send?access_token=请替换"
key = "quant"

[tool.stock-quant.thresholds]
MAX_RSI_THRESHOLD = 85
MIN_RSI_THRESHOLD = 65
PE_THRESHOLD = 100
BOLL_SQUEEZE_SCALE = 0.01
BOLL_EXTRAWIDE_SCALE = 3
EMA60_CHECK = 0
ADX_CHECK = 0
```

### 5.1 配置安全

```bash
chmod 600 /opt/stock-quant/squant.toml
```

- 不要提交真实数据库密码和钉钉 Token；
- 如 Token 泄露，立即在钉钉后台重置；
- 生产环境建议使用只读/最小权限数据库账号，写库账号单独管理。

### 5.2 配置路径规则

源码运行时，配置根目录为项目根目录。

预编译 `squant` 运行时，配置根目录按以下顺序查找：

```text
当前工作目录
可执行文件所在目录
PyInstaller 临时目录
```

因此建议：

```bash
cd /opt/stock-quant
./bin/squant --help
```

或使用包装脚本固定工作目录。

---

## 6. 预编译可执行文件部署

### 6.1 复制文件

```bash
sudo mkdir -p /opt/stock-quant/{bin,input,output,report,logs}

sudo cp dist/squant /opt/stock-quant/bin/squant
sudo cp dist/poolmgr /opt/stock-quant/bin/poolmgr
sudo cp squant.toml /opt/stock-quant/squant.toml
sudo cp input/pool.txt /opt/stock-quant/input/pool.txt

sudo chmod 755 /opt/stock-quant/bin/squant
sudo chmod 755 /opt/stock-quant/bin/poolmgr
sudo chmod 600 /opt/stock-quant/squant.toml
```

### 6.2 创建命令包装脚本

```bash
sudo tee /usr/local/bin/squant >/dev/null <<'EOF'
#!/bin/sh
cd /opt/stock-quant || exit 1
exec /opt/stock-quant/bin/squant "$@"
EOF

sudo tee /usr/local/bin/poolmgr >/dev/null <<'EOF'
#!/bin/sh
cd /opt/stock-quant || exit 1
exec /opt/stock-quant/bin/poolmgr "$@"
EOF

sudo chmod 755 /usr/local/bin/squant /usr/local/bin/poolmgr
```

### 6.3 验证

```bash
squant --help
poolmgr -l

squant analyze \
  -s 515120 \
  -st 20250101 \
  -e 20251231 \
  -g boll
```

检查输出：

- 能正常读取 `squant.toml`；
- 能连接 MySQL；
- 能生成 `output/` 图表和 `report/` HTML 报告；
- 数据源测速正常。

### 6.4 预编译文件注意事项

- `dist/squant`、`dist/poolmgr` 是 Linux x86-64 文件；
- 不要在 Windows/macOS 或 ARM 机器上直接使用；
- 如果目标机 glibc 较旧，可能无法运行，建议源码部署或在目标机重建；
- 可执行文件中的代码版本以构建时源码为准，升级源码后需重新构建。

---

## 7. 源码 + venv 部署

### 7.1 创建虚拟环境

```bash
cd /opt/stock-quant
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

### 7.2 关于 `deploy/install.sh`

当前仓库已将 `pyproject.toml` 改名为 `squant.toml`，因此 `deploy/install.sh` 中的：

```bash
pip install -e .
```

在新环境中可能失败。新部署建议使用本文的手动 venv 步骤，或先恢复一个可用的 `pyproject.toml`。

`deploy/install.sh` 还硬编码了服务路径，使用前必须按实际目录修改。

### 7.3 源码运行

```bash
cd /opt/stock-quant
venv/bin/python main.py --help

venv/bin/python main.py analyze \
  -sf input/pool.txt \
  -st 20250101 \
  -e 20251231 \
  -g boll,macd
```

### 7.4 创建源码版包装脚本

```bash
sudo tee /usr/local/bin/squant >/dev/null <<'EOF'
#!/bin/sh
cd /opt/stock-quant || exit 1
exec /opt/stock-quant/venv/bin/python /opt/stock-quant/main.py "$@"
EOF

sudo chmod 755 /usr/local/bin/squant
```

### 7.5 从源码编译单文件可执行程序

如果目标机器不方便长期维护 Python 虚拟环境，可以用 PyInstaller 将源码打包成单文件可执行程序。

#### 7.5.1 安装构建工具

在项目 venv 中安装 PyInstaller：

```bash
cd /opt/stock-quant
venv/bin/pip install --upgrade pip
venv/bin/pip install "pyinstaller>=6.0"
```

确认：

```bash
venv/bin/pyinstaller --version
```

建议始终使用 `venv/bin/pyinstaller`，不要混用系统级 `pyinstaller`，否则可能打包含不到 venv 中的依赖。

#### 7.5.2 构建前检查

```bash
cd /opt/stock-quant

# 关键入口和配置存在
ls main.py poolmgr.py squant.toml
ls input/pool.txt

# 依赖可导入
venv/bin/python -c "import pandas, numpy, matplotlib, mplfinance, requests, pymysql, akshare, yfinance, lunardate"
```

构建机器建议与运行机器保持相同：

- 操作系统：Linux
- CPU 架构：x86-64
- Python 版本：尽量一致
- glibc 版本：在越旧的发行版上构建，兼容范围越大

#### 7.5.3 构建 `squant`

在项目根目录执行：

```bash
cd /opt/stock-quant

venv/bin/pyinstaller \
  --onefile \
  --console \
  --name squant \
  --clean \
  --noconfirm \
  --paths "$PWD" \
  --hidden-import pymysql \
  --hidden-import akshare \
  --hidden-import yfinance \
  --hidden-import lunardate \
  --collect-data akshare \
  --collect-data yfinance \
  main.py
```

参数说明：

| 参数 | 作用 |
|------|------|
| `--onefile` | 生成单个可执行文件 |
| `--console` | 保留命令行窗口输出 |
| `--name squant` | 输出文件名 `squant` |
| `--clean` | 清理 PyInstaller 缓存 |
| `--noconfirm` | 覆盖已有输出不询问 |
| `--paths "$PWD"` | 让 PyInstaller 找到本地 `core/`、`strategies/`、`visualization/` |
| `--hidden-import pymysql` | `core/db.py` 中为延迟导入，必须显式包含 |
| `--hidden-import akshare` | AkShare 为延迟导入 |
| `--hidden-import yfinance` | yfinance 为延迟导入 |
| `--hidden-import lunardate` | 节假日判断依赖 |
| `--collect-data akshare` | 打包 `akshare/file_fold/calendar.json` 等数据 |
| `--collect-data yfinance` | 打包 yfinance 的 proto 等数据文件 |

构建成功后：

```text
dist/squant
```

文件较大（通常 100 MB 左右），属于正常现象。

#### 7.5.4 构建 `poolmgr`

```bash
cd /opt/stock-quant

venv/bin/pyinstaller \
  --onefile \
  --console \
  --name poolmgr \
  --clean \
  --noconfirm \
  poolmgr.py
```

构建成功后：

```text
dist/poolmgr
```

`poolmgr` 只依赖 `click` 和标准库，文件明显小于 `squant`。

#### 7.5.4.1 构建 `daemon`

```bash
cd /opt/stock-quant

venv/bin/pyinstaller \
  --onefile \
  --console \
  --name daemon \
  --clean \
  --noconfirm \
  daemon.py
```

构建成功后：

```text
dist/daemon
```

`daemon` 用于每日定时调用分析命令。预编译模式下，它不会调用 `main.py`，而是查找同目录或项目 `bin/` 下的 `squant` 可执行文件：

```text
dist/daemon
dist/squant
```

或：

```text
/opt/stock-quant/bin/daemon
/opt/stock-quant/bin/squant
```

因此部署时必须保证 `daemon` 和 `squant` 同时存在。

#### 7.5.5 使用仓库构建脚本

仓库根目录已有构建脚本：

```bash
buildsquant.sh
buildpool.sh
builddaemon.sh
```

当前脚本大致执行：

```bash
pyinstaller --onefile --console --name squant ...
pyinstaller --onefile --console --name poolmgr ...
pyinstaller --onefile --console --name daemon ...
```

使用前建议确认：

1. 使用 `venv/bin/pyinstaller`，而不是系统 `pyinstaller`；
2. `squant` 构建包含 `--paths "$PWD"`；
3. 包含 `--hidden-import lunardate`；
4. 构建完成后执行 `dist/squant --help` 和 `dist/poolmgr --help` 验证。

推荐的 `buildsquant.sh`：

```bash
#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

venv/bin/pyinstaller \
  --onefile \
  --console \
  --name squant \
  --clean \
  --noconfirm \
  --paths "$PWD" \
  --hidden-import pymysql \
  --hidden-import akshare \
  --hidden-import yfinance \
  --hidden-import lunardate \
  --collect-data akshare \
  --collect-data yfinance \
  main.py

rm -f venv/bin/squant
cp dist/squant venv/bin/squant
chmod +x venv/bin/squant
```

推荐的 `buildpool.sh`：

```bash
#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

venv/bin/pyinstaller \
  --onefile \
  --console \
  --name poolmgr \
  --clean \
  --noconfirm \
  poolmgr.py

rm -f venv/bin/poolmgr
cp dist/poolmgr venv/bin/poolmgr
chmod +x venv/bin/poolmgr
```

#### 7.5.6 验证构建产物

```bash
cd /opt/stock-quant

./dist/squant --help
./dist/poolmgr --help

./dist/poolmgr -l

./dist/squant analyze \
  -s 515120 \
  -st 20250101 \
  -e 20251231 \
  -g boll
```

再测试一次多股票/回测流程：

```bash
./dist/squant backtest \
  -sf input/pool.txt \
  -st 20200101 \
  -e 20251231 \
  -g boll,macd
```

确认：

- 能读取 `squant.toml` 和 `input/pool.txt`；
- 能连接 MySQL；
- 能生成 `output/` 和 `report/`；
- 没有 `ModuleNotFoundError`；
- 没有 akshare 数据文件缺失错误。

#### 7.5.7 构建常见错误

| 错误 | 原因 | 处理 |
|------|------|------|
| `ModuleNotFoundError: No module named 'core'` | 未加入项目根目录 | 添加 `--paths "$PWD"` |
| `No module named 'pymysql'` | 延迟导入未被收集 | 添加 `--hidden-import pymysql` |
| `No module named 'akshare'` | 延迟导入未被收集 | 添加 `--hidden-import akshare` |
| `akshare/file_fold/calendar.json` 不存在 | 未收集 akshare 数据文件 | 添加 `--collect-data akshare` |
| `No module named 'lunardate'` | 节假日模块未打包 | 添加 `--hidden-import lunardate` |
| 构建成功但运行报 pandas/numpy 错误 | 混用了系统 Python 和 venv | 使用 `venv/bin/pyinstaller` |
| 换机器后无法运行 | glibc/架构不兼容 | 在目标系统或更旧系统上重新构建 |
| 启动时找不到 `squant.toml` | 工作目录/可执行文件目录不正确 | 进入项目目录运行，或使用包装脚本固定 `cd` |

#### 7.5.8 清理构建中间文件

```bash
rm -rf build squant.spec poolmgr.spec
```

`dist/` 保留可执行文件即可。`build/` 和 `*.spec` 可重新生成。

#### 7.5.9 单文件与目录模式

如果希望启动更快、便于排查依赖，可以使用 `--onedir`：

```bash
venv/bin/pyinstaller \
  --onedir \
  --console \
  --name squant \
  --clean \
  --noconfirm \
  --paths "$PWD" \
  --hidden-import pymysql \
  --hidden-import akshare \
  --hidden-import yfinance \
  --hidden-import lunardate \
  --collect-data akshare \
  --collect-data yfinance \
  main.py
```

输出：

```text
dist/squant/squant
```

目录模式需要整体分发 `dist/squant/`，不能只复制可执行文件。

---

## 8. systemd 定时服务

仓库提供了两套定时相关文件：

| 文件 | 用途 |
|------|------|
| `deploy/service_runner.py` | 常驻进程，每天到达指定时刻后运行一次 `analyze` |
| `deploy/stock-quant.service` | 用户级 systemd unit 示例 |
| `daemon.py` | 另一种轻量定时循环，可直接运行；默认 08:30、14:35 各执行一次，周六/周日跳过；`-t 0830,1430` 可指定多个时间；每次先 analyze，再 scan --random --nost --notify |

### 8.1 使用 `service_runner.py`

`service_runner.py` 的行为：

- 读取 `squant.toml` 的 `[tool.stock-quant.service]`；
- 默认 `SQ_RUN_TIME=1445`；
- 默认 `SQ_STRATEGY=boll,macd`；
- 默认股票池为 `input/big.txt`（代码中的实际默认值）；
- 到时间后执行：

```text
python main.py analyze -sf <WATCHLIST> -g <STRATEGY> --notify
```

- 每天只执行一次；
- 可通过环境变量覆盖：

```bash
export SQ_RUN_TIME=1450
export SQ_STRATEGY=boll,macd
export SQ_WATCHLIST=/opt/stock-quant/input/pool.txt
```

建议在 unit 中显式设置 `SQ_WATCHLIST`，避免使用 `big.txt`。

### 8.2 用户级 systemd

```bash
mkdir -p ~/.config/systemd/user

cp /opt/stock-quant/deploy/stock-quant.service \
   ~/.config/systemd/user/stock-quant.service
```

修改 unit 中的路径：

```ini
[Unit]
Description=Stock Quant Analysis Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/stock-quant
Environment=PYTHONUNBUFFERED=1
Environment=SQ_RUN_TIME=1445
Environment=SQ_STRATEGY=boll,macd
Environment=SQ_WATCHLIST=/opt/stock-quant/input/pool.txt
ExecStart=/opt/stock-quant/venv/bin/python /opt/stock-quant/deploy/service_runner.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

启用：

```bash
systemctl --user daemon-reload
systemctl --user enable --now stock-quant

systemctl --user status stock-quant --no-pager
journalctl --user -u stock-quant -f
```

如希望用户未登录也保持运行：

```bash
loginctl enable-linger "$USER"
```

### 8.3 系统级 systemd

如使用独立账号 `stockquant`：

```bash
sudo useradd -r -m -d /opt/stock-quant -s /bin/bash stockquant
sudo chown -R stockquant:stockquant /opt/stock-quant
```

创建 `/etc/systemd/system/stock-quant.service`：

```ini
[Unit]
Description=Stock Quant Analysis Service
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=simple
User=stockquant
Group=stockquant
WorkingDirectory=/opt/stock-quant
Environment=PYTHONUNBUFFERED=1
Environment=SQ_RUN_TIME=1445
Environment=SQ_STRATEGY=boll,macd
Environment=SQ_WATCHLIST=/opt/stock-quant/input/pool.txt
ExecStart=/opt/stock-quant/venv/bin/python /opt/stock-quant/deploy/service_runner.py
Restart=on-failure
RestartSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stock-quant
sudo systemctl status stock-quant --no-pager
sudo journalctl -u stock-quant -f
```

### 8.4 预编译程序的定时任务

`service_runner.py` 使用 `sys.executable + main.py`，适合源码部署。

如果使用预编译 `squant`，推荐使用 systemd timer 或 cron 直接执行包装脚本：

```bash
# /opt/stock-quant/run_daily.sh
#!/bin/sh
cd /opt/stock-quant || exit 1
exec ./bin/squant analyze \
  -sf input/pool.txt \
  -g boll,macd \
  --notify
```

cron 示例：

```cron
45 14 * * 1-5 /opt/stock-quant/run_daily.sh >> /opt/stock-quant/logs/daily.log 2>&1
```

systemd timer 示例：

`/etc/systemd/system/stock-quant.service`：

```ini
[Unit]
Description=Stock Quant daily analysis
After=network-online.target

[Service]
Type=oneshot
User=stockquant
WorkingDirectory=/opt/stock-quant
ExecStart=/opt/stock-quant/bin/squant analyze -sf input/pool.txt -g boll,macd --notify
```

`/etc/systemd/system/stock-quant.timer`：

```ini
[Unit]
Description=Run stock-quant daily at 14:45

[Timer]
OnCalendar=Mon..Fri *-*-* 14:45:00
Persistent=true

[Install]
WantedBy=timers.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stock-quant.timer
systemctl list-timers | grep stock-quant
```

---

## 9. 日常操作

### 9.1 常用命令

```bash
# 分析
squant analyze -s 515120 -st 20250101 -e 20251231 -g boll,macd

# 回测
squant backtest -sf input/pool.txt -st 20200101 -e 20251231 -g boll,macd

# 单持仓轮动回测
squant backtest -sf input/pool.txt -st 20200101 -e 20251231 \
  -g boll,macd

# 扫描
squant scan --random -g boll,macd -t 10

# 股票池
poolmgr -l
poolmgr -add "600036 000001"
poolmgr -rm "000001"
poolmgr -addf input/favs.txt
poolmgr -rmf input/old_pool.txt
```

### 9.2 输出目录

| 目录 | 内容 |
|------|------|
| `output/` | K 线图、信号图、权益图 PNG |
| `report/` | HTML 分析/回测报告 |
| `input/pool.txt` | 默认股票池 |
| `logs/` | 自定义日志目录（如配置） |

### 9.3 数据库备份

```bash
mkdir -p /opt/backup/stock-quant
mysqldump --single-transaction --routines --triggers \
  -u smit -p stock_quant \
  > /opt/backup/stock-quant/stock_quant_$(date +%F_%H%M).sql
```

备份内容包括表、视图、触发器、存储过程和事件。

---

## 10. 升级流程

1. 停止服务：

   ```bash
   systemctl --user stop stock-quant
   # 或 sudo systemctl stop stock-quant
   ```

2. 备份：

   ```bash
   cp -a /opt/stock-quant/squant.toml /opt/backup/
   cp -a /opt/stock-quant/input/pool.txt /opt/backup/
   mysqldump --single-transaction --routines --triggers \
     -u smit -p stock_quant > /opt/backup/stock_quant_before_upgrade.sql
   ```

3. 替换程序：

   ```bash
   # 预编译方式
   sudo cp dist/squant /opt/stock-quant/bin/squant

   # 源码方式
   cd /opt/stock-quant
   git pull
   venv/bin/pip install -r requirements.txt
   ```

4. 按需执行数据库迁移：

   ```bash
   mysql -u smit -p stock_quant < data/migrations/<new_migration>.sql
   ```

5. 重启并验证：

   ```bash
   systemctl --user restart stock-quant
   systemctl --user status stock-quant --no-pager
   journalctl --user -u stock-quant -n 100 --no-pager
   ```

---

## 11. 健康检查清单

```bash
# 命令是否可用
squant --help
poolmgr -l

# 配置是否能读取
squant analyze -s 515120 -st 20250101 -e 20251231 -g boll

# 数据库
mysql -u smit -p -e "SELECT COUNT(*) FROM stock_quant.daily_kline;"
mysql -u smit -p -e "SHOW VARIABLES LIKE 'event_scheduler';"
mysql -u smit -p -e "SHOW EVENTS FROM stock_quant;"

# 广度队列
mysql -u smit -p -e "SELECT COUNT(*) FROM stock_quant.market_breadth_dirty;"

# 服务
systemctl --user status stock-quant --no-pager
journalctl --user -u stock-quant -n 100 --no-pager
```

---

## 12. 常见问题

### 12.1 `ModuleNotFoundError: No module named 'core'`

预编译时未把项目根目录加入 PyInstaller 搜索路径。构建命令需要包含：

```bash
--paths /path/to/stock-quant
```

### 12.2 数据源连接失败

可能原因：

- Baostock 黑名单/账号限制；
- 东方财富连接被重置；
- 新浪接口限流；
- 服务器无法访问外网。

处理：

```bash
squant analyze --src sina -s 515120 ...
squant analyze --src auto -s 515120 ...
```

查看测速结果和回退日志。

### 12.3 `py_mini_racer` 退出提示

旧版可执行文件退出时可能出现：

```text
AttributeError: 'NoneType' object has no attribute 'mr_free_context'
```

这是 `py_mini_racer` 在解释器退出阶段的析构噪声，通常不影响回测结果和退出码。当前源码已加入安全析构包装；如仍使用旧二进制，重新构建即可。

### 12.4 回测很慢

优先检查是否在做大量网络补数：

```bash
grep "缺失时段" logs/*.log
```

常见原因：

- `daily_kline` 存在节假日/周末脏数据；
- 交易日历被污染；
- 个股停牌或上市前区间被反复请求。

处理：

```bash
venv/bin/python test/clean_daily_kline_holidays.py --apply
venv/bin/python test/backfill_listed_date.py --apply
```

### 12.5 图表中文乱码

安装 Noto CJK 字体，并确认：

```text
~/.local/share/fonts/noto/NotoSansCJKsc-Regular.otf
```

然后：

```bash
fc-cache -fv
```

### 12.6 `market_breadth` 不更新

检查事件调度器：

```sql
SHOW VARIABLES LIKE 'event_scheduler';
SHOW EVENTS FROM stock_quant;
SELECT COUNT(*) FROM market_breadth_dirty;
```

手动重算：

```sql
CALL process_market_breadth_dirty();
```

### 12.7 钉钉通知未发送

检查：

```bash
grep -n "WEBHOOK" /opt/stock-quant/squant.toml
```

确认：

- URL 正确；
- 网络可访问 `oapi.dingtalk.com`；
- 钉钉机器人安全设置允许当前出口 IP；
- 命令包含 `--notify`。

### 12.8 `deploy/install.sh` 执行失败

当前脚本包含：

```bash
pip install -e .
```

但仓库已将 `pyproject.toml` 改名为 `squant.toml`，该步骤可能失败。

新部署请使用本文的“源码 + venv 部署”手动步骤，或先恢复可用的 `pyproject.toml`。

### 12.9 数据库连接失败后的行为

程序启动时会先执行一次数据库连接检查：

- 连接成功：正常运行，K 线优先读数据库；
- 连接失败：打印警告，并将本次进程标记为“数据库不可用”，后续所有数据库读写直接跳过，数据获取自动改走网络。

表现：

```text
数据库连接失败，后续数据将从网络获取: ...
  警告: 数据库连接失败，本次运行将仅从网络获取数据
```

此时仍可运行 analyze/backtest/scan，但速度取决于网络数据源，且不会写回数据库。请优先检查：

```bash
mysqladmin -u smit -p ping
systemctl status mysql
```

---

## 13. 最小快速部署清单

```bash
# 1. 系统依赖
sudo apt update
sudo apt install -y mysql-server fonts-noto-cjk

# 2. 数据库
mysql -u root -p < data/stock_quant.sql
mysql -u root -p -e "SET GLOBAL event_scheduler = ON;"

# 3. 目录
sudo mkdir -p /opt/stock-quant/{bin,input,output,report,logs}

# 4. 程序与配置
sudo cp dist/squant dist/poolmgr /opt/stock-quant/bin/
sudo cp squant.toml /opt/stock-quant/
sudo cp input/pool.txt /opt/stock-quant/input/
sudo chmod 600 /opt/stock-quant/squant.toml

# 5. 包装脚本
sudo tee /usr/local/bin/squant >/dev/null <<'EOF'
#!/bin/sh
cd /opt/stock-quant || exit 1
exec /opt/stock-quant/bin/squant "$@"
EOF
sudo chmod 755 /usr/local/bin/squant

# 6. 验证
squant --help
poolmgr -l
squant analyze -s 515120 -st 20250101 -e 20251231 -g boll

# 7. 数据维护（可选，首次部署建议）
# 源码部署时执行：
# venv/bin/python test/backfill_listed_date.py --apply
# venv/bin/python test/clean_daily_kline_holidays.py --apply
```

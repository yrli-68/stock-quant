#!/usr/bin/env bash
# Stock Quant 编译安装 + 用户级 systemd 服务部署脚本
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE_NAME="stock-quant"
UNIT_FILE="$PROJECT_DIR/deploy/stock-quant.service"
USER_UNIT_DIR="$HOME/.config/systemd/user"

echo "==> 1/4 检查并创建虚拟环境 ($VENV_DIR)"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
fi

echo "==> 2/4 安装依赖与项目（pip install -e .）"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
"$VENV_DIR/bin/pip" install -e "$PROJECT_DIR"

echo "==> 3/4 验证命令入口"
"$VENV_DIR/bin/stock-quant" --help >/dev/null && echo "    stock-quant 命令可用 ✓"

echo "==> 4/4 安装并启动用户级 systemd 服务"
mkdir -p "$USER_UNIT_DIR"
cp "$UNIT_FILE" "$USER_UNIT_DIR/$SERVICE_NAME.service"

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
systemctl --user restart "$SERVICE_NAME"
systemctl --user status "$SERVICE_NAME" --no-pager

echo
echo "部署完成。常用命令："
echo "  启动/停止/重启: systemctl --user start|stop|restart $SERVICE_NAME"
echo "  查看日志:       journalctl --user -u $SERVICE_NAME -f"
echo "  开机自启:       systemctl --user enable $SERVICE_NAME  (已默认执行)"

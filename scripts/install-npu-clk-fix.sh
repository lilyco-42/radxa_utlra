#!/bin/bash
# 在 Radxa Cubie A7A (Debian 13, 内核 6.6.98-4-aw2511) 上构建并安装 npu_clk_fix
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USER_HOME="${1:-/home/radxa}"

if [ ! -d /lib/modules/$(uname -r)/build ]; then
  echo "缺少内核头文件,先安装: sudo apt install linux-headers-$(uname -r)"
  exit 1
fi

echo "[1/4] 编译模块"
make -C "$ROOT/modules" KDIR=/lib/modules/$(uname -r)/build

echo "[2/4] 安装模块文件到 $USER_HOME/npu-clk-fix/"
mkdir -p "$USER_HOME/npu-clk-fix"
cp "$ROOT/modules/npu_clk_fix.ko" "$USER_HOME/npu-clk-fix/"

echo "[3/4] 安装 systemd 服务"
sudo cp "$ROOT/scripts/npu-clk-fix.service" /etc/systemd/system/
sudo systemctl daemon-reload

echo "[4/4] 启动(纠正绑定 + 开时钟 + 钉电源域)"
sudo systemctl enable --now npu-clk-fix.service
sleep 1
systemctl is-active npu-clk-fix && echo "OK: pd_npu 应为 on,clk_npu 应为 en=1"
echo "验证: sudo cat /sys/kernel/debug/pm_genpd/pm_genpd_summary | grep pd_npu"

#!/bin/bash
# rp2_autoprov.sh —— MCU 自动配置守护 (无人值守: 插上即成为能力节点)
#
# 监听并自动处理三类事件:
#   ① 出现 RPI-RP2 优盘 (Pico 在 BOOTSEL)      -> 写入 MicroPython UF2
#   ② 出现 RP2040 MicroPython 串口 (2e8a)      -> 部署 lyco_cap.py + main_server.py (常驻服务)
#   ③ 出现 micro:bit DAPLink 串口 (0d28)       -> 部署 lyco_cap.py + main_idle.py (REPL 驱动)
#
# 日志: /home/radxa/rp2_autoprov.log
set -u
LOG=/home/radxa/rp2_autoprov.log
RP2DIR=/home/radxa/rp2
UF2=/home/radxa/.rp2_uf2                    # 用哪个 UF2 (可改)
MARK=/home/radxa/.rp2_provisioned            # 已配置过的设备记录

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }
[ -f "$UF2" ] || echo "$RP2DIR/RPI_PICO-20241129-v1.24.1.uf2" > "$UF2"
touch "$MARK"
log "=== rp2_autoprov 启动, UF2=$(cat $UF2) ==="

flash_bootsel() {
  local uf2; uf2=$(cat "$UF2")
  for d in $(lsblk -ln -o NAME,LABEL | awk '$2=="RPI-RP2"{print $1}'); do
    log "发现 RPI-RP2 (/dev/$d) -> 写入 $(basename "$uf2")"
    mkdir -p /mnt/rp2
    if mount /dev/"$d" /mnt/rp2 2>/dev/null; then
      cp "$uf2" /mnt/rp2/ && sync
      umount /mnt/rp2
      log "写入完成, 等待 Pico 重启"
      sleep 10
    else
      log "挂载 /dev/$d 失败(可能已被挂载), 跳过"
    fi
  done
}

provision_acm() {
  for p in /dev/ttyACM*; do
    [ -e "$p" ] || continue
    local info uid kind
    info=$(udevadm info -q property -n "$p" 2>/dev/null)
    if echo "$info" | grep -q "ID_VENDOR_ID=2e8a"; then kind=rp2
    elif echo "$info" | grep -q "ID_VENDOR_ID=0d28"; then kind=microbit
    else continue; fi
    # 用 uid 做去重键 (micro:bit 用序列号)
    uid=$(echo "$info" | grep -oE "ID_SERIAL_SHORT=[^ ]+" | head -1 | cut -d= -f2)
    [ -z "$uid" ] && uid=$(basename "$p")
    local key="$kind:$uid"
    grep -q "$key" "$MARK" && continue
    log "发现 $kind 设备 $p (uid=$uid) -> 部署能力层"
    if [ "$kind" = rp2 ]; then
      if python3 /home/radxa/mb.py cap "info" 2>/dev/null | grep -q "OK"; then
        python3 /home/radxa/mb.py put /home/radxa/lyco_cap.py lyco_cap.py >/dev/null 2>&1
        python3 /home/radxa/mb.py put /home/radxa/main_server.py main.py >/dev/null 2>&1
        python3 /home/radxa/mb.py raw "import machine;machine.reset()" >/dev/null 2>&1
        log "RP2040 已部署常驻服务 (main_server.py)"
      else
        # 还没装 MicroPython: 让它进 bootloader, 下一轮由 flash_bootsel 处理
        python3 /home/radxa/mb.py raw "import machine;machine.bootloader()" >/dev/null 2>&1
        log "RP2040 无能力层, 已请求进入 bootloader (等待 RPI-RP2)"
        sleep 5
      fi
    else
      python3 /home/radxa/mb.py put /home/radxa/lyco_cap.py lyco_cap.py >/dev/null 2>&1
      python3 /home/radxa/mb.py put /home/radxa/main_idle.py main.py >/dev/null 2>&1
      log "micro:bit 已部署能力层 (REPL 驱动)"
    fi
    echo "$key" >> "$MARK"
  done
}

while true; do
  flash_bootsel
  provision_acm
  sleep 4
done

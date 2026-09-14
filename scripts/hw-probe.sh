#!/bin/bash
# A7A 硬件能力全量探测（只读，不改变任何配置）
# 用法: sudo bash hw_probe.sh
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

sec() { echo; echo "===== $* ====="; }
has() { command -v "$1" >/dev/null 2>&1; }

sec "板子 / 内核"
tr -d '\0' < /proc/device-tree/model; echo
uname -a
grep PRETTY_NAME /etc/os-release

sec "CPU"
nproc
echo "A76 可用档: $(cat /sys/devices/system/cpu/cpu6/cpufreq/scaling_available_frequencies 2>/dev/null)"
echo "A55 可用档: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies 2>/dev/null)"
for c in 0 6; do
  echo "cpu$c cur=$(cat /sys/devices/system/cpu/cpu$c/cpufreq/scaling_cur_freq 2>/dev/null) gov=$(cat /sys/devices/system/cpu/cpu$c/cpufreq/scaling_governor 2>/dev/null)"
done
echo -n "CPU 特性: "; grep -oE 'asimd|aes|sha1|sha2|crc32|fp16|asimdhp|dotprod|pmull' /proc/cpuinfo | sort -u | tr '\n' ' '; echo

sec "内存"
free -h | head -3

sec "存储"
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT 2>/dev/null

sec "显示 / DRM"
ls /dev/dri/ 2>/dev/null || echo "无 /dev/dri"
for s in /sys/class/drm/*/status; do echo "$(basename $(dirname $s)): $(cat $s 2>/dev/null)"; done 2>/dev/null

sec "GPU"
lsmod | grep -E "pvrsrvkm|powervr" | head -3
for f in /sys/class/devfreq/*gpu*/cur_freq; do echo "$f = $(cat $f 2>/dev/null)"; done 2>/dev/null

sec "VPU / 视频节点"
ls -l /dev/cedar_dev* /dev/video* /dev/media* 2>/dev/null || echo "无"
lsmod | grep -iE "cedar|vin|sunxi" | head -5

sec "NPU"
ls -l /dev/vipcore /dev/galcore 2>/dev/null || echo "无设备节点"
lsmod | grep -iE "galcore|vipcore" | head -3

sec "网络"
ip -br link
has ethtool && ethtool end0 2>/dev/null | grep -E "Speed|Duplex|Link detected"
echo "tx_delay = $(cat /sys/class/net/end0/device/tx_delay 2>/dev/null)"
iw dev 2>/dev/null | grep -E "Interface|type|ssid"
has hciconfig && hciconfig -a 2>/dev/null | head -6

sec "USB"
lsusb 2>/dev/null
lsusb -t 2>/dev/null

sec "PCIe"
ls /sys/bus/pci/devices/ 2>/dev/null | head -5 || echo "无"

sec "I2C"
ls /dev/i2c-* 2>/dev/null || echo "无 i2c 节点"
if has i2cdetect; then
  for b in $(ls /dev/i2c-* 2>/dev/null | sed 's/.*-//'); do
    echo "--- i2c-$b ---"; timeout 5 i2cdetect -y -r $b 2>/dev/null | tail -8
  done
else echo "i2cdetect 未安装"; fi

sec "SPI"
ls /dev/spidev* 2>/dev/null || echo "无 spidev"
ls /sys/class/spi_master/ 2>/dev/null

sec "UART"
ls /dev/ttyS* /dev/ttyAMA* 2>/dev/null || echo "无"

sec "GPIO"
if has gpiodetect; then gpiodetect 2>/dev/null; else echo "gpiodetect 未安装"; fi

sec "PWM"
for p in /sys/class/pwm/pwmchip*/; do echo "$(basename $p) npwm=$(cat $p/npwm 2>/dev/null)"; done 2>/dev/null

sec "ADC / IIO"
for d in /sys/bus/iio/devices/iio:device*/; do echo "$(basename $d): $(cat $d/name 2>/dev/null)"; done 2>/dev/null || echo "无"

sec "音频"
cat /proc/asound/cards 2>/dev/null || echo "无声卡"

sec "RTC"
cat /sys/class/rtc/rtc0/name 2>/dev/null; cat /sys/class/rtc/rtc0/time 2>/dev/null

sec "温度"
paste <(cat /sys/class/thermal/thermal_zone*/type) <(cat /sys/class/thermal/thermal_zone*/temp) 2>/dev/null

sec "风扇 / cooling_device"
for c in /sys/class/thermal/cooling_device*/; do
  echo "$(cat $c/type 2>/dev/null): $(cat $c/cur_state 2>/dev/null)/$(cat $c/max_state 2>/dev/null)"
done

sec "看门狗"
ls -l /dev/watchdog* 2>/dev/null || echo "无"
cat /sys/class/watchdog/watchdog0/identity 2>/dev/null

sec "加密加速"
echo -n "硬件算法: "; grep -oE "^(aes|sha1|sha2|crc32|pmull|ghash)[a-z0-9()-]*" /proc/crypto | sort -u | tr '\n' ' '; echo

sec "电源管理"
cat /sys/power/state 2>/dev/null

echo
echo "PROBE_DONE"

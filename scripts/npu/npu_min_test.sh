#!/bin/sh
# npu_min_test.sh —— NPU 最小 demo 对照测试 (控制变量的原子化验证)
# 目的: 判断 "执行 hang" 是 (a) 时钟/中断层面的通路问题, 还是 (b) 特定大模型的问题
# 变量清单(逐项固定): 同一驱动(vipcore 2.0.3.4) / 同一 runner(vpm_run) / 同一 sample.txt 结构
#                    唯一变量 = 网络规模: 最小算子网络 → yolov5s(5MB)
set -u
D=/home/radxa/npu-sdk/examples/vpm_run/operator/v2
RUN=/home/radxa/npu-bin/usr/bin/vpm_run
export LD_LIBRARY_PATH=/home/radxa/lib:${LD_LIBRARY_PATH:-}

echo "=========== 0. 前置状态 ==========="
ls -la /dev/vipcore 2>/dev/null || echo "/dev/vipcore: 无"
ls -d /sys/bus/platform/drivers/vipcore/3600000.npu 2>/dev/null && echo "vipcore 已绑定设备" || echo "vipcore 未绑定"

echo ""
echo "=========== 1. NPU 时钟状态 ==========="
(echo radxa | sudo -S -k cat /sys/kernel/debug/clk/clk_summary 2>/dev/null) | grep -iE "npu" | head -12

echo ""
echo "=========== 2. NPU 中断计数(前后对比用) ==========="
grep -iE "npu|vipcore" /proc/interrupts | head -3
IRQ1=$(grep -iE "npu|vipcore" /proc/interrupts | awk -F: '{print $1}' | head -1)
echo "监控 IRQ: $IRQ1"

echo ""
echo "=========== 3. 最小算子网络 (对照) ==========="
cd "$D" || exit 1
if [ -f network_binary.nb.orig ]; then
  cp network_binary.nb.orig network_binary.min.nb
  sed 's|./network_binary.nb|./network_binary.min.nb|' sample.txt > sample_min.txt
  sed -i 's/\r$//' sample_min.txt
  # 最小网络输入: 用 224x224x3 uint8 = 150528
  head -c 150528 /dev/urandom > input_min.dat
  sed -i 's|./input_0.dat|./input_min.dat|' sample_min.txt
  cat sample_min.txt
  echo "--- 运行最小网络 ---"
  (echo radxa | sudo -S -k timeout 60 "$RUN" -s sample_min.txt -l 1) 2>&1 | tail -8
else
  echo "未找到 network_binary.nb.orig, 跳过最小网络对照"
fi

echo ""
echo "=========== 4. YOLOv5s (5MB, 已知 hang) ==========="
(echo radxa | sudo -S -k timeout 90 "$RUN" -s sample.txt -l 1) 2>&1 | tail -6

echo ""
echo "=========== 5. 中断计数后 ==========="
grep -iE "npu|vipcore" /proc/interrupts | head -3

echo ""
echo "=========== 6. dmesg (NPU 相关) ==========="
(echo radxa | sudo -S -k dmesg 2>/dev/null | tail -20) | grep -iE "npu|vip|clk" | tail -8

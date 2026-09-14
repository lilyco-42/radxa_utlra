# A7A 硬件配置清单

一张表看全 Radxa Cubie A7A 的**硬件规格 / 当前状态 / 怎么配置 / 怎么验证**。

> 实测环境：Debian 13 trixie · 内核 `6.6.98-4-aw2511` · 8 核 4G
> 更新日期：2026-09-14

---

## 一、核心规格

| 项 | 规格 | 实测确认 |
|---|---|---|
| SoC | Allwinner A733（sun60iw2），12nm | `compatible: radxa,cubie-a7a / allwinner,sun60i-a733` |
| 型号 | A733MX-HN3（全功能版，含 NPU + HDMI） | — |
| CPU | 2× Cortex-A76 @2.0GHz + 6× Cortex-A55 @1.8GHz | `cpu0-5: 0xd05 @1794000` / `cpu6-7: 0xd0b @2002000` |
| MCU | 玄铁 RISC-V E902 @~200MHz（独立实时核） | SoC 内置 |
| GPU | Imagination BXM-4-64 MC1（PowerVR） | `DRIVER=pvrsrvkm` |
| NPU | Vivante VIP9000，3 TOPS @INT8 | `/dev/vipcore` 存在，但**不可用**（见第四节） |
| 内存 | LPDDR5 4GB（板载不可升级） | 实测可用 3.8Gi + zram swap 1.9Gi |
| PMIC | AXP8191（40+ 路电源输出） | `regulator: axp8191-*` |
| 存储 | eMMC 55GB + SPI NOR 8MB + microSD 槽 | `mmcblk1` / `mtdblock0` |

---

## 二、硬件能力清单（状态 + 配置 + 验证）

| 硬件 | 状态 | 怎么启用 / 修 | 验证方式 | 详细文档 |
|---|---|---|---|---|
| **CPU 8 核** | ✅ 可用 | `scripts/deploy-a7a-full-stack.sh --perf`（改 schedutil 并持久化） | `cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor` | [full-stack](a7a-full-stack-deploy.md) |
| **GPU** Vulkan + OpenCL | ✅ 可用 | `scripts/deploy-a7a-full-stack.sh --gpu` | `bash scripts/gpu-check.sh`（14/14） | [full-stack](a7a-full-stack-deploy.md) |
| **VE2** H.264 硬编 | ✅ 可用 | `scripts/deploy-a7a-full-stack.sh --ve2` | `h264-ve2 in.mp4 out.mp4` | [full-stack](a7a-full-stack-deploy.md) |
| **千兆网口** | ⚠️ 有坑 | **必须先改 tx-delay**（见第四节） | `cat /sys/class/net/end0/device/tx_delay` | [router](a7a-router-mode.md) |
| **WiFi 6**（AIC8800） | ✅ 可用 | AP：`nmcli device wifi hotspot` | `iw dev wlan0 info` | [router](a7a-router-mode.md) |
| **蓝牙 5.4**（AIC8800） | ✅ 可用 | 默认已起 | `hciconfig` / `rfkill list` | — |
| **路由器**（PPPoE + AP + NAT） | ✅ 可用 | `scripts/deploy-router.sh --all` | 见[验证判据](a7a-router-mode.md#五验证) | [router](a7a-router-mode.md) |
| **视频自动剪辑** | ✅ 可用 | `scripts/install-tools.sh` + `install-video-service.sh` | `python -m video_tool edit -i in -o out` | README |
| **redroid** Android 14 | ⚠️ 有前置 | 宿主机必须 **cgroup v1** | `ls /sys/fs/cgroup/` 应有 `memory/` | [ROI](hardware-roi.md) |
| **NPU** VIP9000 | ⛔ 封存 | **别启用** —— 会挂死甚至变砖 | — | [三层根因](a733-npu-three-layer-rootcause.md) |

---

## 三、接口清单

### 网络

| 接口 | 设备名 | 状态 | 备注 |
|---|---|---|---|
| 千兆以太网 | `end0` | UP | GMAC，支持 IEEE 1588 PTP；**tx-delay 有坑** |
| Wi-Fi 6 | `wlan0` | DOWN（未连接） | AIC8800，**走 USB 总线**（见第四节） |
| 蓝牙 5.4 | `hci0` | UP RUNNING | AIC8800，设备名 `radxa-cubie-a7a` |

### USB

| 端口 | 总线 | 速率 | 说明 |
|---|---|---|---|
| USB 3.1 Type-A HOST | Bus 002（xhci） | **10Gbps** | SoC USB 3.1 Gen2 |
| USB 2.0 Type-A ×3 | Bus 001（xhci） | 480M | 经 4-port Hub，**AIC8800 就挂在这里** |
| USB Type-C OTG | — | — | 兼供电 |

### 扩展 / 显示

| 接口 | 说明 |
|---|---|
| PCIe 3.0 | **1× FPC，单通道**（接 M.2 扩展板可上 NVMe） |
| 40-pin GPIO | 兼容树莓派，支持 UART / SPI / I2C |
| I2C | `/dev/i2c-0/13/14/15/20` |
| PWM | `pwmchip0/10/20` |
| HDMI 2.0b | 最高 4K@60 |
| MIPI DSI / CSI | 4-lane |
| 3.5mm 耳机口 | 麦克输入 + 立体声输出（可驱动 32Ω） |
| 风扇接口 | 2-pin 1.25mm，PWM 4 档 |
| RTC | `/sys/class/rtc/rtc0`，⚠️ **实测唤醒不可靠** |

### 供电

| 方式 | 说明 |
|---|---|
| USB Type-C 5V | 主要方式 |
| PoE | 需外接 **PoE HAT** |
| GPIO 5V | 引脚 2 / 4 |

---

## 四、已知硬件问题（重要，按严重程度排）

### 1. 千兆网口 RGMII tx-delay 错误 —— **会伪装成"坏网线"**

出厂值 `12` 下**发帧是损坏的**（实测 1200 字节帧丢 42%），症状极具误导性：

- 链路状态正常（1000/full、`LOWER_UP`）
- `tx_errors` 计数为 **0**
- 短 ping 正常
- **但 PPPoE 连发现阶段都过不去**，SSH 密钥交换后卡死，`apt` 卡住

```bash
echo 9 | sudo tee /sys/class/net/end0/device/tx_delay   # 9/10/11 都行
```

⚠️ **数据相关**：重复字节负载 0 丢包，随机数据才暴露 —— 别用 `ping -s` 自测。
⚠️ 这是**运行时值，重启即丢**。

### 2. NPU 执行挂死 —— 三层根因，第三层未解

galcore / VIPLite 两条驱动路线在硬件执行阶段 44 秒超时挂死：

1. 时钟门控 ✅ 已绕过（`scripts/install-npu-clk-fix.sh`）
2. 电源域关闭 ✅ 已绕过
3. 复位 / 安全内存窗口 ⛔ **卡在 boot chain（ATF/U-Boot），内核态无法修复**

而且**即使修好也是负收益**（LPDDR5 带宽天花板，LLM decode 5.02 tok/s vs CPU 18.1）。
→ 详见 [hardware-roi.md](hardware-roi.md) 与 [三层根因](a733-npu-three-layer-rootcause.md)

### 3. WiFi 挂在 USB 2.0 上

`aic8800_fdrv` 在 `Bus 001`（**480M**），而 `Bus 002` 的 10Gbps 空着。
WiFi 6 的速率优势被总线卡死 —— `lsusb -t` 可验。

### 4. 只有 1 个网口

做路由器时入户线占掉 `end0`，下游只能走 WiFi，或加 **USB 3.0 千兆网卡**。

### 5. RTC 唤醒不可靠

`/sys/class/rtc/rtc0` 存在，但实测唤醒功能不稳定 —— 不要依赖它做定时唤醒。

---

## 五、散热与温度

8 个热区：`cpub_thermal_zone`（A76）、`cpul_thermal_zone`（A55）、`gpu_thermal_zone`、
`npu_thermal_zone`、`ddr_thermal_zone`、`skin_zone`、`cpul_idle_zone`、`cpub_idle_zone`。

冷却设备：

| cooling_device | 类型 | 档位 |
|---|---|---|
| cooling_device0 | cpufreq-cpu0（A55） | 0/8 |
| cooling_device1 | cpufreq-cpu6（A76） | 0/11 |
| cooling_device2 | devfreq-3600000.npu | 0/2 |
| cooling_device3 | pwm-fan | 0–4 |

```bash
# 看当前温度
paste <(cat /sys/class/thermal/thermal_zone*/type) <(cat /sys/class/thermal/thermal_zone*/temp)
```

---

## 六、一条命令自检

```bash
# 硬件能力现状（只读，不改动）
sudo ./scripts/deploy-a7a-full-stack.sh --check

# 网络 / 路由器现状
sudo ./scripts/deploy-router.sh --check
```

手写版（不依赖脚本）：

```bash
echo "== SoC ==";        tr -d '\0' < /proc/device-tree/model; uname -r
echo "== CPU ==";        nproc; cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
echo "== 内存 ==";       free -h | head -2
echo "== 网口 ==";       ip -br link; cat /sys/class/net/end0/device/tx_delay 2>/dev/null
echo "== WiFi ==";       iw dev 2>/dev/null | grep -E "Interface|type"
echo "== 温度 ==";       paste <(cat /sys/class/thermal/thermal_zone*/type) <(cat /sys/class/thermal/thermal_zone*/temp)
echo "== GPU ==";        ls /dev/dri/ 2>/dev/null; lsmod | grep -c pvrsrvkm
echo "== NPU ==";        ls -l /dev/vipcore 2>/dev/null || echo "无（封存）"
echo "== cgroup ==";     ls /sys/fs/cgroup/ | grep -E "^(memory|blkio)$" || echo "v2 unified（redroid 不可用）"
```

---

## 七、硬件相关的修正记录

| 日期 | 项 | 内容 |
|---|---|---|
| 2026-09-04 | 全量硬件盘点 | 首次完整实测，产出硬件资产报告 |
| 2026-09-13 | NPU | 三层根因定位，时钟 + 电源域可运行时绕过，复位层卡 boot chain |
| 2026-09-13 | GPU | Vulkan 1.3.277 + OpenCL 3.0 实测通过（14/14） |
| 2026-09-14 | 千兆网口 | tx-delay 出厂值 12 导致发帧损坏；改 9 后 PPPoE 一次拨通 |
| 2026-09-14 | WiFi | 确认 `aic8800_fdrv` 挂 USB 2.0（480M）；驱动声明支持 AP 模式 |
| 2026-09-14 | 路由器 | PPPoE + WiFi AP + NAT 全链路端到端打通（客户端 200/58ms） |

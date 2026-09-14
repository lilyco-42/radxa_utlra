# A7A 硬件配置清单

一张表看全 Radxa Cubie A7A 的**硬件规格 / 当前状态 / 怎么配置 / 怎么验证**。

> 实测环境：Debian 13 trixie · 内核 `6.6.98-4-aw2511` · 8 核 4G
> 探测方式：`tmp_brush/hw_probe.sh` 全量只读探测（198 行输出）+ 针对性复查
> 更新日期：2026-09-14

---

## 一、核心规格

| 项 | 规格 | 实测确认 |
|---|---|---|
| SoC | Allwinner A733（`sun60iw2`），12nm | `compatible: radxa,cubie-a7a / allwinner,sun60i-a733` |
| 型号 | A733MX-HN3（全功能版，含 NPU + HDMI） | — |
| CPU | 2× Cortex-A76 @2.0GHz + 6× Cortex-A55 @1.8GHz | A76 12 档（416M~2002M）/ A55 9 档（416M~1794M） |
| MCU | 玄铁 RISC-V E902 @~200MHz（独立实时核） | SoC 内置 |
| GPU | Imagination BXM-4-64 MC1（PowerVR） | `card1` / `DRIVER=pvrsrvkm` / `gpu@1800000` |
| NPU | Vivante VIP9000，3 TOPS @INT8 | `/dev/galcore` 存在，但**不可用**（见第四节） |
| 内存 | LPDDR5 4GB（板载不可升级） | 可用 3.4Gi + zram swap 1.9Gi |
| PMIC | AXP8191 | i2c-13 @`0x36` |
| 存储 | eMMC 55GB + SPI NOR 8MB + microSD 槽 | `mmcblk1` / `mtdblock0` |
| 调频 | schedutil | cpu0/cpu6 均为 `schedutil` |

**CPU 硬件加速特性**：`aes` `pmull` `sha1` `sha2` `crc32` `asimd` `asimdhp`
→ 支持 AES-GCM、SHA、CRC32 的硬件加速（`/proc/crypto` 有 370 行，含 `cmac(aes)`、
`ecb(aes)`、`ghash`、`ecdh-nist-p*`、`pkcs1pad(rsa,sha256)` 等）

---

## 二、硬件能力清单（状态 + 配置 + 验证）

| 硬件 | 状态 | 怎么启用 / 修 | 验证方式 | 详细文档 |
|---|---|---|---|---|
| **CPU 8 核** | ✅ 可用 | `scripts/deploy-a7a-full-stack.sh --perf` | `cat /sys/devices/system/cpu/cpu6/cpufreq/scaling_governor` | [full-stack](a7a-full-stack-deploy.md) |
| **GPU** Vulkan + OpenCL | ✅ 可用 | `scripts/deploy-a7a-full-stack.sh --gpu` | `bash scripts/gpu-check.sh`（14/14） | [full-stack](a7a-full-stack-deploy.md) |
| **VE2** H.264 硬编 | ✅ 可用 | `scripts/deploy-a7a-full-stack.sh --ve2` | `h264-ve2 in.mp4 out.mp4` | [full-stack](a7a-full-stack-deploy.md) |
| **千兆网口** | ⚠️ 有坑 | **必须先改 tx-delay**（见第四节） | `cat /sys/class/net/end0/device/tx_delay` | [router](a7a-router-mode.md) |
| **WiFi 6**（AIC8800） | ✅ 可用 | AP：`nmcli device wifi hotspot` | `iw dev wlan0 info` | [router](a7a-router-mode.md) |
| **蓝牙 5.4**（AIC8800） | ✅ 可用 | 默认已起（UP RUNNING） | `hciconfig -a` | — |
| **HDMI 2.0b 输出** | ✅ 驱动就绪 | 插上显示器即用 | `cat /sys/class/drm/card0-HDMI-A-1/status` | — |
| **板载音频**（3.5mm） | ✅ 可用 | 默认已起 | `aplay -l`（card1 = `sunxi-ac101b`） | — |
| **HDMI 音频** | ✅ 可用 | 默认已起 | `aplay -l`（card0 = `allwinner-hdmi`） | — |
| **GPIO**（416 线） | ✅ 可用 | `libgpiod` | `gpiodetect` | — |
| **PWM**（30 通道） | ✅ 可用 | sysfs | `cat /sys/class/pwm/pwmchip0/npwm` | — |
| **ADC**（GPADC） | ✅ 可用 | IIO 接口 | `ls /sys/bus/iio/devices/` | — |
| **看门狗** | ✅ 可用 | `/dev/watchdog` | `ls -l /dev/watchdog*` | — |
| **RTC**（HYM8563） | ⚠️ 唤醒不可靠 | i2c-14 @`0x51` | `cat /sys/class/rtc/rtc0/time` | — |
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
| Wi-Fi 6 | `wlan0` | UP | AIC8800，**走 USB 总线**（见第四节） |
| 蓝牙 5.4 | `hci0` | UP RUNNING | AIC8800，BD 地址与 wlan0 **不同**（各自独立 MAC） |

### USB

| 总线 | 速率 | 挂载 |
|---|---|---|
| Bus 001 | 480M（USB 2.0） | Terminus 4-port Hub → **AIC8800**（If0/1 = `aic_btusb`，If2 = `aic8800_fdrv`） |
| Bus 002 | **10000M（USB 3.1）** | **空** |
| Bus 003 | 480M（sunxi-ehci） | 空 |
| Bus 004 | 12M（sunxi-ohci） | 空 |

> ⚠️ WiFi/BT 挂在 **480M** 那条上，而 10Gbps 总线空着 —— WiFi 6 的速率优势被总线卡死。

### 存储

| 设备 | 大小 | 挂载 |
|---|---|---|
| `mmcblk1`（eMMC） | 55G | p1 `/config` 16M · p2 `/boot/efi` 300M · p3 `/` 54.7G |
| `mtdblock0`（SPI NOR） | 8M | U-Boot Bootloader |
| `zram0` | 1.9G | swap（压缩内存） |

### 显示 / 图形

| 节点 | 驱动 | 说明 |
|---|---|---|
| `card0` | `sunxi-drm` | 显示控制器（`/soc@3000000/sunxi-drm`） |
| `card0-HDMI-A-1` | — | HDMI 输出，当前 `disconnected` |
| `card0-Writeback-1` | — | **Writeback 连接器**（可用于录屏/回环） |
| `card1` | `pvrsrvkm` | GPU（`/soc@3000000/gpu@1800000`） |
| `renderD128` | — | GPU 渲染节点 |

### 音频

| 卡号 | 名称 | 用途 |
|---|---|---|
| card0 | `allwinner-hdmi` | HDMI 音频输出 |
| card1 | `sunxi-ac101b` | **板载 3.5mm 耳机口**（codec 在 i2c-15 @`0x3e`） |

### I2C 总线（实测设备表）

| 总线 | 地址 | 设备 | 用途 |
|---|---|---|---|
| `i2c-0` | `0x50` | `24c16` | **板载 EEPROM**（2Kbit） |
| | `0x51`–`0x57` | dummy | 设备树占位（无实体） |
| `i2c-13` | `0x36` | `axp8191` | **PMIC** |
| `i2c-14` | `0x51` | `hym8563` | **RTC** |
| `i2c-15` | `0x3e` | `sunxi-ac101b` | **音频 codec** |
| `i2c-20` | — | `SUNXI HDMI` | **HDMI DDC** 通道 |

### 其它接口

| 接口 | 实测 | 备注 |
|---|---|---|
| GPIO | `gpiochip0 [2000000.pinctrl]` 352 线 + `gpiochip1 [7025000.pinctrl]` 64 线 = **416 线** | 40-pin 排针兼容树莓派 |
| PWM | `pwmchip0/10/20`，各 10 通道 = **30 通道** | sysfs 控制 |
| ADC | `iio:device0: 2521000.gpadc` | IIO 接口 |
| SPI | `spi0` 存在，**无 `/dev/spidev*`** | 用户态未暴露，需加 overlay |
| UART | **无 `/dev/ttyS*`** | SoC 有 9 个 UART，均未暴露为 tty |
| PCIe | `0000:00:00.0`，vendor `0x1f6d` / device `0xabcd` / class `0x060400` | **只是 Root Complex 桥**，FPC 上未插设备 |
| 看门狗 | `/dev/watchdog` + `/dev/watchdog0` | — |
| RTC | `/sys/class/rtc/rtc0`（HYM8563） | ⚠️ 唤醒不可靠 |

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

### 4. 只有 1 个网口

做路由器时入户线占掉 `end0`，下游只能走 WiFi，或加 **USB 3.0 千兆网卡**。

### 5. RTC 唤醒不可靠

`/sys/class/rtc/rtc0`（HYM8563）存在，但实测唤醒功能不稳定 —— 不要依赖它做定时唤醒。

### 6. 风扇默认全速常转

`pwm-fan` 的 `cur_state = 4/4`（满档），而当时各热区只有 31~37°C，
**远低于** trip point（60°C）。说明风扇没走温控，是常开状态 —— 噪音与功耗都白费。

```bash
# 手动降档（临时）
echo 1 | sudo tee /sys/class/thermal/cooling_device2/cur_state
```

---

## 五、散热与温度

### 8 个热区与触发阈值

| 热区 | 当前温度 | trip_point_0 |
|---|---|---|
| `cpub_thermal_zone`（A76） | 37.1°C | 60°C |
| `cpul_thermal_zone`（A55） | 36.1°C | 60°C |
| `gpu_thermal_zone` | 36.0°C | 60°C |
| `ddr_thermal_zone` | 37.0°C | 110°C |
| `npu_thermal_zone` | 36.9°C | 110°C |
| `cpul_idle_zone` | 36.7°C | 90°C |
| `cpub_idle_zone` | 36.1°C | 90°C |
| `skin_zone`（表壳） | 31.2°C | 50°C |

```bash
# 看当前温度（单位 0.001°C）
paste <(cat /sys/class/thermal/thermal_zone*/type) <(cat /sys/class/thermal/thermal_zone*/temp)
```

### cooling_device 现状

| cooling_device | 类型 | cur/max |
|---|---|---|
| cooling_device0 | `cpufreq-cpu0`（A55） | 0/8 |
| cooling_device1 | `cpufreq-cpu6`（A76） | 11/11 |
| cooling_device2 | `pwm-fan` | **4/4（全速）** |

> 注：早期硬件报告里还有 `devfreq-3600000.npu`，当前内核下**已不在列表**（NPU devfreq 未注册）。

### CPU 调频健康度（开机 4 小时的 `time_in_state`）

| 核 | 最低频停留 | 最高频停留 | 结论 |
|---|---|---|---|
| A76（cpu6） | 416MHz：1516s | 2002MHz：2.2s | **能上最高频**，空闲时降频 —— 正常 |
| A55（cpu0） | 416MHz：1402s | 1794MHz：99s | 正常 |

→ 看到 `cpu6 = 416000` 别慌，那是空闲态；schedutil 有负载会升上去。

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
echo "== CPU ==";        nproc; cat /sys/devices/system/cpu/cpu6/cpufreq/scaling_governor
echo "== 内存 ==";       free -h | head -2
echo "== 存储 ==";       lsblk -o NAME,SIZE,TYPE,MOUNTPOINT
echo "== 网口 ==";       ip -br link; cat /sys/class/net/end0/device/tx_delay 2>/dev/null
echo "== WiFi ==";       iw dev 2>/dev/null | grep -E "Interface|type"
echo "== 显示 ==";       ls /sys/class/drm/; cat /sys/class/drm/card0-HDMI-A-1/status
echo "== 音频 ==";       aplay -l 2>/dev/null | grep ^card
echo "== I2C ==";        ls /dev/i2c-*
echo "== GPIO/PWM ==";   gpiodetect 2>/dev/null; ls /sys/class/pwm/
echo "== 温度 ==";       paste <(cat /sys/class/thermal/thermal_zone*/type) <(cat /sys/class/thermal/thermal_zone*/temp)
echo "== 风扇 ==";       cat /sys/class/thermal/cooling_device2/cur_state
echo "== GPU ==";        ls /dev/dri/ 2>/dev/null; lsmod | grep -c pvrsrvkm
echo "== NPU ==";        ls -l /dev/galcore /dev/vipcore 2>/dev/null || echo "无（封存）"
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
| 2026-09-14 | **全量探测** | 补齐 I2C 设备表 / DRM 双卡 / 双声卡 / GPIO 416 线 / PWM 30 通道 / 加密引擎 / 看门狗；发现风扇默认全速、SPI 与 UART 未暴露 |

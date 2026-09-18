# A7A 坏卡根因排查清单

> 更新于 2026-09-18 深夜（第二版）。
> 第一版写于"板子完全不亮"时，方向是**供电**；现在有了完整启动日志，
> 方向修正为 **SD 控制器电压切换 + 供电** 双线并查。
>
> 用户原话（本清单的存在理由）：
> **「我都换了 两张卡了 这样下去不排查问题 下面的卡插入 也会这样」**

---

## 一、先明确一件事：这不是"再换一张卡"能解决的问题

| 时间 | 事件 |
|---|---|
| 9/17 | 第一张卡**烧烫**，拔下来无法识别（`CM_PROB_PHANTOM`），数据全丢 |
| 9/18 | 第二张卡（新卡）`Inode 521217 seems to contain garbage` → `UNEXPECTED INCONSISTENCY` |
| 9/18 | 手动 `fsck -y` 修好后，进系统又烂：`bg 64: bad block bitmap checksum` → 只读 |
| 9/18 | 板子一度完全不亮 |

**两张卡、两种不同的坏法（一张热损坏、一张元数据损坏），都发生在同一块板子上。**
如果是卡的批次问题，不会坏得这么不一样。这一致指向板子侧。

---

## 二、根因取证结论（`card_forensics.py` 输出）

跑法：

```bash
cd D:\Code\mc\a7a-rebuild\tools
C:/Users/liuqi/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe ^
    card_forensics.py --log ..\logs\a7a-boot-2026-09-18.log --offline
```

结论：**`CONTROLLER_FAULT` —— 板子的问题，不是卡的问题。**

### 头号证据（SD 控制器电压切换失败）

| 日志原文 | 含义 |
|---|---|
| `Card did not respond to voltage select! : -110` | SD 3.0 要从 3.3V 切到 1.8V 跑高速，**这一步失败了** |
| `manual set ocr` | 协商失败后驱动退回硬编码电压，说明电压流程没走通 |
| `Cann't get pin bias hs pinstate` | **HS（高速）模式的引脚偏置配置拿不到** |
| `Cann't get uart0 pinstate` | 同一批 pinmux 描述缺失，佐证设备树与实际硬件不符 |

> **为什么这会导致换卡也白换**：SD 卡在 3.3V 下低速读写是好的（所以能启动、能 fsck 通过），
> 一旦内核切到 1.8V 高速模式批量写日志/写队列文件，电压切换没成功 →
> **写进去的字节不可靠** → ext4 元数据（block bitmap）校验和错 → 内核发现损坏 → 强制只读。
> 这个过程和卡是谁家的、容量多大完全无关。

### 二号证据（供电/PMIC 电压域缺失）

| 日志原文 | 含义 |
|---|---|
| `OPP not supported by regulators`（40+ 次） | DTB 里的频率-电压表在板子上找不到对应稳压器 |
| `failed to find dram_clk` | DRAM 时钟配置缺失 |
| `pdtest ... failed with error -110` | 掉电测试超时，电源域不响应 |
| `Speed change timeout` | PCIe 速率切换超时（同批电压问题的旁证） |

### 三号证据（板子身份错位 —— 这可能是根因的**源头**）

```
U-Boot 报：  Model: Radxa A7S     SN: RS501-D4S8R42W28     BOM: V1.10B
实际加载：   sun60i-a733-cubie-a7a.dtb
```

**板子自称 A7S，却用 A7A 的设备树在驱动。**
A7A 和 A7S 的 SD 控制器、稳压器、pinmux 走线不一定一样 ——
这正好解释了为什么"pin bias hs pinstate 拿不到"和"电压切换失败"会同时出现。

另外 10 个 MAC 地址全为 `00:00:00:00:00:00`，说明 EEPROM 里没有烧板级信息。

---

## 三、按顺序做这几步（每步打勾）

### ☐ 1. 确认电源适配器规格（官方要求：USB Type-C 5V）

A7A 官方规格：

| 项目 | 官方值 |
|---|---|
| 供电 | **USB Type-C，5V** |
| 启动存储 | 板载 8MB SPI NOR Flash |
| 系统存储 | microSD / eMMC 模块 / UFS 模块 |
| 推荐卡 | **32GB 以上 microSD** |

把适配器翻过来抄下：

```
输出电压: ______ V
输出电流: ______ A
接口: USB-C / DC 圆孔
```

| 情况 | 判定 |
|---|---|
| 5V / ≥3A，USB-C | ✅ 规格正确 |
| 5V / <5A 且接 USB-C | ⚠️ 可能不足，见下方功耗说明 |
| **12V / 9V 任何电流** | 🚫 **绝对不能插**，会烧板子 |
| 圆孔 DC 适配器 | ⚠️ 确认板子确实有 DC 口且内径匹配（5.5×2.1 vs 5.5×2.5 不通用） |

> **为什么 5V 也常常不够**：5V 供电时，芯片内部要靠 DCDC 把 5V 降压出
> 3.3V、1.8V、1.1V 等一堆电压域。输入电流不够 → 各电压域建立不起来 →
> 正好表现为 `OPP not supported by regulators` 和电压切换失败。
> **建议用支持 PD 5V/3A 档的氮化镓充电器 + 质量好的线。**

### ☐ 2. 换电源试一次（但只试一次）

不要在同一块可疑电源上反复上电 —— **每一次上电都可能再毁一张卡。**

用**品牌氮化镓充电器（65W 以上）+ 好线**，或标着 5V/3A 的 DC 适配器。

### ☐ 3. 检查 DTB 与板子型号是否匹配 ★ 这步最可能是真凶

现在板子加载的是 `sun60i-a733-cubie-a7a.dtb`，但板子自称 A7S。

要确认的事：

- 板子正面丝印到底是 `A7A` 还是 `A7S`？
- 如果是 A7S，`/boot/extlinux/extlinux.conf` 里的 `fdtdir` / `fdtfile`
  是不是指向了 A7A 的 dtb？
- **修法**：把 `fdtfile` 改成 A7S 对应的 dtb 文件名（不改 U-Boot，
  只改 `/boot` 分区里的配置文件 —— 符合"不要动 uboot"的约束）。

> ⚠️ 这一步改的是**根文件系统/boot 分区里的文本文件**，
> 不是 U-Boot 环境变量，也不是 SPL/BL31。不碰你的 U-Boot。

### ☐ 4. 确认 SD 卡状态（拿到 shell 后，全部只读）

```bash
cat /sys/block/mmcblk1/device/name        # 卡是谁家的
cat /sys/block/mmcblk1/device/life_time   # 寿命寄存器 A/B，看磨损
cat /sys/block/mmcblk1/ro                 # 是否已被标记只读
dmesg | grep -iE 'mmc|smc|sunxi_mmc'      # 本次启动有没有控制器报错
dmesg | grep -iE 'EXT4-fs|I/O error'      # 有没有文件系统错误
```

或者一条命令搞定：

```bash
C:/Users/liuqi/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe ^
    card_forensics.py --serial COM3
```

### ☐ 5. 万用表量电压（有表的话，最有说服力）

找板子上的 `5V` / `GND` 测试点或 5V 滤波电容：

```
黑表笔 → GND      红表笔 → 5V 测试点
读数应为 4.9 ~ 5.2 V
```

| 读数 | 结论 |
|---|---|
| 5V 正常但灯不亮 | 板子电源电路/保险丝问题 |
| 明显低于 4.8V | 适配器功率不足或线损太大 |
| 0V | 适配器没输出 / 插头没接触 |

### ☐ 6. 卡本身（排除项，不是主攻方向）

如果 `life_time` 显示 `0x01` 或更高，说明卡确实磨损严重。
但在**控制器电压切换失败**的前提下，任何卡的寿命都会被异常加速。
所以这步只用来排除，不要指望换卡解决。

```bash
# Linux 侧检查是否扩容卡/坏块卡
sudo f3probe --destructive --time-ops /dev/sdX
# Windows 上可用 H2testw
```

---

## 四、现在就能做的事（不需要板子活）

### A. 看取证报告

```bash
cd D:\Code\mc\a7a-rebuild\tools
C:/Users/liuqi/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe ^
    card_forensics.py --log ..\logs\a7a-boot-2026-09-18.log --offline
```

### B. 想办法进 shell 取证

当前板子会停在 initramfs（rootfs 不一致）。两个选择：

**选择 1 —— 修 rootfs 继续启动**（会再被写坏，但能拿到 shell 取证）

```bash
C:/Users/liuqi/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe ^
    initramfs_fixer.py --port COM3 --baud 115200
```

**选择 2 —— 用救援模式进 shell，只读取证，不让系统写盘**（**推荐**）

在 U-Boot 里**只设置不保存**（`setenv` 不 `saveenv`，不碰你的 U-Boot 环境）：

```
setenv bootargs root=UUID=ce788441-061f-4c4b-a90b-feabdcd8790c \
  console=ttyAS0,115200n8 rootwait ro systemd.unit=rescue.target
boot
```

> 带 `ro`（只读挂载）+ `rescue.target`（最小系统）→ 系统不会大量写盘 →
> **不会再次损坏文件系统**，同时给你一个 shell 去跑上面的只读取证命令。
> 因为不 `saveenv`，**断电即恢复**，你的 U-Boot 环境一个字节都没变。

---

## 五、板子救回来之后：立刻做这四件事

1. **核对并修正 `fdtfile`** —— 这是本清单里最可能真正解决问题的一步
2. **换个好电源** —— 优先级第二，但同样重要
3. **配备份**：`~/vp/queue/`、`config.yaml`、`~/biliup/cookies.json`
   定期 `rsync` 到电脑或私有 git 仓库
4. **别带电插拔** SD 卡 / 串口线 / 网线

> 你的关键配置已经在 `D:\Code\mc\a7a-rebuild\` 里备好了一份，
> 下次再出事，10 分钟就能恢复，不用重新踩坑。

# 一次健康启动日志的完整解读（A7A / 2026-09-18）

> **为什么要写这份文档**
>
> 2026-09-18 这台 A7A 重刷后成功启动。日志里出现了 **178 行错误/告警**，
> 但**真正的故障只有 2 个**，而且这 2 个是同一个根因。
>
> 如果不做"报错 → 实际后果"的对应，会误判成"板子到处都是问题"，
> 进而在内核/设备树层面乱改 —— 那是把一个健康系统改坏的最快路径。
>
> 这份文档记录**每个报错的验证方法与实测结论**，供下次复用。

## 结论速览

| 分类 | 数量 | 处理 |
|---|---|---|
| 真故障（有实际后果） | 2 个单元，1 个根因 | `board-fix.sh --apply` 自动修 |
| 设计噪音（DT 声明与实际硬件不符） | ~70 行 | 不动 |
| 环境噪音（板子没焊的器件） | ~12 行 | 不动 |
| 上游 BSP 质量项（用户态改不了） | ~5 项 | 记录，等上游 |

## 一、真故障：`lightdm.service` failed（连带 `plymouth-quit`）

### 症状

```
● lightdm.service       loaded failed failed Light Display Manager
● plymouth-quit.service loaded failed failed Terminate Plymouth Boot Screen
```

### 验证方法

```bash
dpkg -l lightdm                       # 空输出 → 包根本没装
systemctl show lightdm -p ExecStart   # path=/usr/sbin/lightdm
systemctl show lightdm -p UnitFileState  # enabled
ls -l /usr/sbin/lightdm               # No such file or directory
```

### 根因

**镜像打包残留。** `lightdm.service` 由发行版提供（在
`/usr/lib/systemd/system/`），状态是 `enabled`，被 `graphical.target`
和 `hdmi-toggle-once.service` 拉起；但 `lightdm` 软件包**没有安装**。

于是：

```
Failed at step EXEC spawning /usr/sbin/lightdm: No such file or directory
Main process exited, code=exited, status=203/EXEC
Scheduled restart job, restart counter is at 4
...
Start request repeated too quickly.        ← 撞上启动限流
```

`plymouth-quit.service` 属于 `multi-user.target`，被 `graphical` 的反复切换
连带触发，撞上 `start-limit-hit` 一起变成 failed。

**两个 failed 单元，一个根因。**

### 处理

```bash
sudo systemctl mask lightdm.service          # 假装有包 → 直接遮掉
sudo systemctl set-default multi-user.target # CLI 镜像不该用 graphical
sudo systemctl reset-failed plymouth-quit.service
```

撤销：`systemctl unmask lightdm.service` + `systemctl set-default graphical.target`

## 二、设计噪音：`mmc@4022000` 的 RTO 风暴（~33 行）

### 症状

```
sunxi:sunxi_mmc_host-4022000.sdmmc:[ERR]: manual set ocr
sunxi:sunxi_mmc_host-4022000.sdmmc:[WARN]: Cann't get uart0 pinstate
sunxi:sunxi_mmc_host-4022000.sdmmc:[WARN]: Cann't get pin bias hs pinstate
sunxi:sunxi_mmc_host-4022000.sdmmc:[ERR]: smc 0 p2 err, cmd 1, RTO !!   ← ×33
sunxi:sunxi_mmc_host-4022000.sdmmc:[INFO]: retry:set phase failed or over retry times
mmc0: Failed to initialize a non-removable card
```

### 这是真的硬件错误吗？不是

判定依据：

1. **`detmode:alway in(non removable)`** —— 设备树把 mmc0 声明为**不可插拔**。
2. **实际槽位是空的** —— `/sys/class/mmc_host/` 里 `mmc0` 存在，但没有
   `mmc0:XXXX` 子节点（对比 `mmc1:0001` 是那张 SD 卡）。
3. **`cmd 1` 就是 CMD1（SEND_OP_COND）** —— 内核认为卡"应该在"，
   于是发电让卡上报工作电压。
4. **空槽位没有响应** → RTO（Response TimeOut）→ 换采样相位重发 →
   相位 `0x80 → 0x282 → 0x484 …` 一路加到 `0x3ebe` → 放弃。
5. **整个循环只持续 0.66 秒**（`0.329440` → `0.688361`），随后控制器直接下电。

### 对系统的影响：零

- 没有任何写入落到你的 SD 卡上（SD 卡在 `mmc1`，是另一个控制器）
- 0.66 秒后自行放弃，不阻塞启动
- **系统能正常启动，恰恰是因为它放弃了**

### 为什么不建议改设备树

删掉 `mmc@4022000` 或改 `status = "disabled"` 需要重新编译 DTB 并写入
`/boot`。收益只是日志干净一点，风险是改坏启动路径。**不值得。**

## 三、设计噪音：`OPP not supported by regulators`（37 行）

### 症状

```
core: _opp_supported_by_regulators: OPP minuV: 1150000 maxuV: 1150000, not supported by regulator
cpu cpu0: _opp_add: OPP not supported by regulators (1104000000)
```

### 验证：CPU 调频是否真的坏了？

```bash
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies
# 416000 780000 1014000 1196000 1404000 1508000 1612000 1716000 1794000

cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
# ondemand
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver
# cpufreq-dt
```

**9 档频率全部可用，`ondemand` 正在工作。** 结论：这 37 行是
regulator 框架在逐条"尝试注册 OPP"时打的失败日志，实际回退路径工作正常。

**判据：不看报错，看 `scaling_available_frequencies` 有没有内容。**

## 四、环境噪音：板子上没焊的器件

| 报错 | 为什么是噪音 |
|---|---|
| `sunxi-ufs-pltfm 4520000.ufs: link startup failed 1` | A7A 没有 UFS 芯片，`ufshcd_populate_vreg` 找不到 vdd-hba-supply 是必然 |
| `sunxi_sid: Fail to read 'dvfs2_ori' in dts` | 只影响细粒度调压（VF 表）。NPU devfreq 正常读到 3 档频率（492/852/1008 MHz），VE 回退默认 624 MHz |
| `sunxi:ccu_ddr-2002000.clk_ddr: failed to find dram_clk` | 没有 DDR 调频需求（无 `dram` devfreq 设备） |
| `sunxi:pin-2000000.pinctrl: unknown pin` | 设备树里声明了但未被使用的引脚 |

## 五、上游 BSP 质量项（用户态改不了，只能记录）

### 5.1 两个 NOTICE banner

```
** regulator-virtual-consumer is only for testing and debugging.  **
** Do not use it in a production kernel.                          **
```

```
** trace_printk() being used. Allocating extra memory.  **
** This means that this is a DEBUG kernel and it is              **
** unsafe for production use.                                    **
```

`CONFIG_DEBUG_KERNEL=y`、`CONFIG_REGULATOR_VIRTUAL_CONSUMER=y` 是**编译期**
决定的，用户态无法关闭。

### 5.2 但 `trace_printk()` 真的有开销吗？—— 实测：没有

```bash
sudo grep entries-written /sys/kernel/debug/tracing/trace
# entries-in-buffer/entries-written: 0/0   #P:8
```

**ring buffer 是空的（12 行输出全是表头）。**
说明虽然有 `trace_printk()` 被编译进去，但**没有任何代码路径在调用它**。
所以这条警告当前是零开销的，不需要处理。

**判据：不要看到 "DEBUG kernel" 就慌，去查 ring buffer 有没有真的写入。**

### 5.3 内核 taint

```
cat /proc/sys/kernel/tainted   # 4096
```

`4096` = bit12 = `TAINT_OOT_MODULE`，来自 `pvrsrvkm`（PowerVR GPU 驱动，
1.4 MB，out-of-tree）。这是正常的：A733 的 GPU 驱动没有进主线内核。

## 六、存储健康度：这次是好的

这是与上次事故**最关键的区别**。逐项对照：

| 指标 | 上次（损坏中） | 这次（健康） |
|---|---|---|
| ext4 块数 | 每次启动 **+2049** | 稳定 `16299259` |
| 文件系统状态 | `Recovering journal` → 强制只读 | `clean` |
| `dbus.service` | FAILED | `[  OK  ]` |
| `systemd-logind.service` | FAILED | `[  OK  ]` |
| `ssh.service` | 从未启动 | `[  OK  ]` |
| 拿到 shell | ❌ 键盘死 | ✅ `radxa@radxa-cubie-a7a:~$` |

### 但发现了一个盲区

```
systemd-fsck-root.service - File System Check on Root Device skipped,
  unmet condition check ConditionPathIsReadWrite=!/
```

**根分区启动时的 fsck 被跳过了。** 因为根是 `rw` 挂载，
`ConditionPathIsReadWrite=/` 这个条件永远不成立。

后果：坏块**静默累积**。上次事故就是这样累积了 2049+ 个坏块，
一直到系统彻底起不来才被发现 —— **全程没有任何一条报错。**

### 处理

```bash
sudo ./tools/board-fix.sh --apply --harden --only=fsck
```

会做两件事：
- `tune2fs -c 30 -i 30d` —— 每 30 次挂载或 30 天检查一次
- `tune2fs -e remount-ro` —— 出错先转只读，避免"边修边坏"

## 七、可复现的检查清单

板子起来后，按这个顺序查（`board-fix.sh --check` 已全部自动化）：

```bash
# 1. 失败的单元 —— 真的失败了吗？
systemctl --failed
systemctl show <unit> -p Result -p ExecStart   # 看可执行文件在不在

# 2. 存储健康 —— 不要只数错误行数
findmnt -nro SOURCE,OPTIONS /
sudo dumpe2fs -h /dev/mmcblk1p3 | grep -E 'state|Block count|Mount count'
dmesg | grep -iE 'mmc.*(err|fail|timeout)' | grep -v 'RTO'   # 滤掉噪音

# 3. fsck 到底有没有在跑（最容易被忽略）
journalctl -b | grep 'fsck-root'

# 4. 报错有没有实际后果 —— 逐项验证，不要凭感觉
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies
sudo grep entries-written /sys/kernel/debug/tracing/trace
ls /sys/class/mmc_host/          # 有没有 mmc0:XXXX 子节点

# 5. 加速器在不在
ls /dev/vipcore /dev/cedar_dev_ve2 /dev/dri/
```

## 相关文档

- [反复烧卡根因排查报告](./a7a-sd-card-corruption.md)
- [供电与存储排查清单](./a7a-power-and-storage-checklist.md)
- [重建手册](./a7a-rebuild-manual.md)

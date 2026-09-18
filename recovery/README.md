# recovery/ —— A7A 故障恢复与灾后重建工具包

> 这套东西不是"顺手写的脚本"，而是**一次真实的重大事故**（2026-09-17~18 连续两张 SD 卡损坏、
> 板子一度完全不亮）里，逐个踩坑、逐个固化下来的可用工具。
>
> 用法优先级：**先读 `docs/troubleshooting/`，再用 `tools/`。**

## 什么时候会用到这里

| 你遇到的情况 | 看哪里 |
|---|---|
| **板子活着，想一键体检 / 修能修的错误** | **`tools/board-remote.py`（Windows 侧一条命令）** |
| Windows 里读卡器"插了但系统完全看不见" | `docs/troubleshooting/a7a-sd-card-corruption.md` §二 + `scripts/flash-a7a.ps1 -Check` |
| 要重刷系统 | `scripts/flash-a7a.ps1`（Windows）/ `scripts/flash-sd.sh`（Linux/WSL） |
| 怀疑卡是假的 / 容量不对 | `tools/sd_verify.py` |
| 板子起不来、没有 shell | `tools/probe_a7a.py` → `tools/serial_repair.py` |
| 串口收不到东西（或收到一堆乱码） | `tools/baud_scan.py` |
| 卡在 `(initramfs)` 提示符 | `tools/initramfs_fixer.py` |
| 想知道"这次坏卡到底是卡的问题还是板子的问题" | `tools/card_forensics.py` |
| 系统开始烂了，想先把数据捞出来 | `tools/sd_ext4_rescue.py`（Windows 侧只读抢救） |
| 板子活着但网络不通，想从串口拿 shell | `tools/serial_shell.py` |
| 想知道板子现在到底活不活 | `tools/probe_a7a.py`（四通道并行探测） |
| 不知道板子现在 IP 是多少 | `tools/lan_sweep.py` / `board-remote.py --find` |
| 重刷完要重建"生成 + 发布"全链路 | `docs/troubleshooting/a7a-rebuild-manual.md` + `recovery/vp/` |

## 一键体检 / 修复（最常用）

板子能启动、SSH 能连上时，**不用记一堆诊断命令**，一条就够：

```bash
# Windows 侧（自动扫网找板子 → 装公钥 → 推脚本 → 体检）
python tools/board-remote.py --find

# 板子侧（或已经 ssh 进去之后）
sudo ./tools/board-fix.sh --check          # 只体检，不改任何东西
sudo ./tools/board-fix.sh --apply          # 修「安全类」问题
sudo ./tools/board-fix.sh --apply --harden # 额外做写路径加固
./tools/board-fix.sh --list                # 看看有哪些可修项
```

`board-fix.sh` 的修复项分两个风险等级，**默认只做「安全」类**：

| 风险 | 含义 | 项 |
|---|---|---|
| 安全 | 任何情况都建议修，不改变使用习惯 | `lightdm` `graphical` `startlimit` |
| 加固 | 会改变系统行为，需自行判断 | `atime` `fsck` `writeback` `journald` `swap` |

它**绝不触碰** U-Boot / 分区表 / 内核 / 设备树 / 已安装软件包，每处改动前都备份到
`/root/board-fix-backup/<时间戳>/` 并打印撤销方法。

## 目录结构

```
recovery/
├── README.md                   ← 你在这里
├── tools/                      诊断与救援脚本（Python / Shell）
│   ├── board-remote.py         ★ Windows 侧一键入口：扫网→装公钥→推送→执行
│   ├── board-fix.sh            ★ 板子侧安全修复器（--check/--apply/--harden）
│   ├── device_profile.py       ★ 设备抽象层：自动识别厂商/存储/串口/加速器
│   ├── card_forensics.py       ★ 坏卡根因取证：判定 "卡的问题" 还是 "板子的问题"
│   ├── sd_verify.py            ★ 验卡：容量/速度/CID，识别扩容假卡
│   ├── sd_ext4_rescue.py       ★ Windows 侧只读 ext4 抢救（不写卡、不修盘）
│   ├── initramfs_fixer.py        卡在 (initramfs) 时自动 fsck
│   ├── probe_a7a.py            ★ 四通道探测：串口 + ICMP + TCP22 + USB
│   ├── baud_scan.py            ★ 波特率自动扫描（解决"几万字节读不出字"）
│   ├── serial_repair.py          串口精细控制 --watch/--check-only/--fix
│   ├── serial_shell.py           串口 TX 探测，试着拿 shell
│   ├── serial_rescue_guide.py    生成救援命令（只 setenv，绝不 saveenv）
│   ├── uboot_rescue.py           U-Boot 提示符下绕过坏分区手动引导
│   ├── rescue_a7a.py             全自动抢救流水线
│   ├── usb_watch.py            ★ Windows USB 插拔实时监听 + 三段判定
│   ├── lan_sweep.py            ★ 局域网设备发现（跨平台，自动排除代理 TUN 网段）
│   └── _selftest_card_forensics.py
├── vp/                         "生成 + 发布" 全链路（旧卡烧毁后重建用）
│   ├── config.example.yaml     配置模板（真实 config.yaml 已 gitignore）
│   ├── run_pipeline.sh         流水线（含全部板上修复）
│   ├── process_queue.py        队列处理器（flock 单实例锁）
│   ├── publish.py              多平台发布器（success-watch + 进程组收尾）
│   ├── gen_script.py / gen_video*.mjs / pick_model.py
│   ├── patch_cedar_v4.py       Cedar 硬编补丁（15s 超时 + pgrep 守卫）
│   ├── templates/              渲染模板
│   ├── topics.txt
│   └── queue/                  任务队列 + 状态（62 条起步）
└── systemd/
    ├── vp-pipeline.service     Type=oneshot, MemoryMax=1G, Nice=5
    └── vp-pipeline.timer       每天 02/08/14/20 点
```

## 三个跨项目通用的判据（最值钱的部分）

### 1. 「完全静默」是最强的诊断信号

插了设备，系统**连一个枚举事件都没有**（不是"认到但没盘"，是**完全没有反应**）——
这几乎必然意味着**驱动栈把设备拦截了**，不是线坏了、不是设备坏了。

Windows 上最常见的元凶是 `usbipd` 把设备 `bind --force` 给了 USB/IP 协议栈。
一条命令坐实：`usbipd list`，看目标设备是不是 `Shared (forced)`。

判据区分：
- **完全无反应** → 驱动栈拦截
- **认到 USB 但没磁盘** → 卡槽接触 / 卡本身
- **磁盘出现但 Offline** → `Set-Disk -IsOffline $false`
- **磁盘出现且 Online** → 正常

### 2. 收到几万字节 ≠ 收到数据

FTDI 串口在 RX 悬空、对端不上电时会采到大量 NUL/空白，
表现为"51,828 字节但零可读文本"。

**必须按可打印字符占比过滤**，并用 `baud_scan.py` 逐档验证。
看到字节数就以为板子在说话，会白白折腾一轮。

### 3. 「读写不对称」是硬件故障的照妖镜

如果日志里**只有写错误、没有读错误**，那基本可以排除"卡坏了"（卡坏了读写都会错），
指向**写路径的硬件问题**（电压切换、时序、供电）。

`card_forensics.py` 就是按这个判据给结论的。实测输出：

```
写错误行数：1 / 读错误行数：0
▸ 只有写错误，没有读错误 ← 强烈支持「电压切换故障」
```

### 4. 报错多 ≠ 故障多：先看「有没有实际后果」再决定修不修

**这是 2026-09-18 一次完整启动日志给出的最重要的教训。**
一份健康的启动日志里可以有 **178 行错误/告警**，但真故障只有 2 个。

判定方法：**不要数错误行数，去查这个报错有没有导致功能失效。**

| 看着吓人 | 实际查法 | 实测结论 |
|---|---|---|
| `OPP not supported by regulators` ×37 | `cat .../scaling_available_frequencies` | 9 档频率可用、`ondemand` 生效 → 噪音 |
| `smc 0 p2 err, cmd 1, RTO !!` ×33 | 看它 0.6 秒后是不是自己放弃了 | 空槽位重试，自行放弃 → 噪音 |
| `This means that this is a DEBUG kernel` | `grep entries-written /sys/kernel/debug/tracing/trace` | 缓冲为空 → 零开销 |
| `UFS link startup failed` | 板子上有没有 UFS 芯片 | 没焊 → 预期行为 |
| `dvfs2_ori` 缺失 | NPU/VE 的 devfreq 能不能读频率 | 能读、三档可调 → 只影响细粒度调压 |

反例（**真故障，会阻塞系统**）：`lightdm.service` failed —— 一查
`systemctl show lightdm -p ExecStart` 发现可执行文件根本不存在，
重试 5 次撞上 `start-limit-hit`，**连带把 `plymouth-quit` 也拖成 failed**。
两个 failed 单元，一个根因。

### 5. 最危险的不是报错，是「检查被静默跳过」

```
systemd-fsck-root.service - File System Check on Root Device skipped,
  unmet condition check ConditionPathIsReadWrite=!/
```

根分区是可写的，`fsck` 的触发条件因此**永远不成立**。
结果是坏块静默累积 —— 上次事故里每个启动周期稳定增加 **2049 个坏块**，
一直没人发现，直到系统彻底起不来。

**这类问题不会报错，只会沉默。** 定期跑 `board-fix.sh --check` 就是为了盯它
（脚本会记住块数，下次对比；块数增长 = 文件系统在恶化）。

## 硬约束：不要碰 U-Boot

这条是用户的明确要求，也是所有工具的设计边界：

- ❌ 不 `saveenv`
- ❌ 不 `mmc write`
- ❌ 不改分区表
- ❌ 不改 U-Boot 环境变量
- ✅ 只允许 `setenv`（断电即恢复，不落盘）
- ✅ 只操作 rootfs / SD 卡内容

`tools/serial_rescue_guide.py` 里所有生成的命令都遵守这条，且**无法**执行 `saveenv`。

## 环境要求

```bash
# 诊断工具（板子侧或 Linux 侧）
sudo apt install -y python3 python3-serial iputils-ping

# 数据抢救（Windows 侧需要管理员权限读写物理盘）
#   sd_ext4_rescue.py 需要管理员终端

# 串口工具在 Windows 上必须用 pyserial，不要用 shell 重定向
#   （Git Bash 里没有 mode 命令）
```

Python 依赖极少，大部分脚本只用到标准库；串口脚本需要 `pyserial`。

## 相关文档

- [反复烧卡根因排查报告](../../docs/troubleshooting/a7a-sd-card-corruption.md)
- [重建手册](../../docs/troubleshooting/a7a-rebuild-manual.md)
- [供电与存储排查清单](../../docs/troubleshooting/a7a-power-and-storage-checklist.md)
- [Debian 13 刷卡教程](../../docs/flash-radxa-debian13.md)
- [全套能力部署](../../docs/a7a-full-stack-deploy.md)

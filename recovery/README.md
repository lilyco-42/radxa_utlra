# recovery/ —— A7A 故障恢复与灾后重建工具包

> 这套东西不是"顺手写的脚本"，而是**一次真实的重大事故**（2026-09-17~18 连续两张 SD 卡损坏、
> 板子一度完全不亮）里，逐个踩坑、逐个固化下来的可用工具。
>
> 用法优先级：**先读 `docs/troubleshooting/`，再用 `tools/`。**

## 什么时候会用到这里

| 你遇到的情况 | 看哪里 |
|---|---|
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
| 重刷完要重建"生成 + 发布"全链路 | `docs/troubleshooting/a7a-rebuild-manual.md` + `recovery/vp/` |

## 目录结构

```
recovery/
├── README.md                   ← 你在这里
├── tools/                      诊断与救援脚本（Python，Windows/Linux 通用）
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
│   ├── lan_sweep.py              并发全网段 ping 扫描找板子
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

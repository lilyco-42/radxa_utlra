# radxa_utlra

> 👉 **新手从这里开始：[A7A 入门指南](guide.md)**（人类和 AI Agent 都能用）
> 🔧 **板子坏了/系统起不来：先看 [故障恢复工具包](recovery/README.md)**

为 Radxa A7A（Allwinner A733）释放全部硬件性能：**NPU 推理**、**VE2 硬件编码**、**GPU（Vulkan + OpenCL）**、**PPPoE 拨号 + WiFi 热点当路由器**、自动剪视频、自动部署和 GitHub Actions 自动发视频。

## 🚀 一键入口

```bash
git clone https://github.com/lilyco-42/radxa_utlra.git
cd radxa_utlra

sudo ./scripts/a7a-oneclick.sh --check     # 只体检，不改动任何东西
sudo ./scripts/a7a-oneclick.sh --all       # 全量部署：硬件能力 + VP 链路 + 验收
```

其他模式：`--hardware`（只装硬件能力）、`--recovery`（只恢复生成+发布链路）、`--verify`（只验收）。

## 💾 刷机（系统起不来时）

刷机有两个脚本，按你的系统选：

**Windows**（PowerShell，管理员）：

```powershell
.\scripts\flash-a7a.ps1 -Check                        # 先体检：设备到底认没认出来
.\scripts\flash-a7a.ps1 -List                         # 列出物理盘，人工核对
.\scripts\flash-a7a.ps1 -Image <镜像.img.xz> -DiskNumber 1 -DryRun   # 干跑
.\scripts\flash-a7a.ps1 -Image <镜像.img.xz> -DiskNumber 1          # 真刷
```

**Linux / WSL**：

```bash
sudo ./scripts/flash-sd.sh --list                                      # 列出候选盘
sudo ./scripts/flash-sd.sh --image <镜像.img.xz> --device /dev/sdX --dry-run
sudo ./scripts/flash-sd.sh --image <镜像.img.xz> --device /dev/sdX
```

两个脚本都做了这些事（Etcher/Rufus 不会告诉你的）：

- **拒写系统盘**：BusType 不是 USB、或承载了 `/` / Windows 系统分区，直接拒绝
- **处理 Windows 把 USB 盘设为 Offline**：这是 Etcher 报 `The writer process ended unexpectedly` 的真凶
- **检测 usbipd 抢走读卡器**：症状是"插了设备但系统连枚举事件都没有"
- **写后回读校验**：前 64 MiB 比对 SHA256，能抓住"写入看起来成功但数据是错的"

详细教程：[docs/flash-radxa-debian13.md](docs/flash-radxa-debian13.md)

## ⚠️ 如果这块板子烧过卡

A7A 存在一个**已实测确认**的存储写路径问题：SD 控制器在电压切换阶段失败，
导致高速写入不可靠 → ext4 元数据写坏 → 内核强制根文件系统只读 →
dbus/logind/NetworkManager/getty 连锁失败 → **无 IP、无 shell、回车没反应**。

关键证据（同一块板、同一张卡）：

```text
每次启动 fsck block 计数  +2049
坏块组  bg 64 → bg 127
只有写错误，没有读错误   ← 排除"卡坏了"，指向写路径硬件
```

**换卡不能解决**。完整根因链、判定方法、以及不碰 U-Boot 的修法见：

- [反复烧卡根因排查报告](docs/troubleshooting/a7a-sd-card-corruption.md)
- [供电与存储排查清单](docs/troubleshooting/a7a-power-and-storage-checklist.md)

**最低成本的自我防护**：把启动参数里的 `rw` 改成 `ro`（在 `/boot/extlinux/extlinux.conf`），
内核层不再写盘，就不会每次开机都多坏 2049 个块。

## 🔧 故障恢复工具包

`recovery/` 收录了这次事故里逐个固化下来的诊断与救援工具，
包含坏卡取证、验卡、串口诊断、波特率扫描、initramfs 修复、ext4 只读抢救等。

**先看 [recovery/README.md](recovery/README.md)** —— 里面有一张"我遇到什么情况 → 用哪个工具"的对照表。

### 板子活着的时候：一键体检 / 修复

```bash
# Windows 侧（自动扫网找板子 → 装公钥 → 推脚本 → 体检，一条命令）
python recovery/tools/board-remote.py --find

# 板子侧（或已经 ssh 进去之后）
sudo ./recovery/tools/board-fix.sh --check           # 只体检，不改任何东西
sudo ./recovery/tools/board-fix.sh --apply           # 修「安全类」问题
sudo ./recovery/tools/board-fix.sh --apply --harden  # 额外做写路径加固（不含危险项）
./recovery/tools/board-fix.sh --list                 # 看看有哪些可修项
```

`board-fix.sh` 的特点是 **先判断报错有没有实际后果，再决定修不修**：

- 一份健康启动日志里有 178 行错误/告警，它会告诉你**哪些是噪音、怎么验证**
- 修复项分「安全」/「加固」/「危险」三个风险等级，默认只做安全的
- 每处改动前备份到 `/root/board-fix-backup/<时间戳>/` 并打印撤销方法
- **绝不触碰** U-Boot / 分区表 / 内核 / 设备树 / 已安装软件包

**只想跑条命令、连脚本都不想推上去？** 用 `rsh.py` —— 它不往板子写任何文件，
且内置危险命令护栏（默认拒绝 `reboot` / `mkfs` / `dd` / `fsck` / `tune2fs` / `saveenv` 等）：

```bash
export BOARD_PW=***
python recovery/tools/rsh.py --host 192.168.10.165 --cmd "uptime"
python recovery/tools/rsh.py --host 192.168.10.165 --sudo --cmd "dumpe2fs -h /dev/mmcblk1p3"
```

> ⚠️ **`--harden` 会改挂载参数，需要重启才生效 —— 而在这类板子上，
> 「重启」本身就是一次大批量的块位图回写。** 2026-09-19 有一次真实事故：
> 加了 `noatime` 后重启，`ext4lazyinit` 写坏了块位图校验和，整张卡被迫重刷。
>
> 因此 `atime` 项已被降级为**「危险」**，`--harden` 不再自动包含它，
> 必须 `--only=atime --yes-dangerous` 显式点名。
> **只修 systemd 故障（`--apply`）是安全的；改挂载参数请先确认可以接受重刷。**

八个最值钱的通用判据：

1. **「完全静默」是最强的诊断信号** —— 插了设备但连枚举事件都没有 = 驱动栈拦截（Windows 上常见是 usbipd）
2. **收到几万字节 ≠ 收到数据** —— 串口 RX 悬空时会采到大量 NUL，必须按可打印率过滤
3. **「读写不对称」是硬件故障的照妖镜** —— 只有写错误没有读错误，就该怀疑写路径而不是介质
4. **报错多 ≠ 故障多** —— 先查"有没有实际后果"（`OPP not supported` ×37 但 CPU 调频 9 档全可用 = 噪音）
5. **最危险的不是报错，是检查被静默跳过** —— 根分区的 fsck 因 `ConditionPathIsReadWrite` 永远不触发，坏块静默累积
6. **「重启」不是零风险操作** —— 内核 `ext4lazyinit` 每次启动都批量回写块位图；卡不稳定时"重启试试"是最差的调试手段，每重启一次就多烧一次运气
7. **升级前读 postinst，别只看包名** —— 名字带 `cmdline`/`boot`/`u-boot`/`kernel` 的包先 `cat` 它的 postinst，看有没有守卫条件，再决定升还是 `apt-mark hold`
8. **判断"写入有没有弄坏元数据"只看 `Block count`** —— 块数不变 = 几何完好（`e2fsck` 可修）；块数变了 = 数据区在恶化，立刻停手

**硬约束**：所有工具都不碰 U-Boot（不 `saveenv`、不 `mmc write`、不改分区表）。

> 完整的启动日志逐项解读（178 行报错 → 2 个真故障）：
> [一次健康启动日志的完整解读](docs/troubleshooting/a7a-healthy-boot-log-analysis.md)
>
> 换国内源 + 安全升级系统包（含升级前排雷）：
> [换源与安全升级](docs/troubleshooting/a7a-mirror-and-safe-upgrade.md)

## 🔥 一键释放 A7A 全部硬件（新）

> VE2 硬编与 GPU（Vulkan+OpenCL）已实测打通；NPU 的「执行挂死」已定位到三层根因（时钟门控 + 电源域关闭可运行时绕过，复位/互连层待解），详见 [NPU 三层根因](docs/a733-npu-three-layer-rootcause.md)。

```bash
git clone https://github.com/lilyco-42/radxa_utlra.git
cd radxa_utlra

# 先体检，不改动任何东西
sudo ./scripts/deploy-a7a-full-stack.sh --check

# 全量部署（幂等，可重复执行）
sudo ./scripts/deploy-a7a-full-stack.sh
```

**能拿到什么：**

| 硬件 | 状态 | 验证方式 |
|---|---|---|
| **NPU** Vivante VIP9000 | ⚠️ 三层根因已定位：时钟+电源域可运行时修复（`install-npu-clk-fix.sh`），不再挂死；复位层待解 | [三层根因文档](docs/a733-npu-three-layer-rootcause.md) |
| **VE2** 硬件 H.264 编码 | ✅ 可 4K，1080p@60fps 达标 | `h264-ve2 输入.mp4 输出.mp4` |
| **GPU** PowerVR BXM-4-64 | ✅ Vulkan 1.3.277 + OpenCL 3.0 均实测通过 | `bash scripts/gpu-check.sh` |
| CPU 调频 | ✅ schedutil + 持久化 | — |

分项安装：`--npu` / `--ve2` / `--gpu` / `--perf`

**实测性能：**

- VE2 硬编 1080p 约 68 fps（2.8× 实时），CPU 仅占 **27% 单核**（软编要吃 700%+）
- VE2 硬编 4K 约 22 fps
- NPU ⚠️ **6.6 内核上实际不可用**：驱动能加载、设备能枚举，但后端从未被调度器调用（`-ngl>0` 即 `core0 hang`）——详见 `docs/a7a-full-stack-deploy.md`
- GPU 跑在 600MHz；Vulkan 用 Imagination 原厂驱动（`DRIVER_ID_IMAGINATION_PROPRIETARY`，非 Mesa 软件兜底）

**四个反直觉的坑（我们踩过，已写进方案）：**

1. **galcore 中断计数增长 ≠ NPU 在计算**——纯 CPU 模式中断同样涨（驱动内部电源管理），必须用后端 profile 埋点验证调用次数
2. VE2 **不是 V4L2 设备**，走 `/dev/cedar_dev_ve2` 字符设备；用 `ls /dev/video*` 判断会得出错误结论
3. `vulkaninfo` 会**同时列出 PowerVR 真 GPU 和 lavapipe 软件光栅**，必须按 `driverID` 区分，否则容易以为在用 GPU 其实在用 CPU
4. `aw-h264-encoder` **默认 H.264 Level 3.1**，1080p@60fps 必须手动设 `--level 40`+，否则编码器初始化后输出 0 字节并卡入 D 状态（`kill -9` 无效，只能重启板子）

完整技术细节、9 处内核 API 移植说明、验证数据见：
**[A7A 全套能力部署文档](docs/a7a-full-stack-deploy.md)**

## 🩺 NPU 执行挂死：三层根因 + 运行时绕过（2026-09-13）

6.6 BSP 上 NPU 任务提交 44 秒超时的完整根因链（附全部实测证据与修复）：

1. **时钟门控**：galcore 只 `clk_prepare` 从不 `clk_enable` → `modules/npu_clk_fix.c` 修复
2. **电源域关闭**：`pd_npu off` → PM QoS 钉 on 修复
3. **复位/NSI 互连**：待克隆 Orange Pi `orange-pi-6.6-sun60iw2` 参考实现做 diff

修复后 NPU 提交从「内核 wedged + 板子变砖」变为「44s 干净失败 + 自动恢复」，系统全程稳定。
一键安装：`sudo ./scripts/install-npu-clk-fix.sh` · 文档：[docs/a733-npu-three-layer-rootcause.md](docs/a733-npu-three-layer-rootcause.md)

## 🌐 当路由器：PPPoE 拨号 + WiFi 热点 + NAT（2026-09-14 实测打通）

入户网线插网口拨号，板载 WiFi 开热点，NAT 转发给下游设备。**端到端实测可用**：

| 环节 | 结果 |
|---|---|
| PPPoE 拨号 | ✅ `100.75.18.191/32`，CHAP `Authentication success,Welcome!` |
| 公网出口 | ✅ `36.33.45.68`，另有 IPv6 `2408:8244:b00:516f::/64` |
| WiFi 热点 | ✅ ch6 / 2437MHz / 20MHz，`type AP` |
| DHCP + NAT | ✅ 下游设备出口 IP 与板子自身直连**完全一致** |
| 客户端实测 | ✅ ping 网关 4ms、ping 223.5.5.5 40ms、`curl baidu` **200 / 58ms** |

```bash
sudo ./scripts/deploy-router.sh --check                        # 先体检，不改动
cp router-config.example.toml router-config.toml
# 编辑 router-config.toml 后一键导入
sudo python3 scripts/deploy-router-from-toml.py --config router-config.toml
```

`router-config.toml` 只保存在本机（已加入 `.gitignore`），不要把真实宽带/WiFi 密码提交到 GitHub。

**三个必踩的坑（都已写进方案）：**

1. **必须先修千兆网口 tx-delay**：出厂值 `12` 下**发帧是损坏的**，症状像坏网线 —— 链路正常、`tx_errors` 为 0、短 ping 通，**但 PPPoE 连发现阶段都过不去**。改成 `9` 后一次拨通。
2. **NetworkManager 不会自动加 NAT**：客户端能连上热点、能拿 IP、能 ping 通网关，**但上不了网**。必须手加 `MASQUERADE` + 两条 `FORWARD` 规则。
3. **残留代理环境变量会让 curl 秒失败**：`HTTPS_PROXY=127.0.0.1:1080` 之类的残留会让 `curl` 返回 `000` 且耗时 0.0003s，极易误判成「网络不通」。

完整步骤、验证方法、当前限制见：**[docs/a7a-router-mode.md](docs/a7a-router-mode.md)**

## ⚡ NPU 其实可以用（2026-09-14 修正）

之前判「封存」是**误判**：NPU 有**官方支持**（A7A 对应 NPU v3 / 软件 v2.0）和大量社区验证
—— A7A 离线语音助手（官方文档收录）、YOLOv5s 追踪、MediaPipe 人脸、SmolLM2-135M/360M。

真正的卡点是**当前 `trixie` 镜像没编 NPU 驱动**（缺 `/dev/vipcore`），
换 `radxa-a733_bullseye_kde_r5` 镜像即可。板端实测：VIPLite 2.0.3.2 用户态库加载成功，
只差内核设备节点。

完整证据链、社区项目清单、可行方案：**[docs/a733-npu-usable-path.md](docs/a733-npu-usable-path.md)**

## 🧩 硬件配置清单

一张表看全：硬件规格 / 当前状态 / 怎么配置 / 怎么验证 / **已知硬件问题**（tx-delay、WiFi 走 USB 2.0、风扇全速）。
**[docs/hardware-config-list.md](docs/hardware-config-list.md)**

## 📊 硬件资源 ROI 指南

哪个活该交给哪个硬件?按表决策:[docs/hardware-roi.md](docs/hardware-roi.md)
(LLM→CPU / 转码→VE2 / 渲染·轻计算→GPU / 安卓→redroid / NPU 封存条件与重启评估触发器)

## 当前扫描结果

本机执行 `Resolve-DnsName lain42.top` 的结果：

```text
lain42.top -> 8.153.102.122
```

`8.153.102.122:22` TCP 端口可连通，但 SSH banner 超时；局域网扫描发现 `192.168.10.52`、`192.168.10.178`、`192.168.10.233`，这些地址的 22 端口当前均拒绝连接。SSH 密钥已找到：`~/.ssh/lain42.pem`。直接 SSH 链路未就绪时，可先运行扫描脚本再试。

## 控制 Radxa

Windows:

```powershell
.\scripts\radxa-ctl.ps1
.\scripts\radxa-ctl.ps1 -ScanLan
.\scripts\radxa-ctl.ps1 -HostName 192.168.10.52 -RemoteCommand 'uptime'
```

Linux/macOS/Radxa:

```bash
./scripts/radxa-ctl.sh
./scripts/scan-radxa.sh
```

默认使用 `~/.ssh/lain42.pem`，用户 `root`，域名 `lain42.top`。可通过 `RADXA_HOST`、`RADXA_USER`、`SSH_KEY` 覆盖。

## 更新到 Debian 13

在板子上执行：

```bash
sudo ./scripts/setup-debian13.sh
sudo reboot
```

升级前会备份并停用原有 Debian sources，写入 `trixie` / `trixie-security`。Radxa 厂商内核和固件如果来自独立仓库会保留，升级后需要检查内核包是否同步更新。

## 安装媒体工具

```bash
sudo ./scripts/install-tools.sh
source /opt/radxa-tools/env.sh
```

会安装 `ffmpeg`、`imagemagick`、`yt-dlp`、`rclone`、`faster-whisper`、`python3-venv`、`libass` 等，并创建 `/opt/radxa-tools/venv`。

## 自动剪辑和上传

先复制示例配置并修改：

```bash
cp config.example.yaml ~/.config/radxa-video/config.yaml
```

手动处理一次：

```bash
python -m video_tool edit -i ~/Videos/raw -o ~/Videos/edited --config ~/.config/radxa-video/config.yaml
```

监听目录自动处理：

```bash
python -m video_tool watch --config ~/.config/radxa-video/config.yaml
```

作为 systemd 服务常驻：

```bash
sudo ./scripts/install-video-service.sh
journalctl -fu radxa-video
```

上传目标示例：

```yaml
upload_targets:
  - rclone:my-bucket:videos/
  - gh:your-name/your-repo:nightly
  - cp:/mnt/nas/videos/
  - https://example.com/upload
```

## GitHub Actions 自动发视频

把原始素材放到仓库的 `videos/raw/`，推送后 `.github/workflows/auto-publish.yml` 会自动剪辑并把 `dist/videos/` 上传到 GitHub Release。

## Radxa Debian 13 刷卡

如果 A7A 绿灯常亮但局域网扫不到，通常是因为系统没有正常启动。Windows 下刷入官方 Debian 13 镜像的完整步骤见：

[Radxa Cubie A7A Debian 13 刷卡教程](docs/flash-radxa-debian13.md)

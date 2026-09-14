# A7A 入门指南

> **这份文档有两个读者：**
> - **人类** → 看 [§1 人类路线](#1-人类路线从零到跑起来)，按步骤走。
> - **AI Agent** → 直接读 [§2 机器可读能力清单](#2-ai-agent-路线机器可读能力清单)，
>   里面有能力矩阵、调用契约、验证判据和禁止事项。

板子：Radxa Cubie A7A（Allwinner A733 / sun60iw2），8 核 4G。
系统：Debian 13 trixie，内核 6.6.x（实测 `6.6.98-4-aw2511`）。

---

## 0. 这块板子现在能干什么

| 能力 | 状态 | 一句话 |
|---|---|---|
| **VE2** H.264 硬编 | ✅ 可用 | 1080p@60 ≈ 68fps，只吃 27% 单核 |
| **GPU** PowerVR BXM-4-64 | ✅ 可用 | Vulkan 1.3.277 + OpenCL 3.0，14/14 验证通过 |
| **CPU** 8 核 | ✅ 可用 | 目前所有 LLM 推理的实际承担者 |
| **路由器** PPPoE + WiFi 热点 + NAT | ✅ 可用 | 实测端到端打通，客户端 200/58ms |
| **视频自动剪辑** | ✅ 可用 | 静音切除 / 降噪 / loudnorm / watch 服务 |
| **redroid** Android 14 容器 | ⚠️ 有前置 | 宿主机必须是 cgroup v1 |
| **NPU** Vivante VIP9000 | ⛔ 封存 | 第三层根因卡在 boot chain，**当前别碰** |

**先记住一个数字**：内存带宽。它决定了上面几乎所有结论 ——
详见 [docs/hardware-roi.md](docs/hardware-roi.md)。

---

## 1. 人类路线：从零到跑起来

### 1.1 前提

- 板子：Radxa Cubie A7A
- 系统：Debian 13 trixie（没刷？→ [刷卡教程](docs/flash-radxa-debian13.md)）
- 你能 SSH 上去，或者有串口

### 1.2 第一步：连上板子

```bash
ssh radxa@<板子IP>

# 找不到 IP？用扫描脚本
./scripts/scan-radxa.sh
```

### 1.3 第二步：体检（不改动任何东西）

```bash
sudo ./scripts/deploy-a7a-full-stack.sh --check   # 硬件能力现状
sudo ./scripts/deploy-router.sh --check           # 网络 / 路由器现状
```

**先跑体检。** 它只读不写，会告诉你哪块硬件已经能用、哪块还缺东西。

### 1.4 第三步：按需求做一件事

```bash
# 全套硬件能力（NPU 修复模块 + VE2 + GPU + 调频），幂等
sudo ./scripts/deploy-a7a-full-stack.sh

# 只装/验 GPU
sudo ./scripts/deploy-a7a-full-stack.sh --gpu

# 让板子当路由器
sudo ./scripts/deploy-router.sh --all --user 宽带账号 --pass 密码 --mac AA:BB:CC:DD:EE:FF
```

### 1.5 我想干 X，该用哪个硬件？

| 你想干的事 | 用哪个 | 入口 |
|---|---|---|
| LLM 推理（聊天 / 生成） | **CPU 8 核** | `~/bin/a733-llama` |
| 视频 H.264 转码 | **VE2 硬编** | `h264-ve2 in.mp4 out.mp4` |
| 渲染 / 轻量并行计算 | **GPU** | `bash scripts/gpu-check.sh` |
| 安卓 App / 2D 游戏 / 挂机 | **redroid 容器** | `docker start a7a-android` |
| 自动剪视频 / 上传 | **video_tool** | `python -m video_tool edit -i 输入 -o 输出` |
| 让板子当路由器 | **PPPoE + AP** | `sudo ./scripts/deploy-router.sh --all` |
| 重度 3D 手游 / FPS 竞技 | ❌ 别在这块板上 | — |
| NPU 推理 | ⛔ 封存，别碰 | — |

完整决策表（含实测数字和 ROI 分析）：[docs/hardware-roi.md](docs/hardware-roi.md)

---

## 2. AI Agent 路线：机器可读能力清单

> 给自动化 agent：**先读这一节**。它把「能做什么 / 怎么调 / 怎么验 / 别踩什么」
> 压成结构化形式。

### 2.1 能力矩阵

```yaml
board:
  model: Radxa Cubie A7A
  soc: Allwinner A733 (sun60iw2)
  arch: aarch64
  cpu: 8 cores
  ram: 4G
  os: Debian 13 trixie
  kernel: 6.6.x          # 实测 6.6.98-4-aw2511

capabilities:

  - id: ve2
    name: H.264 硬件编码
    status: available
    entry: h264-ve2 <in> <out>
    device: /dev/cedar_dev_ve2     # 注意：不是 /dev/video*
    verified: "1080p@60 ≈ 68fps，4K ≈ 22fps，CPU 27% 单核"
    requires: scripts/deploy-a7a-full-stack.sh --ve2

  - id: gpu
    name: PowerVR BXM-4-64 (Vulkan + OpenCL)
    status: available
    entry: bash scripts/gpu-check.sh
    verified: "Vulkan 1.3.277 + OpenCL 3.0，14/14 通过"
    caveat: "vulkaninfo 会同时列出 lavapipe 软光栅，必须按 driverID 区分"

  - id: cpu
    name: LLM 推理（当前唯一实际可用的推理后端）
    status: available
    entry: ~/bin/a733-llama
    verified: "Qwen3-0.6B 22.4 tok/s / Qwen2.5-0.5B 18.1 tok/s"

  - id: router
    name: PPPoE 拨号 + WiFi 热点 + NAT
    status: available
    entry: sudo scripts/deploy-router.sh --all --user U --pass P --mac M
    requires:
      - "必须先修 tx_delay：echo 9 > /sys/class/net/end0/device/tx_delay"
      - "NAT 要手动加 —— NetworkManager 不会自动加"
    verified: "客户端 curl baidu 200/58ms，出口 IP 与板子自身直连一致"
    docs: docs/a7a-router-mode.md

  - id: video_tool
    name: 自动剪辑 / 静音切除 / 降噪 / 上传
    status: available
    entry: python -m video_tool edit -i <in> -o <out> --config <yaml>
    requires: scripts/install-tools.sh

  - id: redroid
    name: Android 14 容器
    status: conditional
    entry: "docker start a7a-android && scrcpy -s <ip>:5555"
    blocker: "宿主机必须 cgroup v1；cgroup v2 unified 下 Android init 挂 cgroup 失败"
    check: "ls /sys/fs/cgroup/ 应能看到 memory/ 子目录"

  - id: npu
    name: Vivante VIP9000
    status: sealed
    reason: "第三层根因卡在 boot chain (ATF/U-Boot)，内核态无法修复"
    do_not: "不要尝试用它跑推理 —— 会挂死，严重时把板子弄砖"
    docs: docs/a733-npu-three-layer-rootcause.md
```

### 2.2 调用契约

```yaml
rules:
  - "所有 deploy-*.sh 先跑 --check：只读，不改变系统状态"
  - "deploy-*.sh 幂等，可重复执行；已配置的部分会跳过"
  - "需要 root 的操作自己检查 EUID，不是 root 会明确报错退出"
  - "tx_delay / pppd / hotspot / iptables 都是运行时状态，重启全丢"
  - "redroid 启动前先确认 /sys/fs/cgroup 里有 memory/ 子目录（v1 标志）"
```

### 2.3 验证判据（别信「命令没报错」）

```yaml
verification:
  ve2:
    - "输出文件存在"
    - "ffprobe 显示的编码器走的是硬件路径"
  gpu:
    - "bash scripts/gpu-check.sh 输出 14/14"
    - "GPU 硬件中断计数有增量"
  router:
    - "ip -4 -br addr show ppp0 拿到 100.x/32 或公网段"
    - "tail /var/log/ppp.log 出现 CHAP authentication succeeded"
    - "另一台设备 curl -s http://members.3322.org/dyndns/getip
       的结果与板子自身 curl 结果一致"
  redroid:
    - "sys.boot_completed 置 1"
    - "adbd 监听 5555"
  npu: "❌ 没有可用的验证方式 —— 因为不可用"
```

### 2.4 禁止事项 / 高频陷阱

1. **不要用 `ls /dev/video*` 判断 VE2** —— 它是 `/dev/cedar_dev_ve2` 字符设备。
2. **`vulkaninfo` 会同时列出真 GPU 和 lavapipe 软光栅** —— 必须按 `driverID` 区分，
   否则你以为在用 GPU，其实在用 CPU。
3. **galcore 中断计数增长 ≠ NPU 在计算** —— 纯 CPU 模式下中断同样会涨。
4. **修 tx-delay 之前别指望 PPPoE 能通** —— 出厂值 `12` 下发帧损坏，
   症状像坏网线（链路正常、`tx_errors`=0、短 ping 通）。
5. **`HTTPS_PROXY` 残留会让 curl 秒失败**（返回 `000`，耗时 0.0003s）——
   排查网络问题前先 `env | grep -i proxy`。
6. **NPU 是封存状态** —— 别调，见 §2.1。

---

## 3. 排错速查

| 症状 | 先查什么 |
|---|---|
| 板子绿灯常亮但局域网扫不到 | 系统没正常启动 → [刷卡教程](docs/flash-radxa-debian13.md) |
| SSH 密钥交换后卡死 / `apt` 卡住 | 千兆网口 tx-delay：`echo 9 > /sys/class/net/end0/device/tx_delay` |
| PPPoE 连发现阶段都过不去 | 同上 —— tx-delay |
| 客户端连上热点但上不了网 | NAT 没加（NM 不会自动加） |
| `curl` 返回 000 且耗时极短 | 代理环境变量残留 |
| redroid 起不来 / `lmkd` 崩 | 宿主机 cgroup 不是 v1 |
| NPU 调用挂死 | 已知问题，别调（见 §2.1） |

---

## 4. 文档与脚本索引

**文档**

| 文档 | 讲什么 |
|---|---|
| [docs/hardware-config-list.md](docs/hardware-config-list.md) | **硬件配置清单**：规格 / 状态 / 怎么配 / 怎么验 / 已知硬件问题 |
| [docs/hardware-roi.md](docs/hardware-roi.md) | 任务 → 硬件决策表（**先看这个**） |
| [docs/a7a-full-stack-deploy.md](docs/a7a-full-stack-deploy.md) | 全套能力部署 + 9 处内核 API 移植 |
| [docs/a7a-router-mode.md](docs/a7a-router-mode.md) | 当路由器：PPPoE + 热点 + NAT |
| [docs/a733-npu-three-layer-rootcause.md](docs/a733-npu-three-layer-rootcause.md) | NPU 挂死三层根因 |
| [docs/flash-radxa-debian13.md](docs/flash-radxa-debian13.md) | 刷卡教程 |

**脚本**

| 脚本 | 用途 |
|---|---|
| `scripts/deploy-a7a-full-stack.sh` | 一键部署 NPU / VE2 / GPU / 调频 |
| `scripts/deploy-router.sh` | 一键配路由器 |
| `scripts/gpu-check.sh` | GPU 14 项验证 |
| `scripts/install-npu-clk-fix.sh` | 安装 NPU 时钟修复模块 |
| `scripts/install-tools.sh` | 装媒体工具 + Python venv |
| `scripts/install-video-service.sh` | 把视频监听装成 systemd 服务 |
| `scripts/mc-gate.sh` | 视频管道的资源闸门 |
| `scripts/radxa-ctl.sh` / `radxa-ctl.ps1` | 远程控制板子 |
| `scripts/scan-radxa.sh` | 局域网找板子 |
| `scripts/setup-debian13.sh` | 升级到 Debian 13 |

# radxa_utlra

为 Radxa A7A（Allwinner A733）释放全部硬件性能：**NPU 推理**、**VE2 硬件编码**、自动剪视频、自动部署和 GitHub Actions 自动发视频。

## 🔥 一键释放 A7A 全部硬件（新）

> 把原本被认为"不可用"的 NPU 和 VE2 加速器全部打通，已在 Debian 13 + 内核 6.6.98-4-aw2511 上验证。

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
| **NPU** Vivante VIP9000 | ✅ 6.6 内核可用（不再需要降回 5.15） | `~/bin/a733-llama --list-devices` |
| **VE2** 硬件 H.264 编码 | ✅ 可 4K，1080p@60fps 达标 | `h264-ve2 输入.mp4 输出.mp4` |
| CPU 调频 | ✅ schedutil + 持久化 | — |

分项安装：`--npu` / `--ve2` / `--perf`

**实测性能：**

- VE2 硬编 1080p 约 68 fps（2.8× 实时），CPU 仅占 **27% 单核**（软编要吃 700%+）
- VE2 硬编 4K 约 22 fps
- NPU 通路已打通，当前速度与 CPU 持平（需扩充 TIM-VX 算子覆盖才能显著提速）

**两个反直觉的坑（我们踩过，已写进方案）：**

1. NPU 首次推理会报 `core0 hang, automatic recovery`——这是**一次性事件**，自恢复后一切正常
2. VE2 **不是 V4L2 设备**，走 `/dev/cedar_dev_ve2` 字符设备；用 `ls /dev/video*` 判断会得出错误结论

完整技术细节、9 处内核 API 移植说明、验证数据见：
**[A7A 全套能力部署文档](docs/a7a-full-stack-deploy.md)**

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

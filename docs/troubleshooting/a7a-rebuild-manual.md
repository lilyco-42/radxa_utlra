# A7A VP 链路重建手册

> 起因：**2026-09-18 旧 SD 卡烧毁**（发烫、无法枚举、已拔出）。板上数据永久丢失，
> 用户换新卡重刷系统，IP 由 `192.168.10.69` 变为 `192.168.10.165`。
> 本文档记录从零重建「生成 + 发布」全链路的完整步骤。

## 0. 现状

| 项目 | 状态 |
|---|---|
| 旧 SD 卡 | 🔥 已烧毁，数据不可恢复 |
| 新系统 IP | `192.168.10.165`（`radxa-cubie-a7a`） |
| 新系统内存 | 3.9G（旧卡时期曾是 1.6G） |
| 新系统在跑 | 另一条线：`rp2/`、NPU、`voice_server.py` |
| SSH | 需先确认可达（曾出现握手即断 = 内存卡死指纹） |

## 1. 丢失资产清单

旧卡上（全部需重建）：

```
~/vp/                   queue/、process_queue.py、run_pipeline.sh、videos/、logs/
~/sau/                  6 平台发布凭据 + venv
~/biliup/               biliupR-v1.2.4-aarch64-linux
~/mihomo/               代理 + geoip/geosite
~/a733-cedarc/          VE2 硬编码器
~/html-video/           渲染管线（含 Cedar 补丁）
~/venv-tts/             文案/配音虚拟环境
~/bin/                  软链接（sau/vp/biliup/lly/mihomo/h264-ve2）
~/.bashrc               代理端口修正（1080→7890）+ PATH + NO_PROXY
~/.config/systemd/user/ vp-pipeline.service / .timer
```

**本地还活着的**（本重建包已收录）：

| 文件 | 位置 |
|---|---|
| 62 条任务队列 | `vp/queue/lyco-rust-20260917.json` |
| 队列处理器 | `vp/process_queue.py` |
| 流水线脚本（已合并板上全部修复） | `vp/run_pipeline.sh` |
| Cedar 补丁脚本 | `vp/patch_cedar_v4.py` |
| 发布器（已合并 success-watch + 进程树收尾） | `vp/publish.py` |
| 队列状态 | `vp/queue/state.json` |
| systemd 单元 | `systemd/` |

## 2. 重建顺序

### 2.1 先决条件（板子上）

```bash
# 系统包
sudo apt update
sudo apt install -y python3-venv ffmpeg fonts-noto-cjk nodejs npm

# 文案 + 配音虚拟环境
python3 -m venv ~/venv-tts
~/venv-tts/bin/pip install -U pip
~/venv-tts/bin/pip install edge-tts requests
```

### 2.2 VE2 硬编码器（可选，但强烈建议 —— 提速关键）

```bash
git clone https://github.com/mashiqi/A733-Cedarc ~/a733-cedarc
sudo ~/a733-cedarc/scripts/install-runtime.sh
~/a733-cedarc/scripts/verify-runtime.sh
```

验证：
```bash
ls -l /dev/cedar_dev /dev/cedar_dev_ve2 /dev/dma_heap/system
ls /usr/lib/aarch64-linux-gnu/libvenc_h264.so
```

> ⚠️ **H.264 Level 是硬坑**：`aw-h264-encoder` 默认 Level 3.1 编不了 1080p@60
> （初始化正常但输出 0 字节，随后进程卡入 D 状态，`kill -9` 无效，**只能重启板子**）。
> 1080p 一律用 `--level 40` 以上。详见 `~/a733-cedarc` 与 `radxa_utlra` 文档。

### 2.3 渲染管线（html-video）

```bash
git clone <html-video 仓库> ~/html-video
cd ~/html-video && npm install
# 打 Cedar 补丁（Cedar 优先 + libx264 veryfast 回退）
python3 ~/vp/patch_cedar_v4.py
```

> 补丁要点：15s 超时 + `pgrep` 守卫（防与卡死进程冲突）+ Level 40/31 自动选择
> + 失败回退 `libx264 -preset veryfast -crf 23`。
> 实测 veryfast 比 medium 快约 26%（渲染）/ 22%（总耗时）。

### 2.4 部署 VP 核心

把本重建包传到板子，然后：

```bash
# 先体检（不改动任何东西）
./deploy-rebuild.sh --check

# 正式部署
./deploy-rebuild.sh
```

脚本会：建目录 → 装 VP 文件 → 装 systemd 单元 → 检查 html-video/a733-cedarc →
`daemon-reload` + `enable/start` timer。

### 2.5 biliup（B站）

```bash
# 下载 arm64 原生二进制
mkdir -p ~/biliup && cd ~/biliup
# 从 https://github.com/biliup/biliup/releases 取 biliupR-v1.2.4-aarch64-linux
cd ~/vp && biliup login          # 扫码登录
biliup -u ~/vp/cookies.json list # 验证（应列出账号下视频）
```

### 2.6 sau（多平台）

```bash
git clone https://github.com/dreammis/social-auto-upload ~/sau
cd ~/sau && python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium

# 每平台登录
~/sau/.venv/bin/sau douyin   login --account '我的抖音'
~/sau/.venv/bin/sau kuaishou login --account '我的快手'
~/sau/.venv/bin/sau tencent  login --account '我的视频号'

# 验证
~/sau/.venv/bin/sau douyin check --account '我的抖音'
```

> 浏览器导出的 cookie 可用 `sau-install-cookie` 转换安装（工具本身也需重建，见下）。
> sau 会在发布成功后挂住不退 —— `publish.py` 已加 success-watch（3s 收尾 + 杀进程组）。

### 2.7 环境变量与软链接

```bash
# ~/.bashrc：代理端口必须是 7890（不是 1080，否则 curl 0ms 失败）
export HTTPS_PROXY=http://127.0.0.1:7890
export HTTP_PROXY=http://127.0.0.1:7890
export ALL_PROXY=http://127.0.0.1:7890
export NO_PROXY=localhost,127.0.0.1,::1,.bilibili.com,bilibili.com
export PATH="$HOME/bin:$PATH"

# 软链接
mkdir -p ~/bin
ln -sf ~/biliup/biliupR-v1.2.4-aarch64-linux/biliup ~/bin/biliup
ln -sf ~/sau/.venv/bin/sau ~/bin/sau
ln -sf ~/vp/run_pipeline.sh ~/bin/vp
ln -sf ~/a733-cedarc/aw-h264-to-mp4 ~/bin/h264-ve2
```

### 2.8 凭据填写

编辑 `~/vp/config.yaml`：
1. `llm.api_key` —— 去 https://openrouter.ai/keys 重新生成
2. `llm.model` —— 保持 `inclusionai/ling-3.0-flash-vl:free`（实测不泄漏推理过程）
3. 平台 `enabled` —— 登录成功并 `check` 通过后再逐个改 true
4. `backend` —— 全平台就绪后改 `both`

然后：
```bash
chmod 600 ~/vp/config.yaml
```

## 3. 验收

```bash
# 1) 队列状态
python3 ~/vp/process_queue.py --status

# 2) dry-run 预演
python3 ~/vp/process_queue.py --dry-run

# 3) 真跑一个任务（不发布）
python3 ~/vp/process_queue.py
# 检查 ~/vp/videos/<最新>/final.mp4
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate -of default=nw=1 <final.mp4>

# 4) 定时器
systemctl --user list-timers vp-pipeline.timer --no-pager
```

## 4. 已知坑（重建时务必避开）

| 坑 | 症状 | 处理 |
|---|---|---|
| VE2 Level 3.1 编 1080p@60 | 输出 0 字节 + 进程 D 状态 | `--level 40`+；D 状态只能重启板子 |
| sau 发布成功后不退出 | 每平台等满 900s | success-watch 3s 收尾（已在 publish.py） |
| edge-tts 偶发限流 | `NoAudioReceived` | 已加 3 次重试（run_pipeline.sh） |
| 代理端口写错 | curl 0ms `Connection refused` | 用 7890，不是 1080 |
| timer 重入 | 两个渲染同时跑抢内存 | flock 单实例锁（冲突 exit 75） |
| 内存吃紧 | SSH 握手即断（ICMP 通） | MemoryMax=1G + NODE_OPTIONS 768M + nice 5 |
| 平台上传流程过期 | TikTok/小红书选择器失效 | 先只启用验证过的平台 |

## 5. 未重建 / 待办

- [ ] `sau-install-cookie` 工具（`/usr/local/bin/`）—— 转换浏览器裸 cookie → Playwright storage_state
- [ ] `mihomo` 代理 + geoip/geosite（若要代理）
- [ ] `~/lly-v0.2.1`、`a733-llama` 等其它工具软链接
- [ ] TXT 队列的文本内容（旧队列 JSON 只存了 topic，Lyco 日记正文需重新抓）
- [ ] YouTube / TikTok / 小红书 —— 上传流程需修（旧选择器已失效）

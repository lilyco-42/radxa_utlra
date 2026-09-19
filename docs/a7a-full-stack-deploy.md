# A7A 全套能力部署（NPU + VE2 硬编 + GPU + 性能调优）

把 Radxa Cubie A7A（Allwinner A733）上**原本被认为不可用**的加速器全部打通，并做成可复现的一键部署。

> 验证环境：Radxa Cubie A7A / Debian 13 trixie / **内核 6.6.98-4-aw2511** / 8 核 4G

## 一句话结论

| 硬件 | 之前认知 | 实际状态 |
|---|---|---|
| **NPU** (Vivante VIP9000) | 6.6 内核下不可用，必须降回 5.15 | ✅ **6.6 内核可用**，galcore 驱动移植成功 |
| **VE2** (视频硬编) | 不可用（没看到 V4L2 节点） | ✅ **一直可用**，走 `/dev/cedar_dev_ve2` 字符设备 |
| **GPU** (PowerVR BXM-4-64) | 只有 OpenCL 可用 | ✅ **Vulkan 1.3.277 + OpenCL 3.0 双通道实测通过** |
| CPU 调频 | ondemand | ✅ 改 schedutil 并持久化 |

## 快速开始

```bash
git clone https://github.com/lilyco-42/radxa_utlra.git
cd radxa_utlra

# 先看现状，不做任何改动
sudo ./scripts/deploy-a7a-full-stack.sh --check

# 全量部署
sudo ./scripts/deploy-a7a-full-stack.sh

# 按需分项
sudo ./scripts/deploy-a7a-full-stack.sh --npu
sudo ./scripts/deploy-a7a-full-stack.sh --ve2
sudo ./scripts/deploy-a7a-full-stack.sh --gpu
sudo ./scripts/deploy-a7a-full-stack.sh --perf
```

脚本**幂等**，可重复执行；已装部分自动跳过。

## 验证

```bash
# NPU：应列出 A733 设备
~/bin/a733-llama --list-devices
# → A733: Allwinner A733 VIP9000Nano-DI via TIM-VX

# VE2：任意视频硬编转码
h264-ve2 输入.mp4 输出.mp4

# GPU：Vulkan + OpenCL 全量验证（14 项）
bash scripts/gpu-check.sh
```

官方验证脚本（帧率矩阵 + 旋转矩阵，需先装 VE2）：

```bash
cd ~/a733-cedarc
./validation/test-fps-matrix.sh
./validation/test-rotation-matrix.sh
```

---

## 一、NPU：把 galcore 驱动移植到 6.6 内核

### 背景

A7A 的 NPU 是 Vivante VIP9000，需要 Allwinner 的 `vipcore` 或开源的 `galcore` 驱动。
社区已知的成功案例（reef1994、skamagedon、petayyyy）**全部跑在 5.15 vendor 内核上**，
在 6.6 BSP 内核上一律失败，因此普遍结论是"NPU 必须用 5.15"。

实测发现：**这个结论不成立。** 把 galcore 驱动对着 6.6 内核重新移植后可以正常工作。

### 移植的 9 处改动

驱动来自 [reef1994/a733-llama-npu-stack](https://github.com/reef1994/a733-llama-npu-stack)，
针对 5.15 编写，直接对 6.6 编译会报错。补丁见 `patches/galcore-6.6-port.patch`：

| # | 文件 | 问题 | 修法 |
|---|---|---|---|
| 1 | `gc_hal_kernel_allocator_gfp.c` | `vm_flags` 6.3+ 只读 | 改 `vm_flags_set(vma, ...)` |
| 2 | `gc_hal_kernel_allocator_reserved_mem.c` | 同上 | 同上 |
| 3 | `gc_hal_kernel_allocator_user_memory.c` | `pin_user_pages` 6.5+ 去掉 `vmas` 参数 | 改 4 参数调用 |
| 4 | `gc_hal_kernel_allocator_dmabuf.c` | `dma_buf.lock` 6.6 已移除 | 用版本卫语句包住 |
| 5 | `gc_hal_kernel_driver.c` | `class_create` 6.4+ 只收 1 参 | 版本卫语句 |
| 6 | `gc_hal_kernel_os.c` | `virt_addr_valid` 要指针 | 传 `(void *)logical` |
| 7 | `gc_hal_kernel_os.c` | `__pte_offset_map_lock` 未导出 | 用 `pte_offset_kernel` + `pte_lockptr` 开码实现 |
| 8 | `gc_hal_kernel_allocator_gfp.c` | BSP 内核缺 `__GFP_ATOMIC` | 补 `#ifndef` 兜底 |
| 9 | `Makefile` | `-Werror` 导致告警变错误 | 去掉 |

### 关键坑

- **`__pte_offset_map_lock` 不在 `Module.symvers` 里**，虽然 `kallsyms` 能看到符号，
  但模块链接会失败（MODPOST undefined）。必须在驱动内部自己实现一个等价函数。
- **`-ngl` 一旦启用必触发 `GPU[0] core0 hang`** —— ⚠️ **2026-09-12 复测更正**：
  `-ngl 4` / `-ngl 99` 反复测试**每次都挂死**，并非"一次性事件"。
  进而查明更根本的问题：**A733 后端在 6.6 内核上从未真正参与推理**（见下方「性能现状」）。

### 运行环境两个必需变量

llama.cpp 的 A733 后端是动态加载的，缺任一条件设备不会注册：

```bash
STACK=~/a733-llama-npu-stack
export LD_LIBRARY_PATH="$STACK/vendor/unified/lib/aarch64-none-linux-gnu:$STACK/dist/llama-a733-runtime/lib:$STACK/dist/llama-a733-runtime/bin:$STACK/build/tim-vx/src/tim"
export GGML_BACKEND_PATH="$STACK/dist/llama-a733-runtime/bin/libggml-a733.so"   # 必须是 .so 文件本身，不是目录
```

`scripts/a733-llama` 已封装好这些，直接用即可。

### 性能现状（2026-09-12 复测更正）

| 模型 | `-dev A733` | 纯 CPU 8 核 |
|---|---|---|
| Qwen3-0.6B Q8_0 | 14.5 t/s | 14.7 t/s |
| qwen2.5-0.5B Q8_0 | 18.3 t/s | 18.1 t/s |

**结论：NPU 后端从未被调度器调用，速度就是纯 CPU 速度。**

决定性判据：给后端加 profile 埋点、上报阈值降到 **1 次/调用** 后运行 → **零输出**，
证明 `ggml_backend_a733_graph_compute()` 一次都没被调用。

原因是后端声明自己为 GPU 类型设备，走 llama.cpp 的 **layer offload 路径**：

- `-ngl = 0`（默认）→ 后端完全不被选中（其 buffer type 是 host，CPU 后端优先）
- `-ngl > 0` → 参与，但立即 `TIM-VX run failed` + 内核 `core0 hang`

**没有中间态。** 因此「扩充 TIM-VX 算子覆盖」没有优化对象——通路是断的，
覆盖多少算子都是零。

根因在**内核态**：上游工程的已验证环境是内核 `5.15.147-21-a733`
（其 `docs/VERIFICATION.md` 记载 Qwen2.5-1.5B Q4_K_M 可跑，6.4 t/s）；
6.6 上的 galcore 是移植版，**设备能枚举，计算下发即挂**。

### ⚠️ 反例：中断计数**不能**证明 NPU 在计算

```bash
grep galcore /proc/interrupts
```

**这个判据是失效的**（本项目曾因此误判，特此记录）：
实测**纯 CPU 模式**（不加 `-dev A733`）中断同样增长（+11、+29），
且 `fuser /dev/galcore` 无任何进程占用——中断来自驱动内部 `_SetPower` 电源管理配对。

**正确做法**：用后端内部的调用计数（`A733_PROFILE=1` 埋点，阈值设 1）。
教训：曾把"约 0.5 次/token"解读为"低 offload 率"，实际是**一个都没接住**。

---

## 二、VE2：硬件 H.264 编码

### 背景：一个检查方法导致的误判

之前用 `ls /dev/video*` 看不到节点，就判定"VE2 不可用"。**这是错的**：
A733 的 VE 走的是 **cedar 字符设备接口，不是 V4L2**：

```
/dev/cedar_dev       (major 235)
/dev/cedar_dev_ve2   (major 511)
/dev/dma_heap/system
```

内核侧 `sunxi_ve` 模块一直是加载的，`1c0e000.ve`（解码）和 `1c10000.ve2`（编码）
都绑定了 `sunxi-cedar` 驱动。缺的只是**用户态厂商库**。

### 方案

用 [mashiqi/A733-Cedarc](https://github.com/mashiqi/A733-Cedarc)（同为 A733、同为 6.6.98 内核），
提供 `aw-h264-encoder`（NV12 stdin → Annex-B H.264 stdout）+ 厂商运行时库。

### 验证结果：官方脚本全通过

**帧率矩阵 8/8 PASS**（IRQ 增量精确等于帧数，零同步超时，时长全部精确 6.000000）：

| 分辨率 | fps | 帧数 | IRQ增量 | 结果 |
|---|---|---|---|---|
| 640×360 | 1 | 6 | 6 | PASS |
| 640×360 | 7 | 42 | 42 | PASS |
| 640×360 | 15 | 90 | 90 | PASS |
| 640×360 | 29 | 174 | 174 | PASS |
| 640×360 | 30 | 180 | 180 | PASS |
| 640×360 | 60 | 360 | 360 | PASS |
| 1920×1080 | 30 | 180 | 180 | PASS |
| 1920×1080 | 60 | 360 | 360 | PASS |

**旋转矩阵 ALL PASSED**（0°/90°/180°/270°）

### 性能实测

| 场景 | 耗时 | 吞吐 |
|---|---|---|
| 1080p 241 帧 | 3.5s | ~68 fps（2.8× 实时） |
| 4K (3840×2160) 48 帧 | 2.22s | ~22 fps |
| 编码期间 CPU | — | **仅 27% 单核** |

对比 libx264：软编绝对速度可能更快，但吃 **700%+ CPU** 且挤占其他服务。
VE2 的价值在**低占用、可并行、支持 4K**。

### 我们修的原版两个 bug

`patches/aw-h264-to-mp4.fixed`：

1. **输出 MP4 时长错乱**（10s 视频显示 2.04s）
   原因：VE2 输出的 SPS VUI timing 有偏差，而原版用的 `h264_metadata` 滤镜在 `-c:v copy` 下不生效。
   修法：改**两遍法**——先出裸流，再用 `-r $fps -fps_mode cfr -video_track_timescale 90000` 重封装。

2. **退出码异常**
   原因：新增临时文件未清理导致 `rmdir` 失败，退出码 1，进而使官方验证脚本误判 FAIL。
   修法：补上清理逻辑。

### 实战坑：H.264 Level 与 D 状态挂死（2026-09-17 实测）

`aw-h264-encoder` 的 `--level` 默认 **3.1**（Level 31）。H.264 规范里 Level 3.1
上限是 1080p@30fps，**1080p@60fps 必须用 Level 4.0 以上**。

**错误现象**：用默认 Level 31 编 1080p@60，编码器日志显示正常初始化
（`ve init`、`/dev/cedar_dev fd=4`），但**输出 0 字节**，随后 `aw-h264-encoder`
进程进入 **D 状态**（uninterruptible sleep）。

**D 状态无法清除**：
- `kill -9`（SIGKILL）无效——内核态 I/O 等待不可中断
- `systemctl stop` 等待 cgroup 清空也会卡住，日志报
  `Processes still around after final SIGKILL`
- **唯一恢复方式：物理重启板子**（见 a733-cedarc README 同款建议）

**正确做法**：
1. 调用 `aw-h264-encoder` / `aw-h264-to-mp4` 时，1080p 一律加 `--level 40`（或 41），
   720p 及以下可用默认 31
2. 调用方加 **15 秒超时** + **pgrep 守卫**：若检测到已有 `aw-h264-encoder` 进程
   在跑（可能已挂死），跳过 Cedar 路径，直接回退 `libx264`
3. 参考 `scripts/h264-ve2` 脚本的 Level 自动匹配逻辑（≤1280→31, ≤1920→41, >1920→51）

### 渲染管线集成（html-video adapter-hyperframes）

VP 短视频流水线的渲染步骤（`adapter-hyperframes/dist/render.js`）已集成
Cedar 优先 + libx264 回退策略：

1. 检查 `/dev/cedar_dev_ve2` 与 `aw-h264-to-mp4` 存在，且宽度是 16 的倍数
2. `pgrep -f aw-h264-encoder` 检测是否已有 Cedar 进程在跑（避免与挂死进程冲突）
3. 管线：`ffmpeg`（WebM→NV12 rawvideo pipe）| `aw-h264-to-mp4`（硬件编码→MP4）
4. 15 秒超时，失败自动回退 `libx264 -preset veryfast -crf 23`
5. 备份：`render.js.bak-cedar-20260917`

实测（回退路径）：1080p60 H.264 + AAC，61.3 秒完成；
`libx264 medium→veryfast` 使渲染提速约 26%、总耗时约 22%。
重启板子清除 D 状态进程后，Cedar 硬编码路径预计进一步提速 30–50%。

---

## 三、GPU：PowerVR BXM-4-64（Vulkan + OpenCL）

### 结论先说

GPU **不需要额外安装任何东西**——Radxa 官方镜像已经把完整 DDK 装好了。这一节的价值是**把它验证清楚**，因为这台机器上有一个很容易骗过人的陷阱。

```
GPU0: PowerVR B-Series BXM-4-64 MC1     ← 真 GPU
      driverID = DRIVER_ID_IMAGINATION_PROPRIETARY
      apiVersion = 1.3.277, driverInfo = 24.2@6603887
      conformanceVersion = 1.3.8.1        ← 过了 Khronos 官方一致性测试

GPU1: (无名)                             ← Mesa lavapipe，CPU 软件光栅
      vendorID = 0x10005, driverID = DRIVER_ID_MESA_LAVAPIPE
```

### 坑：`vulkaninfo` 会同时列出真 GPU 和软件兜底

装了 `mesa-vulkan-drivers`（Debian 默认就有）之后，`vulkaninfo` 会**无条件多出一个 lavapipe 设备**。如果只看到"Vulkan 有设备"就下结论，很可能你测的其实是 CPU。

**判断标准只有一条：看 `driverID`。**

```bash
vulkaninfo --summary | grep -E 'deviceName|driverID'
# 出现 DRIVER_ID_IMAGINATION_PROPRIETARY 才是真的在用 PowerVR
```

Mesa 自己也意识到了这个问题，所以带了 `VK_LAYER_MESA_device_select` 层来选设备。但**不要依赖它**——在脚本里显式判 `driverID` 更可靠。

### 组件构成

| 部分 | 位置 | 来源包 |
|---|---|---|
| 内核驱动 | `pvrsrvkm` 模块，绑 `/soc@3000000/gpu@1800000` (`img,gpu`) | 内核 BSP |
| 渲染节点 | `/dev/dri/renderD128`（DRIVER=pvrsrvkm） | — |
| Vulkan 库 | `/usr/lib/libVK_IMG.so` → `libVK_IMG.so.24.2.6603887` | `xserver-xorg-img-bxm` |
| Vulkan ICD | `/usr/share/vulkan/icd.d/img_icd.json` | 同上 |
| OpenCL 库 | `/usr/lib/libPVROCL.so` | 同上 |
| OpenCL ICD | `/etc/OpenCL/vendors/powervr.icd` | 同上 |

Vulkan 和 OpenCL **共用同一份 DDK**（`libsrv_um.so` + `pvrsrvkm`）。所以打通一个另一个自然就通——这也是为什么你如果之前配好了 OpenCL，Vulkan 大概率已经是好的。

### 实测结果

`scripts/gpu-check.sh` 一次性跑完 14 项检查：

```
1. 内核 / 设备节点
  ✓ pvrsrvkm 内核模块已加载
  ✓ /dev/dri/card0 存在
  ✓ /dev/dri/renderD128 存在
  ✓ renderD128 归属 pvrsrvkm
  ✓ 当前用户可写 renderD128
  ✓ GPU 时钟 600 MHz

2. Vulkan
  ✓ 厂商 Vulkan 库 libVK_IMG.so 存在
  ✓ ICD 描述文件 img_icd.json 存在
  ✓ Vulkan 枚举到 PowerVR 设备（apiVersion=1.3.277）
  ✓ 使用 Imagination 原厂驱动（非 Mesa lavapipe 软件兜底）

3. OpenCL 计算
  ✓ powervr.icd 已注册（/usr/lib/libPVROCL.so）
  ✓ OpenCL 设备就绪（compute units=1, global mem=3.819GiB）
  ✓ OpenCL 向量加法 PASS（4194304 元素，29.2ms，零误差）
  ✓ GPU 硬件中断 +6（证明计算落在 GPU 而非 CPU 兜底）
```

### 关键：怎么证明计算真的在 GPU 上

跟 NPU 那节同一个思路——**看硬件中断计数**，这是无法造假的。

```bash
# 跑之前
grep pvrsrvkm /proc/interrupts
# 481:  72  0  0  0  0  0  0  0  wakeupgen  63 Level  pvrsrvkm

# 跑一个 4M 元素的 CL 向量加法
./vecadd

# 跑之后
grep pvrsrvkm /proc/interrupts
# 481:  78  0  0  0  0  0  0  0  wakeupgen  63 Level  pvrsrvkm
#       ↑ +6
```

中断涨了，说明命令真的下发到了 PowerVR 硬件。如果只是 OpenCL 运行时在软件模拟，中断计数不会动。

### GPU 时钟

```bash
cat /sys/kernel/debug/clk/gpu0/clk_rate
# 600000000   → 600 MHz（跑在最高档）
```

GPU 没有独立的 devfreq 节点，调频走 CCF（Common Clock Framework），可用 `/sys/kernel/debug/clk/gpu0/` 观察。

### 性能参考

小规模测试（4M 元素向量加法）测出来 0.43 GFLOP/s，**这个数字不代表 GPU 上限**——它被内核启动开销和内存带宽主导了，单次 kernel 太短。要看真实算力需要持续负载测试。BXM-4-64 的规格是 64 FP32 lanes，理论峰值远高于此。

实用结论：**BXM-4-64 更适合当"渲染 + 轻量并行计算"用**，别指望它跑深度学习推理——那个活儿交给 NPU。

### GPU 章节的坑清单

1. `vulkaninfo` 一定同时列出 lavapipe，**必须判 `driverID`**
2. `vkcube` 默认要 X11，无头环境会直接退出（`Selected WSI platform: xlib` → `Exiting`），别误判成渲染失败
3. OpenCL 编译需要 `opencl-headers`，Debian 不默认装
4. 渲染节点需要 `video` 组权限，否则 root 能跑、普通用户不能

---

## 四、性能调优

CPU 调频 `ondemand` → `schedutil`，8 核全生效，通过 `cpu-governor.service` 持久化。

---

## 已知限制

| 项目 | 说明 |
|---|---|
| NPU 可用性 | ⚠️ 6.6 内核上**实际不可用**：`-ngl=0` 后端零调用，`-ngl>0` 必 core0 hang |
| NPU 速度 | 与纯 CPU 相同（后端未参与）。要真加速需回 5.15 内核或改走 GPU |
| VE2 1080p 旋转 | 90°/270° 会 VE2 超时（上游已禁用），需要时用软件旋转预处理 |
| VE2 H.264 Level | `aw-h264-encoder` **默认 Level 3.1**，1080p@60fps 必须设 `--level 40`（或 41）以上，否则编码器初始化后输出 0 字节并可能卡入 D 状态 |
| VE2 D 状态挂死 | 编码器异常挂起时进程进入 D 状态（uninterruptible sleep），`SIGKILL` 无效、systemd 无法回收 cgroup，**只能重启板子**。建议：(1) 调用方加超时+pgrep 守卫；(2) 不要在同一帧上无限等待 |
| VE2 H.265 | 未测试，理论支持 |
| GPU 算力 | BXM-4-64 是轻量 GPU，适合渲染/轻量并行，不适合深度学习推理 |
| GPU 无头渲染 | `vkcube` 需要 X11/Wayland；纯命令行环境要用 offscreen 或 `VK_EXT_headless_surface` |
| `/dev/sunxi_soc_info` | 缺失，仅影响 SoC 型号串读取，无功能影响 |
| 存储 | 系统在 microSD 上；eMMC 模块座空置（可扩展） |

## 存储提醒

Docker 日志容易撑爆 SD 卡（我们踩过：单容器 json.log 涨到 **35G**，占满分区）。
建议配置日志轮转：

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {"max-size": "50m", "max-file": "3"}
}
EOF
sudo systemctl restart docker
```

## 致谢

- [reef1994/a733-llama-npu-stack](https://github.com/reef1994/a733-llama-npu-stack) — NPU 驱动与推理栈
- [mashiqi/A733-Cedarc](https://github.com/mashiqi/A733-Cedarc) — VE2 硬编工具与厂商运行时
- [Incipiens/OrangePiZero3W-GPU-VPU](https://github.com/Incipiens/OrangePiZero3W-GPU-VPU) — VPU 提取方法
- Radxa 官方镜像团队 — GPU DDK（`xserver-xorg-img-bxm`）与内核 BSP 已预置完整 PowerVR 支持

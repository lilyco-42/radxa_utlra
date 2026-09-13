# A7A 硬件资源 ROI 指南 —— 哪个活该交给哪个硬件

> 日期:2026-09-13。全部数字来自 Radxa Cubie A7A(Debian 13 / 内核 6.6.98-4-aw2511)
> 真机实测,来源与证据见各节链接。目标:拿到板子的人按表决策,不重走弯路。

## 0. 先记住一个数字:内存带宽

SoC 是 32-bit LPDDR5-4800,理论 19.2 GB/s;llama.cpp 实测有效 ~12 GB/s。
**逐 token 生成(LLM decode)是带宽游戏**:Qwen2.5-0.5B Q8_0 每 token 要流过 0.68GB 权重,
decode 上限 ≈ 带宽 ÷ 模型大小 ≈ 18 tok/s,给谁执行都一样。这个约束决定了下面所有结论。

## 1. 决策表(先看这个)

| 你想干的事 | 用哪个硬件 | 入口 | ROI |
|---|---|---|---|
| LLM 推理(聊天/生成) | **CPU 8 核**(Qwen3-0.6B 22.4 / Qwen2.5-0.5B 18.1 tok/s) | `~/bin/a733-llama` 或系统 llama.cpp | ⭐ 零投入,已最优 |
| LLM 长文档阅读(prefill) | CPU(实测 107 t/s 级别);NPU 理论 10-20× 但见 §2 | - | NPU 需先通第三层,当前不可用 |
| 视频 H.264 转码 | **VE2 硬编**(1080p@60 ≈ 68fps,4K@22,仅 27% 单核) | `h264-ve2 in.mp4 out.mp4` | ⭐ 已接入管线 |
| 渲染 / 轻量并行计算 | **GPU**(Vulkan 1.3.277 + OpenCL 3.0,14/14 验证) | `scripts/gpu-check.sh` | ⭐ 零投入,已可用 |
| 安卓 App / 2D 游戏 / 挂机多开 | **redroid 容器**(Android 14) | `docker start a7a-android` + `scrcpy -s IP:5555` | ⭐ 手机/PC 浏览器皆可玩 |
| 重度 3D 手游 / FPS 竞技 | ❌ 别在这块板上(swiftshader 软渲染 + 串流延迟) | - | - |
| 视频检测/追踪/人脸/embedding(编码器类) | NPU **理论上**最合适——但见 §2 封存说明 | - | 修复前不可用 |
| 图片解码播放(8K H.265) | VE 解码(cedar 接口,libcedarc 在位) | cedarc API | 中,按需 |

## 2. NPU(3 TOPS):封存说明 —— 为什么现在别碰

**现状**:6.6 内核上两条驱动路线(galcore/TIM-VX、vipcore/VIPLite)在硬件执行阶段
44 秒超时挂死。三层根因已定位([三层根因文档](a733-npu-three-layer-rootcause.md)):
1️⃣ 时钟门控 ✅已绕过 2️⃣ 电源域关闭 ✅已绕过 3️⃣ 复位/安全内存窗口 ⛔卡在
**boot chain(ATF/U-Boot)**——内核态无法修复,双驱动交叉验证,铁证。

**就算修好(三个独立来源交叉验证)**:
- LLM decode:5.02 tok/s vs CPU 18.1 —— **负收益 3.6 倍**(带宽天花板,见 §0;
  独立佐证:msazanov 实验室调优数周全模型仅 0.97 tok/s)
- NBG 导出精度:Qwen2.5-0.5B int16 cosine 0.236(工具链层崩坏,petayyyy 实测)
- 逐算子 TIM-VX:llama.cpp 后端实测与纯 CPU 持平

**理论正收益场景(修复后才可兑现,现状不可用)**:
- CNN/ViT 编码器:检测(YOLO/Frigate)、人脸关键点、CLIP/SigLIP、embedding
  —— 静态 shape、整图 NBG 一次调用、无 KV-cache。petayyyy 的 SmolVLM SigLIP
  (NPU)+CPU LLM 混合已实测精确;社区 Frigate/YOLO/追踪仓库见 awesome 索引
- 长 prompt 预填充:3 TOPS 对 512 token 理论 10-20×(mllm-NPU 论文在同类移动 NPU
  上实现过 1000+ tok/s prefill)——但需第三层修复 + NBG 静态 prefill 图 + 工具链
  精度修复三件事全部就位

**重新评估触发条件**:①Allwinner/Radxa 发布修复 boot chain 的 BSP;②Orange Pi
`orange-pi-6.6-sun60iw2` 栈移植成熟(参考实现,petayyyy 已证明其 6.6 上 vipcore 可执行);
③有 CNN 类任务且 CPU/GPU 方案实测不达标。

## 3. GPU(PowerVR BXM-4-64 MC1):已可用,别高估

- ✅ Vulkan 1.3.277(原厂驱动,过 Khronos 一致性)+ OpenCL 3.0,600MHz
- ⚠️ 轻量 GPU:64 FP32 lanes。定位 = **渲染 + 轻量并行**,不要替代 NPU/CPU 做大推理
- 🎯 下一个值得试的方向:llama.cpp 的 **Vulkan 后端**(唯一未证伪的加速路线)
- 坑:`vulkaninfo` 会混列 lavapipe 软渲染,认准 `DRIVER_ID_IMAGINATION_PROPRIETARY`;
  无头环境 vkcube 直接退出不是失败;验证脚本 `scripts/gpu-check.sh`(14 项)

## 4. VE2(H.264 硬编):管线级可用

- 官方套件 8/8 + 旋转矩阵全过(IRQ 增量=帧数,输出时长与源一致)
- 1080p ~68fps(2.8× 实时)/ 4K ~22fps / 仅 27% 单核(x264 要 700%+)
- 已修上游两个 bug(MP4 时长错乱、退出码),入口 `h264-ve2`
- 限制:90°/270° 旋转上游禁用(用软件旋转预处理);H.265 未测

## 5. CPU:当前一切推理的实际承担者

- 2×A76 + 6×A55,schedutil 已持久化
- 实测(Q8_0):Qwen3-0.6B 22.4 tok/s,Qwen2.5-0.5B 18.1 tok/s,长 prompt ~107 t/s
- 有效内存带宽 ~12 GB/s —— 已经贴近 DRAM 天花板,没有"再优化"空间

## 6. 安卓容器(redroid)边界

- ✅ 2D 游戏、App 多开、挂机、普通 App;手机/PC 经 scrcpy 游玩(客户端渲染)
- ❌ 重度 3D(软渲染)、FPS 竞技(延迟)、国产手游反作弊(FPS 类明封模拟器,
  例:三角洲行动官方 G.T.I. 条款)

## 7. 一页速查

```
LLM       → CPU(-ngl 0)          Qwen3-0.6B/Qwen2.5-0.5B 系
转码      → h264-ve2              VE2 硬编,低占用 4K
安卓      → redroid + scrcpy      轻中度 App/2D
渲染/CL   → GPU Vulkan/OpenCL     scripts/gpu-check.sh 先体检
NPU       → 封存                  触发条件见 §2
```

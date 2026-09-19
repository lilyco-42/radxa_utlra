# NPU 调查残留产物（investigation artifacts）

本目录收录 2026-09-19 在 Radxa Cubie A7A (Allwinner A733) 上调查 NPU 量化推理问题的**直接证据产物**。
根因与结论见 `docs/a733-npu-three-layer-rootcause.md` 与 `docs/a733-npu-usable-path.md`。

> 结论速记：Radxa 原厂 `6.6.98-4-aw2511` BSP 没把 NPU 量化通道（NSI 互连 / 量化 MAC 阵列）接好，
> float 图能跑、量化图挂死（`VIPDRV_WAIT_TASK=-1`，`vipcore_0` 中断计数 0）。
> 换装 Rabs9 的 `6.6.98+` 内核后，量化推理全通（YOLOv5s 出检测框、官方 A733 量化 NBG `ret=0`+IRQ）。
> ⚠️ 换内核的写盘 + 重启触发了 SD 卡 `ext4lazyinit` 坏位图缺陷（见工作记忆），该卡此后不可再重启/再写。

## 本目录内容

### `npu-clk-experiment/`（量化路径时钟证伪实验）
- `npu_clk_fix2.c` — 扩展内核模块：在 `npu_clk_fix.c` 基础上额外 `clk_prepare_enable` `clk_bus`(`npu-gate`)。
- `Makefile` — 板端 `/tmp`(tmpfs) 编译用，不落 SD。
- 用途：**证伪"量化通道时钟门控没开"假设**——实测打开 `npu-gate` 后量化 NBG 仍挂死，排除时钟门控。
- 注意：这是实验证据，**不是最终修复**。最终修复是换 Rabs9 `6.6.98+` 内核。

### `kws-min/`（vpm_run 测试架 + A733 专用模型，实物证据）
- `vpm_run.c` — 官方 `ZIFENG278/ai-sdk` 的 `vpm_run` 最小 runner（A733 上编译运行）。
- `inc/vip_lite.h`, `inc/vip_lite_common.h` — VIPLite 2.0 头文件。
- `lib/libVIPhal.so`, `lib/libNBGlinker.so` — VIPLite 2.0.3.2-AW-2024-08-30 用户态库（与官方同款）。
- `models/` — A733 专用 NBG（target `0x1000003b`）：
  - `joiner_float_a733.nb` (181KB) — **PASS** `ret=0` 91us，IRQ 0→1
  - `decoder_float_a733.nb` (621KB) — **PASS** `ret=0` 314us，IRQ 1→4（3 runs）
  - `encoder_float_a733.nb` (6.9MB) — **PASS** `ret=0` 25.5ms（float，文档记录）
  - `vocoder_int16_a733.nb` (2.1MB) — **FAIL**（原内核量化图挂死，IRQ=0）
  - `official_a733.nb` (964KB) — 官方 `ai-sdk operator/v3/network_binary.nb`（ShuffleNetV2_uint8，版本 `0x1001e`）：原内核挂死，新内核 `ret=0`+IRQ
  - `official_in.dat` (150KB) — 官方示例输入
- `s_official.txt`, `sample_android.txt` — `vpm_run` 配置（network/input 列表）
- 用途：在板端直接复现"float 通 / 量化挂"及"换内核后量化通"的决定性证据。运行：`LD_LIBRARY_PATH=./lib ./vpm_run -s s_official.txt -l 1`。

### 对比图
- `a733-npu-yolov5-demo-result.png` — 真实板端 YOLOv5s demo：模型加载/建网/准备/喂输入 PASS，推理 `VIPDRV_WAIT_TASK=-1` 失败（原内核状态，未伪造检测框）。
- `a733-npu-usable-proof.png` — 可用性对照：KWS 三模型 PASS（真实 IRQ）+ YOLOv5s 失败 + ai-sdk 自带 T527 NBG 不适用。
- `a733-npu-quant-vs-float.png` — float 图 vs 量化图判别矩阵（量化图全挂 → 根因收敛到量化计算路径）。

### 报告
- `a733-npu-minimal-experiment.md` — 实验过程记录（含"NPU 已证可用、仅 YOLOv5s 特定 NBG 失败"的修正结论）。

## 未纳入本目录的产物（在 `D:\Code\mc\artifacts\`，本地仅，不入 git）

| 路径 | 大小 | 说明 |
|---|---|---|
| `npu-exp/allwinner-model-zoo.tar.gz` | 208MB | 官方模型库。超大，不入库；可从 `https://dl.radxa.com/cubie/allwinner-model-zoo.tar.gz` 重新下载（公开版为 v0.9.0，与本地逐字节相同） |
| `logcat_live.txt` | 73MB | 板端 logcat 抓取日志，超大不入 git |
| `npu-exp/` (bundle) | 238MB | YOLOv5 demo 源码 + dog.jpg 等；含上面 208MB 包。demo 源码已通过 `docs/` + `scripts/` 在仓库内体现 |
| `ai-sdk/` | 9.7MB | `ZIFENG278/ai-sdk` 克隆（vpm_run 源码/库），可重新 clone |
| `kws-repo/` | 20MB | `Ronin-1124/cubie-a7a-voice-assistant` 克隆（KWS 预编译 nb 来源），可重新 clone |
| `rsh.py` / `rsftp.py`（artifacts 根） | 8KB | 板端远程工具本地副本；**仓库已有更完整版** `recovery/tools/rsh.py`，不重复入库 |

## 复现步骤（摘要）

1. 板端：`apt`/官方镜像装好 VIPLite 2.0.3.2 + `/dev/vipcore` 可用。
2. 编译测试架：`aarch64-linux-gnu-gcc -O2 -Iinc -o vpm_run vpm_run.c -Llib -Wl,-rpath,'$ORIGIN/lib' -lNBGlinker -lVIPhal -lm`
3. 运行量化 NBG（需 `6.6.98+` 内核）：`LD_LIBRARY_PATH=./lib ./vpm_run -s s_official.txt -l 1`
4. 期望：`run time for this network 0: ~2900 us` / `vpm run ret=0` / `vipcore_0` 中断计数 +1。

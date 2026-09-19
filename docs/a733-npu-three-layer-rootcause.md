# A733 NPU「执行挂死」三层根因与运行时绕过(内核 6.6)

日期:2026-09-13。板卡:Radxa Cubie A7A,Debian 13,内核 `6.6.98-4-aw2511`。
本文是对 `a733-npu-final-verdict.md`(结论:NPU 两条路线执行阶段挂死)的**根因定位续篇**:
挂死不是玄学,是三层可定位的初始化缺陷,前两层已可运行时绕过。

## 一句话

> 6.6 BSP 上 galcore 驱动**只 `clk_prepare` 从不 `clk_enable`**(NPU 核心无时钟),
> 且 **`pd_npu` 电源域保持 off**(NPU 无供电)——没电没钟的硬件当然永远不回 idle。
> 两者都可用运行时手段修复(本仓库 `modules/npu_clk_fix.c` + `scripts/install-npu-clk-fix.sh`);
> 修复后提交不再挂死系统,但硬件仍不执行命令 → 第三层(复位/互连)待解。

## 根因链(实测证据)

### 第 1 层:核心时钟被门控(已修复)

```
/sys/kernel/debug/clk/clk_summary:
  pll-npu   en=0 prep=2  rate=1008000000   ← prepare 了,从没 enable
  npu       en=0 prep=1  rate=1008000000   ← 同上
```

- DT 节点 `npu@3600000` 声明 5 个时钟(clk_npu/clk_parent/clk_bus/clk_mbus_gate/clk_ahb_gate),
  galcore probe 只 prepare 不 enable;运行时恢复回调同样不碰 CCF 时钟。
- **修复**:`modules/npu_clk_fix.c` —— 对 `3600000.npu` 设备 `clk_get(dev,"clk_npu")` +
  `clk_prepare_enable()`,CCF 自动连带使能父时钟 pll-npu。
- 验证:加载后 `npu en=1`,`pll-npu en=1`。

### 第 2 层:电源域关闭(已修复)

```
/sys/kernel/debug/pm_genpd/pm_genpd_summary:
  pd_npu            off-0
    /devices/.../3600000.npu    suspended
```

- 即使时钟修好,`pd_npu` 仍 off —— NPU 无 VDD,照样不执行。
- **修复**:PM QoS 钉死 —— `echo on > /sys/bus/platform/devices/3600000.npu/power/control`
  → 设备 active → `pd_npu on`。
- galcore dmesg 同时暴露 BSP PM bug:`Unbalanced pm_runtime_enable!`

### 第 3 层:复位/互连(待解)

时钟+供电全开后,任务提交仍 **44 秒无完成** → galcore 看门狗
`GPU[0] core0 hang, automatic recovery` → `recovery done` → TIM-VX 返回失败。

关键差异:挂死**不再拖垮系统**——llama.cpp 收到错误后回落 CPU 继续跑,板子全程稳定。
剩余嫌疑(按优先级):

1. 复位线时序(DT 声明 npu_rst/npu_axi_rst/npu_ahb_rst,BSP glue 的 deassert 顺序)
2. NSI 互连控制器(`2020000.nsi-controller`,genpd 里挂在 npu 域下,可能需要独立初始化)
3. 上电时序(npu-supply regulator vs 时钟使能的先后)

**参考实现已定位**:`orangepi-xunlong/linux-orangepi` 分支 `orange-pi-6.6-sun60iw2`
——petayyyy 实测该 BSP 的 6.6 上 `/dev/vipcore` 可正常工作。下一步 = diff 它的
galcore/ vipcore glue 与 NPU 设备树,克隆缺失的初始化步骤。

## 修复前 vs 修复后(同一操作)

| | 修复前 | 修复后 |
|---|---|---|
| NPU 提交(-ngl≥2) | 内核 wedged,板子断网"变砖"(实测两次) | 44s 超时 → 自动恢复 → 错误回落 CPU,系统稳定 |
| `llama_decode` | 卡死 | `ret=-3` 干净失败,进程存活 |
| 重启后状态 | 挂死复现 | 服务自动恢复安全态 |

## 毒性操作清单(血泪验证)

1. **`rmmod galcore` = 内核崩溃**(两次实测,每次都把板子搞到断网需断电恢复)。永不执行。
   调试需重载驱动参数时,走重启 + 修改 service。
2. `/proc/interrupts`、`clk_summary`、`pm_genpd_summary` 需要 root 读;nohup 脚本里的
   `sudo` 无 tty 会静默失败 —— 用 `echo <pw> | sudo -S ... < /tmp/.pw`(600 权限密码文件)。
3. `llama-cli -no-cnv` 生成完成后不退出,在交互提示符死循环(上游已知问题),基准测试
   必须 `timeout` 包裹 + `</dev/null`。
4. 本启动 galcore 可能被 `vipcore` 抢占绑定(双驱动互斥,见架构图),
   `npu-clk-fix.service` 的 ExecStartPre 已含纠正逻辑。

## 安装(在板子上)

```bash
git clone https://github.com/lilyco-42/radxa_utlra.git
cd radxa_utlra
sudo ./scripts/install-npu-clk-fix.sh
# 验证
sudo cat /sys/kernel/debug/pm_genpd/pm_genpd_summary | grep pd_npu   # → on
sudo grep -E "^\s+npu\s" /sys/kernel/debug/clk/clk_summary           # → en=1
```

注意:装完后 `-ngl≥2` 仍会 TIM-VX run failed(第三层未解),但**不再挂死**。
LLM 推理请继续用 CPU(-ngl 0,Qwen2.5-0.5B ≈ 18 t/s)或评估 GPU 路线。

---

## 2026-09-19 补充:重刷后复现 + 修掉一个安装脚本的 bug

板子重刷系统后重新走了一遍最小实验,顺带发现并修掉两个真问题。

### A. 一个**通用**的 yolo demo 也复现了同样的挂死

不再只有 llama.cpp 复现 —— Model Zoo 的 yolov5 demo(走 VIPLite/NBG,不经过 TIM-VX)
表现完全一致:

```
create network 0: 18195 us.      ← 建网成功
prepare network: 3186 us.        ← 准备成功
feed input cost: 29106 us.       ← 输入喂入成功
cid=0x1000003b                   ← 硬件 ID 读到了
fail to ioctl vipcore, command[4]:VIPDRV_WAIT_TASK, status=-1   ← 执行挂死
```

dmesg 同样是 `FE not idle / SH not idle / NN not idle` → `VIP not going to idle`,
`/proc/interrupts` 里 `vipcore_0` 计数**恒为 0**。

→ **这条排除"某个上层框架的问题"**,坐实是驱动/内核层。

### B. ⚠️ 安装脚本的绑定逻辑在**没有 galcore 的内核上会把 NPU 弄坏**

原 `npu-clk-fix.service` 的 `ExecStartPre` 是**无条件**执行的:

```sh
if [ -e .../drivers/vipcore/3600000.npu ]; then echo 3600000.npu > .../vipcore/unbind; fi
if [ ! -e .../drivers/galcore/3600000.npu ]; then echo 3600000.npu > .../galcore/bind; fi
```

它假设 galcore 与 vipcore **都存在**并互相抢占。但 Radxa 这个内核是:

```
CONFIG_AW_NNA_VIP=y                  ← 有 vipcore
# CONFIG_AW_NNA_GALCORE is not set    ← 没有 galcore
```

于是执行结果是:**先解绑 vipcore,再往不存在的 galcore 绑定(静默失败)** →
NPU **谁都没绑**,`/dev/vipcore` 直接消失,`pd_npu` 保持 off —— **比不修还坏**。

现场症状:`ls /sys/bus/platform/devices/3600000.npu/driver` 不存在、
`/dev/vipcore: No such file or directory`、`pd_npu off-0`。

**修复**:先探测哪个驱动存在,再决定绑谁(见新版 service 的 ExecStartPre)。
手动恢复命令(如已踩中):

```bash
echo 3600000.npu > /sys/bus/platform/drivers/vipcore/bind
# 之后 /dev/vipcore 会回来, pd_npu 变 on
```

**修复后实测**(重启服务即可,无需重启机器):

| 项 | 修复前 | 修复后 |
|---|---|---|
| 绑定的驱动 | (未绑定) | `vipcore` |
| `/dev/vipcore` | 不存在 | 存在 |
| `pd_npu` | `off-0` | **`on`** |
| `npu` 时钟 | `en=0` | **`en=2`** |

### C. 第三层仍然未解(修复后重测确认)

时钟 + 电源域全部就位后**再跑一次 yolov5 demo**:

```
feed input cost: 34524 us.
fail to ioctl vipcore, command[4]:VIPDRV_WAIT_TASK, status=-1
nbglk_network_segment_wait: timeout
fail to run network, status=-1
```

`/proc/interrupts` 的 `vipcore_0` **依然是 0**。

→ 对 **yolov5s 这条路径**，仍然是 `VIPDRV_WAIT_TASK` 超时；
但不能把这个结果推广成"NPU 整体仍不执行"。`a733-npu-usable-path.md` 已记录
同一 `vipcore + VIPLite + vpm_run` 路径下，KWS 的 joiner / decoder / encoder
三件 NBG 都能 `ret=0`，所以这里的失败必须按**模型/算子兼容性或该网络的复位触发路径**继续拆，
不能再写成 NPU 的整体结论。
差别只在"失败是否安全" —— 修复后是干净超时,不再拖垮系统。

### D. 结论没变,但更精确了

| 层 | 状态 |
|---|---|
| 1 时钟门控 | ✅ 已修(运行时,`npu_clk_fix.ko`) |
| 2 电源域 | ✅ 已修(运行时,PM QoS) —— **但安装脚本需先修上面的绑定 bug** |
| 3 复位/互连 | ⚠️ **对 yolov5s / 部分网络路径仍未解**；但 KWS 三件套已 `ret=0`，不能推广为 NPU 整体不可用；继续 diff `orangepi-xunlong/linux-orangepi` 的 `orange-pi-6.6-sun60iw2` |

---

## 2026-09-19 补充(2): 量化路径判别 —— 推翻"全局复位/互连"旧框定

重刷后做了一轮**控制变量判别实验**(同一 `vpm_run` + A733 专用 NBG,只换模型内部计算类型),
结论比"第三层=复位/互连"更尖:

| 模型 | I/O 格式 | **NBG 内部计算** | 结果 |
|---|---|---|---|
| KWS joiner / decoder / encoder | FP32 | **float 图** | ✅ `ret=0`,`vipcore_0` IRQ 增长 |
| `vocoder_int16_a733.nb` (2.1MB) | FP32 | **int16 量化图** | ❌ `VIPDRV_WAIT_TASK=-1`,IRQ 恒为 0 |
| `yolov5s_rt_uint8_a733.nb` (5MB) | UINT8 | **uint8 量化图** | ❌ `VIPDRV_WAIT_TASK=-1`,IRQ 恒为 0 |

判别结论(当场验证,非推断):

1. **不是 I/O 格式**:vocoder 输入也是 FP32,照样挂;yolov5s 输入是 UINT8 也挂;KWS 输入 FP32 却成。
2. **不是模型大小**:6.9MB 的 float encoder 能跑,2.1MB 的 int16 vocoder 却挂。
3. **共同因子是"内部量化图"** —— 任何**量化 NBG(int8/uint8/int16)都在执行阶段挂死、无完成中断**;
   纯 **FP32 NBG(KWS)完整跑完并触发 IRQ**。

→ 因此第三层应**重新定性**:不是"NPU 全局没初始化好"(KWS 已证核心/时钟/供电/中断都正常),
而是 **当前 Radxa 6.6 BSP 下,NPU 的量化计算路径(量化 MAC 阵列 / quantize-dequantize 算子 / NN 引擎的量化模式配置)未使能或未正确初始化**,float 路径完全正常。

dmesg 签名与此一致:`FE not idle / SH not idle / NN not idle` → `VIP not going to idle`(NN 引擎卡在量化算子)。

**修正后的修复方向**(比"无差别 diff 复位/互连"更聚焦):
diff `orangepi-xunlong/linux-orangepi` 分支 `orange-pi-6.6-sun60iw2` 时,重点看它相对 Radxa BSP
**为量化路径多做了什么** —— 例如量化 MAC 阵列的附加时钟/复位、量化固件 blob 加载、或 NN 引擎模式寄存器。
(此项需要改内核/设备树 + 重启验证;SD 卡写路径有缺陷,动之前先接 USB SSD。)

**注意**:此结论不推翻前两层的运行时修复(时钟/电源域),只把"第三层"从"全局"收窄为"量化路径"。

---

## 2026-09-19 补充(3): 官方 A7A 文档分析 —— 量化在 A733 上本应可跑,失败是环境/模型问题

通读 Radxa 官方 A7A NPU 文档(`docs.radxa.com/cubie/a7a/app-dev/npu-dev/`,注意旧本地文档写的 `a7z` 是错路径,真实是 `a7a`),结论被进一步修正:

### A. 官方明确证明:量化模型在 A733 上能跑

1. **vpm_run 官方示例**(`cubie-vpm-run`):
   ```
   input 0 dim 3 224 224 1, data_format=5(INT16), quant_format=1(DFP), dfp=13
   output 0 dim 1000 1 0 0, data_format=1(FP16), none-quant
   ... run time for this network 0: 3160 us ... vpm run ret=0
   ```
   → 一个**量化(INT16/DFP)模型**在 A733(`cid=0x1000003b`)上 `ret=0` 跑通。
2. **Model Zoo YOLOv5 官方页**(`model-zoo/yolov5`):跑的就是 `yolov5s_rt_uint8_a733.nb`(UINT8/TF_ASYMM),
   同一 VIPLite 2.0.3.2,结果:
   ```
   run time for this network 0: 20142 us.  detection num: 3  (dog 92% / truck 69% / bicycle 52%)
   ```
   → **我们失败的同款 yolo uint8 模型,官方在 A733 上 49.8 FPS 跑通**。
3. **NPU 版本对照表**(`cubie-acuity-usage`):A733 = NPU v3 = **NPU_SW v2.0**;我们用的 VIPLite 2.0.3.2 正对应 v2.0,版本对得上。
4. **ACUITY 支持** A733 的 UINT8/PCQ(INT8)/INT16/BF16 量化编译。

→ **量化在 A733 上不是硬件/硅片限制,也不是内核"全局未初始化"。我们板子的失败是这块板子的具体环境/模型/驱动状态问题。**

### B. 用 NBG 头部把失败拆成两个不同原因

本地三个模型的 NBG 头(`56 50 4d 4e`=VPMN + version(4B LE) + target=0x1000003b):

| 模型 | NBG 版本 | 大小 | 板端结果 |
|---|---|---|---|
| KWS joiner (float) | `00 00 02 00` = **0x20000** | 181KB | ✅ ret=0 |
| vocoder int16 | `00 00 02 00` = **0x20000** | 2.1MB | ❌ 挂 |
| yolo uint8 | `1e 00 01 00` = **0x0001001e** | 5120576 | ❌ 挂 |

- **vocoder int16** 的 NBG 版本 `0x20000` 与**能跑的 KWS float 完全相同**,却挂 → 这是板上内核/驱动**量化计算路径**的真实缺口(同版本 float 图能跑、量化图不能)。
- **yolo uint8** 版本是老的 `0x0001001e`,且大小 `5120576` ≠ 官方的 `5564152`(差 ~433KB)。

### C. 关键发现:本地模型库是旧版

- 官方当前 Model Zoo:`awnpu_model_zoo-v1.0.0-20260423-f562dd16`
- **我们本地的是:`awnpu_model_zoo-v0.9.0-20260116-83a67d4b`**(早 3 个月、低一个大版本)
- 下载地址:`https://dl.radxa.com/cubie/allwinner-model-zoo.tar.gz`(约 199MB)

→ yolo 的失败**很可能是本地模型文件本身就是旧版/不兼容构建**,而不是量化路径的锅(与 vocoder 是两回事)。

### D. "下载官方模型"假设证伪 + 旧 NBG 版本不是主因

1. 从 `https://dl.radxa.com/cubie/allwinner-model-zoo.tar.gz` 拉下来的包,内部是
   `awnpu_model_zoo-v0.9.0-20260116-83a67d4b` —— **和我们本地副本逐字节相同**(yolo nb 同为 5120576 字节、版本 `0x0001001e`)。
   文档文字写的 `v1.0.0-20260423`(5.56MB 那个能跑的 yolo)实际锁在**全志客户服务平台**(open.allwinnertech.com,需登录),
   公开渠道拿不到。→ 我们手上的 yolo nb **已经是公开最新版**,重新下载不会改变任何事。
2. 所以 yolo 的 `0x0001001e` 是**旧 NBG 格式**这点,最多是叠加因素,不能解释全部失败——
   因为下面 E 节的**决定性实验**证明:用**官方 A733 专用、同版本 `0x0001001e`** 的量化 NBG,在我们板子上**照样挂**。

---

## 2026-09-19 决定性实验:锁定 Radxa BSP 内核/设备树缺陷(非模型、非库、非时钟)

### E1. 决定性对照:同一份官方量化 NBG,官方板能跑、我们的板挂

- 用 `ZIFENG278/ai-sdk` 里 `make install AI_SDK_PLATFORM=a733` 实际安装的 `operator/v3/network_binary.nb`:
  - 头部 `56 50 4d 4e | 1e 00 01 00 | 3b 00 00 10` → **A733 专用**(target `0x1000003b`)、版本 `0x0001001e`、
    正是官方文档 `cubie-vpm-run` 里跑通 `ret=0` 的那个 **INT16/UINT8 量化**样本(`ShuffleNetV2_uint8_NCHW`)。
- 在我们板子(内核 `6.6.98-4-aw2511`)上跑同一文件 + 官方 `input_0.dat` + 同源 ai-sdk v2.0 库:
  ```
  init vip lite, driver version=0x00020003...  VIPLite driver software version 2.0.3.2-AW-2024-08-30
  input 0 dim 224 224 3 1, data_format=2(UINT8), quant_format=2(TF_ASYMM)
  [viphal_os_call_drv] fail to ioctl vipcore, command[4]:VIPDRV_WAIT_TASK, status=-1
  nbglk_network_segment_wait: wait network=...ShuffleNetV2_uint8_NCHW timeout
  vpm run ret=-2     ← irq 计数不变(无完成中断)
  ```
- **控制变量全部相同**:模型文件 = 官方同款、userspace 库 = 官方同款 ai-sdk v2.0、内核驱动版本 =
  官方同款 `VIPLite 2.0.3.2-AW-2024-08-30`。唯一不同的变量是**板子/内核 BSP 本身**。
- 而 `petayyyy/a733_npu_driver` 实证:**同一颗 A733 NPU(int16 量化)** 在
  Radxa Cubie **A7Z 的 `5.15.147-21-a733`** 与 Orange Pi Zero 3W 的 **`6.6.98-sun60iw2`**(Allwinner 参考 BSP)上
  都能跑通 int16 模型(SmolLM2-135M int16 20.7 tok/s、MobileCLIP int16 22.6ms)。

→ **铁证:A733 的 NPU 量化路径本身没问题;出问题的是 Radxa 这版 `6.6.98-4-aw2511` 的 BSP 内核/设备树没有把量化计算通道接好。**

### E2. 用户态排除:不是 NPU 主时钟/电源域,也不是 npu-gate 时钟门控

现场已确认 float 模型(KWS)完全正常,且我们已把 NPU 全部时钟/电源域打开后重测:

| 资源 | 状态(修复后) | 量化 NBG 结果 |
|---|---|---|
| `npu` / `pll-npu`(主计算时钟) | enable=1 (1.008GHz) | 仍挂 |
| `npu-mbus-gate` / `npu-ahb-gate` | enable=1 | 仍挂 |
| `pd_npu`(主电源域) | on、`3600000.npu` active | 仍挂 |
| `npu-gate`(→ clk_bus) | **实验性 enable=1**(扩展模块 `npu_clk_fix2` 打开) | **仍挂** |
| `nsi_master/npu`(NSI 互连主口电源子域) | **suspended**(待机断电) | — |

- 实验:写了内核模块 `npu_clk_fix2.c`,在 `/tmp`(tmpfs,不落 SD)编译、`insmod` 把 `clk_npu`+`clk_bus`+`mbus`+`ahb` 全部 `clk_prepare_enable`;
  `npu-gate` 确认变 enable=1,但官方量化 NBG **依旧 `VIPDRV_WAIT_TASK` 超时、IRQ 不变**。
  → **排除"量化通道的时钟门控没开"这一假设。** 模块已 `rmmod` 卸载、原 `npu_clk_fix` 已恢复,float 推理未受影响(ret=0)。

### E3. 最终根因(收敛)

- ❌ 不是模型问题(官方 NBG 也挂)
- ❌ 不是 userspace 库/驱动版本(同款 ai-sdk v2.0、同款 VIPLite 2.0.3.2)
- ❌ 不是 NPU 主时钟/主电源域(float 能跑)
- ❌ 不是 `npu-gate` 时钟门控(实测打开仍挂)
- ✅ **Radxa `6.6.98-4-aw2511` 这个 BSP 构建,在 NPU 设备树/NSI 互连/量化单元接线层面不完整**:
  `nsi_master/npu` 电源子域处于 suspended,而同一硅片 + 同一驱动 + 同一套库在
  Allwinner 参考 BSP(`5.15.147` / `6.6.98-sun60iw2`)上量化推理是通的。
  → 量化算子执行所需的硬件通道(最可能是 NSI 互连主口或量化 MAC 阵列的供电/复位)未被该 BSP 使能。

### E4. 修复方向(需换内核/设备树,非用户态可解)

量化推理要真正可用,必须让 NPU 的量化通道被正确接线。已知可行路径:

1. **Rabs9/radxa-cubie-a7a-kernel**:同样是 6.6.98 但带自定义设备树/补丁,明确写"NPU 3 TOPS、ResNet50 ~7.8ms 已验证"。
   提供 `.deb` 内核包,可在现有 Debian 13 系统上**只换内核**(不用整盘重刷),但仍需重启。
2. **Allwinner 参考 BSP `6.6.98-sun60iw2`**(Orange Pi 那条线):petayyyy 已实证量化可跑;本质就是"把 NPU 节点按参考 DT 接好"。
3. **自补 Radxa 内核 DT**:把 `3600000.npu` 按参考实现补上 NSI 主口电源域关联 / 量化单元时钟复位,重新编译内核。

⚠️ 以上都涉及**改内核 + 重启**;当前 SD 卡写路径有已知缺陷,**动之前先接 USB SSD**,且需你确认后再执行。
在此之前,板上 NPU 的**纯 FP32(float)推理(KWS 等)是可用的**,量化模型(int8/uint8/int16)暂不可用。

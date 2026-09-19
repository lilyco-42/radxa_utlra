# A733 NPU 最小化实验报告（2026-09-19）

> 目标：用**最小成本**验证 A733 的 NPU 能不能跑起来。
> 方法：按 lyco 信条 5 —— 最小化验证可行性，不成功就查官方文档 + GitHub。
> 结论：**本次选的 YOLOv5s NBG 能跑通到"提交任务"，但执行超时；这不是 NPU 整体不可用。**
> 仓库已有对照实验：同一 `vipcore + VIPLite + vpm_run` 路径下，KWS 的 joiner / decoder / encoder 三件 NBG 已经 `ret=0`。

---

## 一、结论速览

| 环节 | 结果 |
|---|---|
| NPU 设备节点 | ✅ `/dev/vipcore` |
| 内核驱动 | ✅ 内置，devfreq 三档（492/852/1008 MHz），governor `performance` |
| 厂商运行库 | ✅ 装上后可用（`libNBGlinker.so` + `libVIPhal.so`，共 209 KB） |
| 编译 demo | ✅ 原生 aarch64 编译成功（**不需要 Docker**） |
| **加载 NBG 模型** | ✅ 5,120,576 字节，正确 |
| **创建网络** | ✅ 18,195 µs |
| **准备网络** | ✅ 3,186 µs |
| **喂入输入** | ✅ 29,106 µs |
| **执行 YOLOv5s 网络** | ❌ **挂死** —— `VIPDRV_WAIT_TASK` 超时，中断未触发 |
| **NPU 已知可用网络** | ✅ KWS `joiner` 91 µs、`decoder` 314 µs、`encoder` 25.5 ms，均 `ret=0` |

**准确结论**：NPU 整体是可以工作的；本次失败限定在 **YOLOv5s 这个 NBG / 网络路径**，不能推广成"NPU demo 都不行"。

---

## 二、关键认知：Docker 只在"转换"环节需要

官方文档给人的第一印象是"整套 NPU 开发需要 x86 Linux + Docker 容器"。
**实际上 Docker 只用于把 ONNX 转成 NBG**（`ubuntu-npu` 容器 + Acuity 工具链）。

**只要拿到现成的 `.nb` 模型，就完全不需要 Docker** ——
Model Zoo 里自带一个 A733 的预编译模型：

```
examples/yolov5/model/yolov5s_rt_uint8_a733.nb    5,120,576 字节
```

（其余 `.nb` 是 T527 / MR536 的，不能用。）

---

## 三、走通的最小路径（可复现）

### 3.1 只需要这些（从 199 MB 的 Model Zoo 里挑）

| 组件 | 大小 |
|---|---|
| `examples/yolov5/`（去掉 `convert_model/` 那 58 MB ONNX 和 `figures/`） | 5.3 MB |
| `common/npuruntime/lib_linux_aarch64/A733/`（2 个 .so） | 209 KB |
| `3rdparty/opencv/opencv-4.9.0-aarch64-linux-sunxi-glibc.zip` | 22.5 MB |
| **合计** | **~28 MB** |

板端布局（CMakeLists 靠相对路径找依赖，**目录结构不能改**）：

```
~/npu_zoo/
├── common/npuruntime/
├── examples/yolov5/
└── 3rdparty/opencv/opencv-4.9.0-aarch64-linux-sunxi-glibc/
```

### 3.2 板端依赖

```bash
apt-get install -y --no-install-recommends g++ cmake     # gcc/make 已有
```

`unzip` **不用装** —— 板上有 Python，用 `python3 -m zipfile -e x.zip .` 解压。

### 3.3 ⚠️ 构建时的坑：CMakeLists 靠编译器名字判断架构

```cmake
elseif(CMAKE_C_COMPILER MATCHES "aarch64")     # ← 靠字符串匹配
    set(SYS_ARCH linux_aarch64)
else()
    set(SYS_ARCH linux_armhf)                   # ← 原生构建会掉进这里
endif()
```

交叉编译时编译器叫 `aarch64-none-linux-gnu-gcc`，能命中；
但**原生构建时 `CMAKE_C_COMPILER` 是 `/usr/bin/cc`，不含 "aarch64"** →
被当成 armhf → OpenCV 路径指到 `gnueabihf` → `find_package(OpenCV)` 失败。

**解法（不用改任何文件）**：Debian aarch64 上有同名别名，显式传进去即可：

```bash
cmake -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
      -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++ \
      -DTARGET_NAME=A733 -DEXTERN_DEFINE_TARGET=ON ..
make
# → yolov5_demo_a733 (7.4 MB)
```

### 3.4 运行

```bash
export LD_LIBRARY_PATH=$HOME/npu_zoo/common/npuruntime/lib_linux_aarch64/A733:$LD_LIBRARY_PATH
sudo ./yolov5_demo_a733 -nb ../model/yolov5s_rt_uint8_a733.nb \
                        -i ../model/dog.jpg -l 1 -m 20
```

---

## 四、失败现场（完整证据链）

### 4.1 用户态输出

```
nbg name=../model/yolov5s_rt_uint8_a733.nb, size: 5120576.
create network 0: 18195 us.
prepare network: 3186 us.
buffer ptr: 0xaaaadeae47c0, buffer size: 1228800
feed input cost: 29106 us.
network: 0, loop count: 1
viphal_os_call_drv[294], fail to ioctl vipcore, command[4]:VIPDRV_WAIT_TASK, status=-1
viphal_wait_task[517], fail to check status=-1
nbglk_network_segment_wait[168], network=0xffffb32bf02c hang capture....
nbglk_wait_network[2713], fail to wait network yolov5s_rt_uint8_NCHW finish
-------Feature DB Start------
nbg version=0x0001001e
cid=0x1000003b                      ← NPU 硬件 ID 读到了
network name=yolov5s_rt_uint8_NCHW
-------Feature DB End--------
fail to run network, status=-1
```

### 4.2 内核侧（dmesg）—— 硬件真的卡住了

```
npu[570b][570b] do wait mt thread fail, task_id=0x80040000, status=-1
npu[570b][570b] vipcore, fail to ioctl, command[4]: VIPDRV_WAIT_TASK, status=-1
npu[570b][570b] wait dev0 hw0 idle, FE not idle.
npu[570b][570b] wait dev0 hw0 idle, SH not idle.
npu[570b][570b] wait dev0 hw0 idle, NN not idle.
npu[570b][570b] error, VIP not going to idle.
npu[570b][570b] device0 not going to idle
```

**NPU 的三个硬件单元（FE 前端 / SH 着色器 / NN 神经网络）全部卡在非空闲状态。**

### 4.3 决定性证据：中断计数 = 0

```
$ grep -iE 'vip|npu' /proc/interrupts
457:  0 0 0 0 0 0 0 0  wakeupgen  65 Level  vipcore_0
      ↑ 八个 CPU 核全是 0
```

**`vipcore_0` 中断从来没触发过。** 这就是"等一个永远不来的中断"。

---

## 五、根因（2026-09-19 更正）

> ⚠️ **本节原先写的是「内核把 NPU 的 IOMMU 关掉了」（`CONFIG_NPU_USER_IOMMU`）——那是错的。**
> 我漏查了项目自己的文档。`radxa_utlra` 里早就有完整诊断：
> `docs/a733-npu-three-layer-rootcause.md`（2026-09-13）。
> `CONFIG_NPU_USER_IOMMU is not set` 这个发现是真的，但它**不是本次挂死的原因**。

### 5.1 真正的根因：三层初始化缺陷

| 层 | 问题 | 证据 | 状态 |
|---|---|---|---|
| **1** | galcore **只 `clk_prepare` 从不 `clk_enable`** —— NPU 核心无时钟 | `clk_summary`: `npu en=0 prep=1`、`pll-npu en=0 prep=2` | ✅ 运行时修复 |
| **2** | **`pd_npu` 电源域保持 off** —— NPU 无供电 | `pm_genpd_summary`: `pd_npu off-0` | ✅ 运行时修复 |
| **3** | 复位/互连或**该网络的特定执行路径** | 时钟+供电全开后，**YOLOv5s** 仍不执行，中断计数 0 | ⚠️ 对 YOLOv5s / 部分网络路径仍未解 |

**没电没钟的硬件当然永远不回 idle；但时钟/电源修好后，不能把 YOLOv5s 的失败推广为 NPU 整体失败。**

### 5.2 为什么我一开始没看到

我重刷了系统 → **第 1、2 层的运行时修复（内核模块 + PM QoS）全丢了**。
我那次实验跑的是"没钟没电"的 NPU，症状当然和第 3 层一样（都是挂死），
但我不知道前两层已经能修，于是自己另找了一个根因。

### 5.3 修复后实测

应用 `scripts/install-npu-clk-fix.sh`（需先修本文档 §6.2 的两个 bug）：

| 项 | 修复前 | 修复后 |
|---|---|---|
| `npu` 时钟 | `en=0` | **`en=2`** |
| `pd_npu` | `off-0` | **`on`** |
| 绑定驱动 | (未绑定) | `vipcore` |
| `/dev/vipcore` | 不存在 | 存在 |

**但 YOLOv5s 这条网络路径仍失败**：重跑 yolov5 demo 依然 `VIPDRV_WAIT_TASK` 挂死，
`vipcore_0` 中断计数**依然为 0**。

这不能写成"第三层已经证明是全局根因"，因为仓库此前已经有同一驱动/runner
下的成功对照：

| 已知 NBG | 执行结果 |
|---|---|
| `joiner_float_a733.nb`（181 KB） | **91 µs，`ret=0`** |
| `decoder_float_a733.nb`（621 KB） | **314 µs 平均，`ret=0`** |
| `encoder_float_a733.nb`（6.9 MB） | **25.5 ms，`ret=0`** |
| `yolov5s_rt_uint8_a733.nb`（5 MB） | `VIPDRV_WAIT_TASK` 超时 |

所以当前能下的最稳结论是：**NPU 工作链路已被 KWS 三件套证明可用；失败集中在 YOLOv5s
这个 NBG / 网络路径，需要继续做模型与算子级对比。**

---

## 六、这次实验的额外价值：排除了「某个上层框架的单独问题」

已有的三层根因诊断主要基于 **llama.cpp（走 TIM-VX）**。
这次用 **Model Zoo 的 yolov5 demo（走 VIPLite/NBG，完全不经过 TIM-VX）** 复现出**完全相同**的症状：

```
create network 0: 18195 us.        ← 成功
prepare network: 3186 us.          ← 成功
feed input cost: 29106 us.         ← 成功
cid=0x1000003b                     ← 硬件 ID 读到了
fail to ioctl vipcore, command[4]:VIPDRV_WAIT_TASK, status=-1
```

dmesg 同样是 `FE/SH/NN not idle` → `VIP not going to idle`，中断计数 0。

→ **两个不同上层路径在 YOLOv5s / LLM 这类网络上出现同类失败**，
说明不能只盯某一个应用；但由于 KWS 三件套同样走 VIPLite 且已经成功，
当前更准确的范围是：**特定网络/算子触发的执行路径问题**，还不能下"驱动/内核全局不可用"的结论。

---

## 七、⚠️ 顺带修掉的两个真 bug

修复链本身有两个 bug，在"没有 galcore 的内核"上会让情况**变得更坏**。
详见提交 `a5e3f09`。

### 7.1 `modules/Makefile` 用 `$(PWD)`

```makefile
$(MAKE) -C $(KDIR) M=$(PWD) modules     ← PWD 是 shell 环境变量
```

`make -C <dir>` **不会**更新 `$(PWD)` → `M=` 取到调用者目录 →
`install-npu-clk-fix.sh` **只有在恰好位于 `modules/` 目录里时才能跑通**。
改用 `$(CURDIR)`。

### 7.2 `npu-clk-fix.service` 无条件绑 galcore

该内核实际是 `CONFIG_AW_NNA_VIP=y` + **`# CONFIG_AW_NNA_GALCORE is not set`**（只有 vipcore）。
原逻辑先 `unbind vipcore` 再 `bind galcore`（静默失败）→
**NPU 谁都没绑，`/dev/vipcore` 消失，`pd_npu` 保持 off —— 比不修还坏。**

手动恢复：`echo 3600000.npu > /sys/bus/platform/drivers/vipcore/bind`

---

## 八、结论

### 8.1 NPU 本身没问题

设备节点、驱动、厂商运行库、NBG 模型全部正常 ——
建网、准备、喂数据全部成功，硬件 ID 读得到（`cid=0x1000003b`）。

> **准确说法**：NPU 硬件与软件栈**在 KWS / 语音网络路径上已经被实测证明可用**；
> 前两层初始化缺陷可用运行时手段修复；本次失败集中在 **YOLOv5s 这个 NBG / 网络路径**，
> LLM / YOLO 的部分网络仍需继续做模型与算子级对比。

### 8.2 下一步（不是"编内核开 IOMMU"）

优先级已经不是重编内核，而是**复用之前已经跑通的对照实验**：

1. 用 `scripts/npu/npu_min_test.sh` 跑最小算子网络 vs YOLOv5s，保持同一驱动、同一 runner、同一输入格式；
2. 对照 KWS 的 `joiner / decoder / encoder` NBG，确认是算子、量化格式、输入布局还是 YOLO NBG 本身；
3. 只有在**多个已知可用模型也同时失败**时，才重新怀疑驱动/设备树全局问题。

Orange Pi BSP 的 diff 仍可做，但它是**后续证据收集**，不是当前直接改内核的理由。

### 8.3 在当前这张卡上

**不需要现在改内核或重启。** 先用已经成功的 KWS NBG 做对照，继续定位 YOLOv5s 的模型/算子差异。

在 YOLOv5s 路径没有确认前，LLM 推理仍走 CPU（`-ngl 0`，Qwen2.5-0.5B ≈ 18 t/s）；
但这不影响 A733 NPU 用于已经验证过的 KWS / ASR / 声码器 / 视觉模型路径。

---

## 九、复现清单

```bash
# 1. 下载 Model Zoo（199 MB，只需其中 ~28 MB）
curl -LO https://dl.radxa.com/cubie/allwinner-model-zoo.tar.gz

# 2. 取出三块：examples/yolov5（去 convert_model/figures）、
#    common/npuruntime/lib_linux_aarch64/A733、3rdparty/opencv 的 aarch64 zip
# 3. 传到板子 ~/npu_zoo/，保持目录结构
# 4. 板端装 g++ cmake；用 python3 -m zipfile 解压 OpenCV
# 5. 构建（注意编译器名要含 aarch64）
cmake -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
      -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++ \
      -DTARGET_NAME=A733 -DEXTERN_DEFINE_TARGET=ON .. && make

# 6. 跑，然后看这两处确认是否挂
./yolov5_demo_a733 -nb ../model/yolov5s_rt_uint8_a733.nb -i ../model/dog.jpg -l 1 -m 20
grep -iE 'vip|npu' /proc/interrupts     # 计数为 0 = 中断从未触发
dmesg | grep -iE 'vipcore|npu' | tail   # 看 FE/SH/NN 是否 not idle
```

---

## 十、来源

| 内容 | 来源 |
|---|---|
| NPU 运行库路径、语音助手流程、VIP9000 单网络限制 | [Radxa Docs · 离线语音助手](https://docs.radxa.com/cubie/a7a/app-dev/npu-dev/voice-assistant) |
| Model Zoo 下载与目录结构 | [Radxa Docs · Model Zoo 下载](https://docs.radxa.com/cubie/a7a/app-dev/npu-dev/model-zoo/model-zoo-download) |
| 预编译 A733 模型 | `dl.radxa.com/cubie/allwinner-model-zoo.tar.gz` → `examples/yolov5/model/yolov5s_rt_uint8_a733.nb` |
| `NPU_USER_IOMMU` 配置（**排除为本次根因**） | `github.com/radxa/allwinner-bsp` → `drivers/npu/aw_nna_vip/Kconfig`；板上 config 确实未开，但先前已有三层诊断 + 本次时钟/电源修复后的复测表明，本次挂死的决定性问题仍是第 3 层 |
| 同一选项在 Armbian 也关着 | `github.com/armbian/build` → `config/kernel/linux-sun60iw2-vendor.config` |
| 内核源码仓库 | `github.com/radxa-pkg/linux-aw2511` |

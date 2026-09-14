# A733 NPU 可用路径 —— 广泛调研报告

> ## 🎯 2026-09-14 深夜实测更新：**在 6.6 内核上把 NPU 驱动跑起来了**
>
> **本节取代下面「方案 A：刷 5.15 镜像」的建议 —— 不需要刷镜像。**
>
> Radxa 的 6.6 内核**本来就内置了 NPU 驱动**：
>
> | 检查项 | 结果 |
> |---|---|
> | `/boot/config-6.6.98-4-aw2511` | **`CONFIG_AW_NNA_VIP=y`**（内置，不是模块） |
> | 设备树 `soc@3000000/npu@3600000` | `compatible=allwinner,npu`、**`status=okay`** |
> | `/sys/bus/platform/devices/3600000.npu/driver` | → **`drivers/galcore`** ← 被抢了 |
> | `/sys/bus/platform/drivers/vipcore/` | **空**（一个设备都没有） |
>
> **真正的问题：`galcore` 驱动把 NPU 设备抢占了**，`vipcore` 拿不到设备，
> 所以 `/dev/vipcore` 不存在。
>
> **修法（实测有效）：**
>
> ```bash
> rmmod galcore
> echo 3600000.npu > /sys/bus/platform/drivers/vipcore/bind
> ls -l /dev/vipcore        # → crw-rw-rw- 1 root root 199, 0
> ```
>
> **结果 —— 初始化成功：**
>
> ```
> VIPLite driver software version 2.0.3.2-AW-2024-08-30
> vip lite init OK.                     ← 初始化成功
> cid=0x1000003b, device_count=1        ← 识别到 NPU 硬件
>   device[0] core_count=1
> ```
>
> 内核侧日志：
>
> ```
> npu NPU Use VF0000, use freq 1008 MHz
> npu Want set pclk rate(1008000000) ... real(1008000000)    ← 时钟 OK
> npu Get NPU Regulator Control FAIL!                        ← ⚠️ 电压控制失败
> npu Want set npu vol(960000) now vol(800000)               ← 想 960mV，实际 800mV
> npu core_0, request irqline=457, name=vipcore_0
> npu VIPLite driver version 2.0.3.4-AW-2025-10-27
> ```
>
> ⚠️ **但实际推理仍然挂死**：
>
> ```
> npu wait dev0 hw0 idle, FE not idle.
> npu wait dev0 hw0 idle, SH not idle.
> npu wait dev0 hw0 idle, NN not idle.
> npu error, VIP not going to idle.
> npu device0 not going to idle
> ```
>
> **电压没提上去（960mV → 实际 800mV）**，这很可能就是挂死的直接原因，
> 也是下一个要攻的点（设备树 `npu-supply` / regulator 配置）。
>
> ---

> 调研日期：2026-09-14
> 方法：gh 近义词搜索（5 组关键词）+ 官方文档 + 官方论坛 + **板端实测**
> 结论强度：**有板端实测证据**

---

## 一、一句话结论

**A733 的 NPU 可以用，有官方支持和大量社区验证 —— 但我们当前这个镜像不行。**

原因**不是**"6.6 内核不支持 NPU"（社区正是在 `6.6.98-sun60iw2` 上跑通的），
而是 **`radxa-a733_trixie` 镜像的内核没有编译 NPU 驱动**（缺 `/dev/vipcore`）。

---

## 二、我们之前错在哪

| 之前结论 | 实际情况 |
|---|---|
| "6.6 内核上 NPU 不可用" | ❌ **不成立**。社区在 `6.6.98-sun60iw2` 上跑通了 |
| "两条驱动路线都挂死" | ⚠️ 我们试的是 **galcore / TIM-VX** 路线。官方和社区走的是 **`sunxi_npu` → `/dev/vipcore` + VIPLite**，是**另一条完全不同的路** |
| "NPU 封存，别碰" | ❌ 应改成「**当前镜像缺驱动，换镜像即可**」 |

**关键区分（这是整件事的核心）：**

| 设备节点 | 属于 | 用途 |
|---|---|---|
| `/dev/galcore` | Vivante 官方驱动 | TIM-VX / galcore 路径（我们之前试的，会挂死） |
| **`/dev/vipcore`** | **Allwinner 的 NPU 驱动** | **VIPLite 路径 —— 官方与社区都用这条** |

> `jackhe183/radxa-dev` 里写得很直白：
> 「驱动节点：`/dev/vipcore`（**注意：不是 `/dev/galcore`** 或 `/dev/rknpu`）」

---

## 三、板端实测证据（决定性）

按官方文档，在板子上把**用户态全流程跑通了**：

```bash
# 1. 取 SDK（板子直连 GitHub 不通，走本机 HTTP 中转）
git clone --depth 1 --filter=blob:none --sparse https://github.com/ZIFENG278/ai-sdk
git sparse-checkout set viplite-tina examples machinfo

# 2. 编译 vpm_run
cd examples/vpm_run && make AI_SDK_PLATFORM=a733     # → 生成 vpm_run（106200 字节）

# 3. 运行
export LD_LIBRARY_PATH=$PWD/../../viplite-tina/lib/aarch64-none-linux-gnu/v2.0
./vpm_run -s sample.txt -l 1
```

**实际输出：**

```
VIPLite driver software version 2.0.3.2-AW-2024-08-30              ← 用户态库 OK ✓
[0xd23cc020]viphal_os_init[81], fail to open device /dev/vipcore   ← 设备节点缺失 ✗
[0xd23cc020]viphal_init[380], fail to initialize OS, status=-2.
failed to init vip
vpm run ret=-1
```

**结论**：VIPLite 用户态（2.0.3.2）**完全就绪**，唯一缺的就是内核侧的 `/dev/vipcore`。

---

## 四、根因：当前镜像的内核没编 NPU 驱动

板端逐项检查：

| 检查项 | 结果 |
|---|---|
| `ls /dev/vipcore` | ❌ 不存在（只有 `/dev/galcore`，199,0） |
| `modinfo sunxi_npu` | ❌ `Module sunxi_npu not found` |
| `/lib/modules/$(uname -r)/kernel/drivers/` | ❌ **没有 `npu` 目录** |
| `dmesg \| grep -icE "npu\|vip"` | ❌ **0**（1119 行日志里零 NPU 痕迹） |
| `/dev/galcore` 的 major:minor | `199,0` —— 与文档里 `/dev/vipcore` 的 `199,0` **相同**，但 VIPLite 硬编码找 `/dev/vipcore` 这个名字 |

**Radxa 官方论坛有完全相同的案例**（症状一模一样）：

> 我用 A7A + `radxa-a733_trixie_kde T5` 镜像，NPU 报错，
> 内核 VIPLite 2.0.3.4 vs 用户态 2.0.3.2。
>
> **官方回复**：`radxa-a733_trixie_kde T5` **是测试镜像，不推荐用于 NPU 工作**。
> 请切换到 **r5 版本（`radxa-a733_bullseye_kde_r5`）**。
> 两个 VIPLite 字符串都是 2.0.3.x，驱动只检查 2.0.3 ABI，patch 日期差异不会阻塞 `vpm_run`。

来源：<https://forum.radxa.com/t/ai-sdk-viplite-driver-software-version/31323/3>

---

## 五、官方支持情况（不是野路子）

**Radxa 官方 NPU 版本对照表：**

| 产品 | 算力 | NPU 版本 | NPU 软件版本 |
|---|---|---|---|
| Radxa A5E | 2 Tops | T527 | v1.13 |
| **Radxa A7A** | **3 Tops** | **A733 v3** | **v2.0** |
| Radxa A7Z | 3 Tops | A733 v3 | v2.0 |
| Radxa A7S | 3 Tops | A733 v3 | v2.0 |

**官方文档：**

- [vpm_run 模型测试工具](https://docs.radxa.com/cubie/a7z/app-dev/npu-dev/cubie-vpm-run)
- [ACUITY Toolkit 使用示例](https://docs.radxa.com/cubie/a7z/app-dev/npu-dev/cubie-acuity-usage)
- [离线语音助手（A7A）](https://docs.radxa.com/en/cubie/a7a/app-dev/npu-dev/voice-assistant)

官方文档里的示例输出，提示符就是 `rock@radxa-cubie-a7a`：

```
VIPLite driver software version 2.0.3.2-AW-2024-08-30
vip lite init OK.
cid=0x1000003b, device_count=1
  device[0] core_count=1
run time for this network 0: 3160 us.
vpm run ret=0
```

---

## 六、社区已验证的项目（"一定能用"的实例）

| 项目 | 做了什么 | 链接 |
|---|---|---|
| `Ronin-1124/cubie-a7a-voice-assistant` | **A7A 离线中文语音助手**，KWS+ASR+TTS 全走 NPU（**被 Radxa 官方文档收录**） | [GitHub](https://github.com/Ronin-1124/cubie-a7a-voice-assistant) |
| `petayyyy/a733_npu_driver` | SmolLM2-135M/360M 在 NPU 上跑（**21 / 8 tok/s**）、MobileCLIP-S0（**22.6 ms**）；完整可复现工具链 | [GitHub](https://github.com/petayyyy/a733_npu_driver) |
| `northwindlight/a733-npu` | EDSR x2 超分：360×640 → 720×1280，纯 NPU **236 ms** | [GitHub](https://github.com/northwindlight/a733-npu) |
| `RiteshKumarRay/Radxa-VIP9000-NPU-Tracking` | YOLOv5s 人物追踪 + 网页直播 | [GitHub](https://github.com/RiteshKumarRay/Radxa-VIP9000-NPU-Tracking) |
| `arnaudlvq/MediaPipe-FaceLandmarker-NPU-Version-A733-...` | MediaPipe 人脸关键点检测 | [GitHub](https://github.com/arnaudlvq/MediaPipe-FaceLandmarker-NPU-Version-A733-VeriSilicon-VIP9000) |
| `waz664/vip9000-embeddinggemma` | EmbeddingGemma 向量模型 | [GitHub](https://github.com/waz664/vip9000-embeddinggemma) |
| `jackhe183/radxa-dev` | 中文实战指南（"拒绝 RKNN，拥抱 VIPLite"） | [GitHub](https://github.com/jackhe183/radxa-dev) |
| `ZIFENG278/ai-sdk` | **官方 ai-sdk**：VIPLite 库 + `vpm_run` + 示例（26★，注意**无 license**） | [GitHub](https://github.com/ZIFENG278/ai-sdk) |

---

## 七、可行方案

### 方案 A：刷 `radxa-a733_bullseye_cli_r6` ← **推荐，已挂载验证**

**为什么用 r6 而不是论坛说的 r5**：r6（2026-04-30）更新，而且我们**已经把 r6 镜像挂载起来验证过**
（证据见下）。用 **CLI 版**（无桌面，501MB），不要 KDE 版（1086MB）。

#### 镜像清单

| 版本 | 文件 | 大小 |
|---|---|---|
| **bullseye cli r6** ← 推荐 | `radxa-a733_bullseye_cli_r6.output_512.img.xz` | 501 MB |
| bullseye cli r5 | `radxa-a733_bullseye_cli_r5.output_512.img.xz` | 502 MB |
| ~~bullseye kde r6~~ | `..._kde_r6.output_512.img.xz` | 1086 MB（带桌面，不需要） |

下载：<https://github.com/radxa-build/radxa-a733/releases> → 选 **`rsdk-r6`** → `cli` 那个。

> 💡 如果你之前下载过 `radxa-a733_bullseye_cli_r6.output_512.img.xz`（501MB），
> **不用重新下** —— 我们就是拿它验证的。本地文件（xz 压缩包）sha512：
>
> ```
> 154e4bf5baec5901c1cb6a6e20fb448cedfa7b4b232f3fe34e8ca1a0e861feeba1ab7b1360f5d14637690bb65449d760cc65b66ee2ef015b5138f3a6eac3ad8b
> ```

#### 镜像内已含 NPU 驱动（实测证据，不用刷机就能验）

把镜像挂载后直接查：

```bash
mount -o ro,loop,offset=348127232 radxa-a733_bullseye_cli_r6.output_512.img /mnt/r6
find /mnt/r6/lib/modules -iname "*npu*"
```

结果：

```
/mnt/r6/lib/modules/5.15.147-21-a733/kernel/bsp/drivers/npu/aw_nna_vip/vip2/vipcore.ko.xz
```

**三个关键点：**

- 内核是 **`5.15.147-21-a733`** —— 正是 petayyyy 文档里 Radxa 用的那个版本
- 有 **`vipcore.ko`** → 会创建 `/dev/vipcore`
- 对比当前 `trixie` 镜像：内核 `6.6.98-4-aw2511`，`/lib/modules/*/kernel/drivers/` 下**没有 npu 目录**

> 镜像里**没有**预装 VIPLite 用户态库（`libVIPhal.so` / `libNBGlinker.so`）——
> 需要自己装，见下一步。这与官方文档「Copy the libraries from Model Zoo」一致。

#### 刷机

流程见 [flash-radxa-debian13.md](flash-radxa-debian13.md)（步骤相同，只换镜像文件）。
⚠️ 注意选 `output_512` 那个（512 字节扇区），不是 `output_4096`。

#### 刷完后启用 NPU

```bash
# 1. 确认设备节点出现
ls -l /dev/vipcore           # 期望：crw-rw-rw- 1 root root 199, 0
sudo chmod 777 /dev/vipcore  # 官方文档要求的权限

# 2. 装 VIPLite 用户态库（把 ai-sdk 的 v2.0 目录拷到板子 ~/lib）
export LD_LIBRARY_PATH=$HOME/lib:$LD_LIBRARY_PATH

# 3. 编译并运行 vpm_run
cd examples/vpm_run && make AI_SDK_PLATFORM=a733
./vpm_run -nb <model>_a733.nb -i <input> -l 10
```

**成功判据**：输出 `vip lite init OK` + `cid=0x1000003b` + 单次网络耗时。

### 方案 B：在 trixie 上补 NPU 驱动（不推荐）

需要拿到 `sunxi_npu` 源码并重编内核 —— 工作量大，且官方已明确说 trixie 不适合 NPU 工作。

### 方案 C：等 trixie 后续版本

论坛称 T5 是测试镜像，后续版本可能补上 NPU 驱动。

---

## 八、上板注意事项（论坛与实测踩坑）

1. **捆绑的示例模型是 T527 专用的**（`mobilenet_v2_t527.nb`）—— **A733 上不要用**，
   要换 `*_a733.nb`（例如 `lstm_model_uint8_a733.nb`）。这是官方论坛明确提醒的坑。
2. `vpm_run` 的参数形式（论坛版）：`./vpm_run -nb <model>.nb -i <input> -l 10`
3. NPU **同一时间只能加载一个网络**（单核 VIP），多模型要排队。
4. 板子**直连 GitHub 不通**（CMCC 线路），可用本机起 HTTP 服务中转。
5. 用户态库路径要 export：
   `export LD_LIBRARY_PATH=<sdk>/viplite-tina/lib/aarch64-none-linux-gnu/v2.0`

---

## 九、对现有结论的修正

| 文档 | 需要改什么 |
|---|---|
| `docs/hardware-config-list.md` | NPU 从「⛔ 封存」改为「⚠️ 当前镜像缺驱动，换 r5 镜像即可用」 |
| `docs/a733-npu-three-layer-rootcause.md` | 补充说明：那是 **galcore/TIM-VX 路线**的问题，**不代表 NPU 本身不可用** |
| `docs/hardware-roi.md` | NPU 状态更新 |
| `awesome-radxa-a733/README.md` | 同上 |

**重要澄清**：之前测到的"NPU 执行挂死"是在 **galcore / TIM-VX 路径**下得到的；
而 **VIPLite 路径在当前镜像上根本没有驱动，压根没被验证过**。
所以"NPU 不可用"这个结论**证据不足**，应当撤回。

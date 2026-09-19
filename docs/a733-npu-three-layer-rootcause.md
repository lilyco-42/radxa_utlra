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

→ 与本文档原结论一致:**第三层(复位/互连)未解,硬件仍不执行命令**。
差别只在"失败是否安全" —— 修复后是干净超时,不再拖垮系统。

### D. 结论没变,但更精确了

| 层 | 状态 |
|---|---|
| 1 时钟门控 | ✅ 已修(运行时,`npu_clk_fix.ko`) |
| 2 电源域 | ✅ 已修(运行时,PM QoS) —— **但安装脚本需先修上面的绑定 bug** |
| 3 复位/互连 | ❌ **未解**,待 diff `orangepi-xunlong/linux-orangepi` 的 `orange-pi-6.6-sun60iw2` |

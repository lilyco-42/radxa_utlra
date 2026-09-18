# A7A 反复烧卡 —— 根因排查总报告

> 日期：2026-09-18
> 排查人：用户 + AI
> 结论状态：**已定案（Windows 侧） / 待定案（板子侧）**

---

## 一、用户最关心的问题

> 「我都换了 **两张卡**了，这样下去不排查问题，下面的卡插入 **也会这样**」

**这个问题现在可以回答一半：**

- **Windows 侧读不到卡** → **根因已找到并修复**（`usbipd` 劫持，与卡无关）
- **板子侧烧卡** → **根因已定位到具体环节，但需要一次对照实验才能确认**

---

## 二、Windows 侧：读卡器"插了但系统看不见"

### 现象
- 换 **两个**读卡器、插 **本体口和扩展坞**，全部零反应
- `Get-Disk` 只有 NVMe 系统盘
- System 日志 **两天零 USB 事件**
- 60+ 个 `Code 45`（PHANTOM）幽灵设备

### 根因（一条命令坐实）

```
usbipd list
7-2   349c:0418  USB 大容量存储设备   Shared (forced)
                                     ^^^^^^^^^^^^^^^ 元凶
```

`usbipd-win 5.3.0` 把总线 7-2 上的 USB 大容量存储设备以 **forced** 方式
bind 给了 USB/IP 协议栈 → 设备从 Windows 主系统被摘走 → 主系统彻底看不见。
叠加 `usbipd` 自身 `CM_PROB_FAILED_DRIVER_ENTRY (Code 37)` 加载失败
（与反作弊驱动 `hrdevmon` 冲突）→ 设备变成黑洞。

**关键判据：完全静默（连枚举事件都没有）= 被驱动栈拦截**，
而不是"口坏了/线坏了/设备坏了"。后者至少会有 USB 枚举日志。

### 解药（需管理员）

```cmd
"C:\Program Files\usbipd-win\usbipd.exe" unbind --all
net stop usbipd
sc config usbipd start= demand
```

**验证成功的样子：**
- `usbipd list` 中 7-2 → `Not shared`
- USBIP 设备状态 → `OK` / `CM_PROB_NONE`
- `Get-Disk` 出现 `#1 Generic Mass-Storage`
- `E:` 盘出现

**全部可逆：** `bind` / `Start-Service` / `Set-Service -StartupType Automatic`

### 附带发现
- 系统装有 **VMware 残留驱动**（`oem28.inf vmusb.inf`、`oem15.inf vmci.inf`）
- 有 **WSL2**（Ubuntu-24.04 + docker-desktop）
- 读卡器 `SerialNumber = 20240418000000`（固定值，不传递卡 CID → 廉价白牌读卡器）

---

## 三、卡的真伪：双通道交叉验证

### 结论：**卡是真的，不是扩容假卡**

| 读取项 | Windows 扩展坞 | 板子 U-Boot | 一致？ |
|---|---|---|---|
| 总容量 | 62.5 GiB | 62.5 GiB | ✅ |
| 分区表 | GPT，3 分区 | GPT，3 分区 | ✅ |
| EFI 分区类型 | `c12a7328…93b` | `c12a7328…93b` | ✅ |
| 卡名称 | 读卡器不报告 | `asdfg` | — |
| SD 版本 / 速率 | 需管理员读 CID | SD 2.0 / 50MHz | — |

**62.5 GiB 与卡面标称「64G」相符**（64G 卡可用容量正是 62.5 GiB）。

### ⚠️ 我犯过的错误（记录以免重蹈）

- 我一度从 `Name: asdfg` + `62.5 GiB` 跳到"**假卡**"结论 → **错了**
- 用户纠正：卡面有 `A2` + `U3` 标记，是 UHS-I 高速卡标准标记
- 后用双通道数据交叉验证 → **容量真实，卡是真的**
- **教训：单一来源的可疑字段不足以定案，必须交叉验证**

### 仍存在的矛盾

卡面印 `A2 / U3`（宣称 UHS-I、支持 1.8V）
但板子读到 `SD 2.0 / 50MHz`（只有 3.3V 模式）

**后果是「跑不快」，不是「存不了」。**

---

## 四、板子侧：烧卡的真正机理

### 决定性证据：读写不对称

```
写错误行数：60
读错误行数：0
```

| 情况 | 读 | 写 | 换卡有用？ |
|---|---|---|---|
| 卡彻底坏（物理/寿命耗尽） | ✗ | ✗ | **有用** |
| 电压切换故障（3.3V 读 OK / 1.8V 写坏） | ✓ | ✗ | **无用** |

**实测坐实第二种：读全好、写全坏。**

**为什么读没事、写出事？** 读的时序容差比写大得多 ——
读只要在窗口内采到数据即可；写要求严格的建立/保持时间。
控制器按高速时序驱动、卡只能跑低速 → 写时序对不上 → 写坏。

### 完整因果链

```
① SD 控制器电压切换失败
   「Card did not respond to voltage select! : -110」
   「manual set ocr」
        ↓
② 高速写路径不可靠（60 次写错误 / 0 次读错误）
        ↓
③ ext4 元数据被写坏（ext4_validate_block_bitmap 校验和错）
        ↓
④ 内核强制只读挂载（Remounting filesystem read-only）
        ↓
⑤ dbus.service FAILED → systemd-logind.service FAILED（依赖 dbus）
        ↓
⑥ serial-getty@ttyAS0 从未启动 → 键盘无效
   → 用户看到的「卡住了」

   附带：NetworkManager 也失败 → 没 IP → 局域网扫不到
   附带：journald 自激刷屏（45,249 行 / 1.27 MB / 5 秒）
```

### 板子本身是好的（要区分清楚）

- 蓝灯闪烁 = 系统正常启动（官方定义）
- 串口 uptime 1472~1508 秒，未重启
- systemd 已到 `multi-user.target` / `graphical.target`

**供电和 SoC 都没问题。**

---

## 五、待定案的关键实验（唯一能回答"下一张卡会不会一样"）

### 背景
用户有 **两张卡**：
- **卡 A** = 被烧过（板子起不来，之前排查的就是它）
- **卡 B** = 目前正常（刚插进扩展坞的这张）

**两张卡都上过板子** —— 卡 B 的分区表也是 GPT + Linux filesystem。

### 实验设计

**把卡 B（已知目前正常）插回板子，开机看串口。**

| 结果 | 含义 | 行动 |
|---|---|---|
| 正常启动，无 voltage select 报错 | **板子没问题** → 卡 A 是自己坏的 | 用好卡即可 |
| 也报 `Card did not respond to voltage select` | **板子有问题** → 会毁每张卡 | 修控制器 / DTB |
| 能启动但开始报写错误 | **板子有问题** → 正在毁卡 B | 立刻停止 |

**这是不可替代的实验。** 没有别的方法能分辨"卡 A 偶然坏"还是"板子持续毁卡"。

### ⚠️ 实验前必须先抢救数据

因为若证实"板子会毁卡"，卡 B 在测试中就可能被毁。
**数据必须先出来。**

---

## 六、数据抢救方案（WSL2 路线）

用户有 WSL2（Ubuntu-24.04），可以读 ext4：

```powershell
# 管理员 PowerShell
wsl --mount \\.\PHYSICALDRIVE1 --bare
```

然后在 WSL 内：
```bash
# 找到设备
lsblk
# 挂载第 3 分区（rootfs，只读挂载最安全）
sudo mkdir -p /mnt/card
sudo mount -o ro /dev/sdX3 /mnt/card
# 抢救数据
ls /mnt/card/home/radxa/
```

**要抢救的：**
- `/home/radxa/vp/`（VP 流水线）
- `/home/radxa/sau/`（social-auto-upload）
- `/home/radxa/biliup/`
- `/etc/kernel/cmdline`（看启动参数）
- `/boot/extlinux/extlinux.conf`

---

## 七、长期修复方向（待板子侧定案后决定）

### 方案 A：如果板子有问题 → 改 DTB（不用碰 U-Boot）

在 `/usr/lib/linux-image-*/allwinner/sun60i-a733-cubie-a7a.dtb` 对应的
设备树源里，给 SD 控制器节点加：

```dts
&mmc0 {
    /* 禁用 1.8V 电压切换，强制 3.3V */
    no-1-8-v;
    /* 或降频到 SD 2.0 上限 */
    max-frequency = <50000000>;
};
```

官方更新路径：`sudo nano /etc/kernel/cmdline && sudo u-boot-update`

### 方案 B：如果卡有问题 → 换正牌卡

推荐：闪迪 Extreme/Pro（U3 + A2）、三星 EVO Plus，**32GB 以上**。

### 方案 C：不管哪种情况都建议做 —— 启动参数加 `ro`

当前 `extlinux.conf` 两个 label 都是 `rw`（读写挂载）。
**这是 ext4 被持续写坏的直接原因。**

改成 `ro` 至少能保证：即使写路径坏了，文件系统也不会被破坏。

---

## 八、工具清单

| 工具 | 用途 |
|---|---|
| `tools/card_forensics.py` | 根因取证（日志/SSH/串口），含读写不对称分析 |
| `tools/usb_watch.py` | 实时监听 Windows USB 插拔，三档判定 |
| `tools/serial_rescue_guide.py` | 诊断 journald 刷屏 + 打印救援方案 |
| `tools/serial_shell.py` | 探测串口能否拿到 shell |
| `tools/sd_verify.py` | 读 SD 卡 CID 验证真伪（需管理员） |
| `tools/lan_sweep.py` | 局域网扫描找板子 |
| `tools/initramfs_fixer.py` | initramfs 修复（含 U-Boot 保护护栏） |

---

## 九、给未来的自己

1. **"完全静默"是最强的诊断信号** —— 排查 USB 问题先看有没有枚举事件
2. **`usbipd` 是隐形杀手** —— 装了 Docker Desktop / WSL2 转发就可能中招
3. **单一来源的可疑字段不足以定案** —— `asdfg` 不等于假卡
4. **读写不对称是硬件故障的照妖镜** —— 只有写坏 ≠ 介质死
5. **用户的直觉往往是对的** —— 用户说"不排查问题下一张还会坏"，这个判断是对的

# A7A 存储损坏修复方案（2026-09-28）

> 配套文档：`a7a-2026-09-28-incident.md`（现场取证与判据）。
> 本文是**可执行的分步方案**。所有结论都标注了依据；凡是「推断」都写明是推断。

---

## 0. 一句话结论

板子的 **ext4 块位图坏了**：分配器把**已经属于某个文件的块**当成空闲块发出去，
新写入的数据直接盖掉旧文件。**207 个文件已被盖坏**，但：

- **内核启动链完好**（vmlinuz / DTB / 模块 / initrd 全部验证通过）→ **不需要换内核**；
- 207 个坏文件**已经全部拿到正确内容**（从官方 deb 抽取，逐个 md5 核对通过）；
- dpkg 数据库废了，但**有 09-22 的可用备份**，且修完之后可以用 `apt` 自动恢复。

修复路线：**断电拔卡 → 离线 e2fsck → 拷回 207 个文件 → 恢复 dpkg 数据库 → 上电 → apt 收尾**。

---

## 1. 现在的状态

### 1.1 板子还活着，但它在持续自我破坏

```
uptime: 23 小时+（未重启，符合「绝不重启」的要求）
根分区: /dev/mmcblk1p3  62G，已用 7.7G（14%）—— 空间充足，不是「写满导致的」
```

**损坏仍在发生，而且是静默的。** 实测证据（这一段很重要，它推翻了「已经停了」的旧结论）：

| 时间 | 事件 |
|---|---|
| 09-28 11:11:39 | `rescue5.tgz` 打包完成，其中 `/var/cache/apt/archives/at-spi2-core_*.deb` 的 md5 = `78272a37…` |
| 09-28 11:19 | 再读同一个文件，md5 = `2a798987…` —— **内容变了** |
| —— | 但它的 `ctime` 始终是 `2026-09-27 13:03:47`，`mtime` 始终是 `2026-09-04 10:15:29`，**大小始终 59916** |

**inode 元数据一个字节都没动，文件内容却变了。** 这是「块被别的文件覆盖」的独有指纹，
也解释了为什么下面这条旧结论是**错的**：

> ❌ 旧结论：「EXT4 错误数 9.7 小时零新增 → 损坏已停止扩散」

**EXT4 不会为这种覆盖报错。** 分配器认为那个块是空闲的，写进去完全合法，
内核没有任何理由记一条 error。所以「错误数不涨」根本不能证明「没在坏」。
本次只观察到**一次**改写（11:11→11:19 那 8 分钟），之后 7 分钟内 214 个样本文件零变化
（snap1..snap4 四份 md5 完全一致），说明**频率是低频偶发的，不是持续高频**。
但只要板子还在跑，写入就在发生，就还有可能盖掉新东西。

> 一句话：**别再等，尽快停掉。**

### 1.2 损坏范围（已精确量化）

用 `*.md5sums` 反查（`dpkg -V` 因为数据库坏了跑不了），得到 **226 条**不一致记录。
逐条甄别后：

```
226 条  =  207 个真损坏  +  19 个假阳性
```

**19 个假阳性的 6 个类别**（这一类必须记住，否则会做错事）：

| # | 类别 | 例子 | 为什么是假阳性 |
|---|---|---|---|
| 1 | **形态转换** | `/boot/vmlinuz-6.6.98-4-aw2511` | `radxa-bootutils` 在 postinst 里把 gzip 内核**解压**成未压缩 arm64 Image；md5sums 记的是出厂压缩态。实测 `gzip -dc`(deb 内) 与板子上那份**逐字节相同** |
| 2 | **共享路径** | `/usr/share/applications/mimeapps.list`、`/usr/lib/firmware/cypress/cyfmac43455-sdio.bin` | 两个包都提供同一路径，只有一份能落盘，落败方的 md5sums 必然不匹配。板子上那两份**完好**（md5 分别等于 allwinner 版 / radxa-firmware 版） |
| 3 | **本地合法改写** | `/usr/share/desktop-base/kf5-settings/kdeglobals` | 板子上是完整可读文本，与 radxa 出厂版只差 `BrowserApplication=` 一处（`chromium-browser.desktop` vs `firefox-esr.desktop`，正好 +5 字节 = 664-659） |
| 4 | **depmod 再生** | `/lib/modules/6.6.98-4-aw2511/modules.{dep,dep.bin,alias,alias.bin,symbols,symbols.bin}` | 安装后 `depmod -a` 重算，模块集变大所以文件变大。内容合法：`modules.dep` 908 行、**0 个非打印字符** |
| 5 | **路径解析 bug** | 7 个 `/usr/lib/firmware/brcm/*.txt` + 2 个 alsa ucm2 `*.conf` | 板子侧生成清单时按空白分列，**把带空格的文件名截断了**（如 `…-sdio.ASUSTeK COMPUTER INC.-ME176C.txt` → `…-sdio.ASUSTeK`）。文件本身 md5 与期望**完全一致** |
| 6 | （同上，含在 5 里） | | |

**→ 真正被盖坏的是 207 个**，全部符合「大小不变、内容随机」的块覆盖指纹。

按包分布（前 10）：

| 包 | 坏文件数 |
|---|---|
| `at-spi2-common` | 93 |
| `git` | 41 |
| `libauthen-sasl-perl` | 22 |
| `git-man` | 14 |
| `fonts-liberation` | 12 |
| `fonts-noto-cjk` | 11 |
| `libcdio19t64` | 4 |
| `libnspr4` | 3 |
| `python3-setuptools-whl` / `python3-pip-whl` / `libxxf86dga1` / `at-spi2-core` / `alsa-ucm-conf` | 各 2 |
| 其余 18 个包 | 各 1 |

**其中真正影响使用的**（其余多是 locale/字体/文档，坏了不影响开机）：
- `/usr/bin/python3.13`、`/usr/bin/git`、`/usr/bin/git-shell`、`/usr/bin/scalar`
- `/usr/libexec/at-spi-bus-launcher`、`/usr/libexec/at-spi2-registryd`
- `/usr/lib/aarch64-linux-gnu/` 下若干 `lib*.so`（libatk、libatspi、libnspr4、libXpm、libXmuu…）
- `/var/lib/dpkg/status`（**全文件随机**，见 §1.4）

### 1.3 内核启动链 —— 全部完好，**不要动它**

这是本轮最重要的更正。逐项验证结果：

| 组件 | 结论 | 依据 |
|---|---|---|
| `/boot/vmlinuz-6.6.98-4-aw2511` | ✅ **完好** | `gzip -dc`(官方 6.6.98-4 deb 内的 vmlinuz) 与板子上那份**逐字节相同**，md5 都是 `f7c1fcbef265dd741a4dbbd1e2d71852` |
| DTB | ✅ **完好** | 207 个坏文件里没有任何 dtb；`/usr/lib/linux-image-6.6.98-4-aw2511/allwinner/sun60i-a733-cubie-a7a.dtb` 未出现在清单里 |
| `/lib/modules/6.6.98-4-aw2511/**/*.ko.xz` | ✅ **完好** | 没有任何 .ko 被标记；被标记的只有 6 个 depmod 索引文件（见 §1.2 类别 4） |
| `/boot/initrd.img-6.6.98-4-aw2511` | ✅ **完好** | `unmkinitramfs` 解出 953 个文件 / 124 个模块，**与磁盘上同名模块逐个 md5 一致（124 一致 / 0 不一致）** |
| `extlinux.conf` | ✅ 指向正确 | `linux /boot/vmlinuz-6.6.98-4-aw2511`、`initrd /boot/initrd.img-6.6.98-4-aw2511`、`fdtdir /usr/lib/linux-image-6.6.98-4-aw2511/` |

> ⚠️ **因此「上电前必须先把内核换成 6.6.98-5」这条旧要求作废。**
> 原版 6.6.98-4 也**不需要**去 GitHub Release 找回来重装（虽然确实找得到，见 §4.4）。
> 唯一要做的是：**别把那 19 个假阳性里的 vmlinuz 拷回去**——载荷包里已经把它排除并挪到
> `_DO-NOT-APPLY/`。

### 1.4 dpkg 数据库

| 文件 | 状态 |
|---|---|
| `/var/lib/dpkg/status`（当前） | ❌ **整文件随机**（非 NUL 709382/712228 ≈ 随机数据期望值）。`dpkg -V` 报 `field name '??nxuk?*K~…'` |
| `/var/backups/dpkg.status.0`（09-27 14:37，升级后） | ❌ **整文件全 NUL**（非 NUL 字节 = 0）—— 「刚写下去没落盘」的指纹 |
| `/var/backups/dpkg.status.1.gz`（09-22） | ✅ **gzip 完好，749 个包**。已导出为 `packages-2026-09-22.txt` |
| `/var/backups/dpkg.status.2.gz`（08-04） | ✅ 完好，752 个包（更旧，备用） |
| `/var/lib/dpkg/info/*`（md5sums / list / 脚本） | ✅ 已整目录抢救（rescue5），**md5sums 数据库本身可信**——已用官方 deb 反证：`at-spi2-common` 的 `.mo` md5 与 md5sums 期望值**完全一致** |

### 1.5 apt 缓存 —— 大部分也坏了，别指望它

`/var/cache/apt/archives` 有 93 个 deb（86MB，mtime 09-27 14:30，正是出事那次升级）。
**其中 69 个的 `ar` 魔数已经损坏**（74%）：

```
$ head -c 8 at-spi2-core_2.56.2-1+deb13u2_arm64.deb
88104697 179afdc5 ...        ← 应为 "!<arch>\n"
```

大小正确（59916）但内容是随机字节 —— 同样的块覆盖指纹。
**→ 恢复用的 deb 一律从官方源重新下载**，不用缓存里的。

---

## 2. 阶段 A：立刻停写（现在就做）

### 为什么不能等

板子上的写入源（实测）：

| 来源 | 频率 |
|---|---|
| `radxa-net-ensure.timer` | **每 60 秒** |
| `ap-power.timer` | 每 ~5 分钟（服务当前 failed，仍在写日志） |
| `systemd-journald` | 持续 |
| `man-db.timer` / `apt-daily.timer` 等 | 每天 |

每一次写入都可能再盖掉一个块。**已经拿到的东西不会丢，但还没拿到的会。**

### 怎么做

> **推荐：直接断电（拔电源 / 长按电源键 ~10 秒），不要走 `systemctl poweroff`。**

理由：`poweroff` 会停服务、刷日志、写 journal —— 这些**都是写入**，
在块位图已经坏掉的机器上，等于**再给它几次机会去盖坏更多文件**。
断电只会留下一个脏 journal，而 `e2fsck` 本来就会处理它，代价为零。

**也不要在通电状态下拔 SD 卡** —— 先断电，再拔卡。

### 断电之后

1. 拔下 microSD 卡。
2. 插进已经就位的读卡器（当前显示 `Generic Mass-Storage / No Media`）。
3. 确认 Windows 能看见它：`.\sd_offline_repair.ps1 -Check`

---

## 3. 阶段 B：Windows 侧离线 e2fsck

根分区此刻不能再挂着，`e2fsck` 必须离线跑。脚本把有风险的顺序固定下来了：

```powershell
# 0) 体检（不需要管理员，零风险）
cd D:\Code\radxa\radxa_utlra\recovery\tools
.\sd_offline_repair.ps1 -Check
#    预期输出：OK WSL 发行版 Ubuntu-24.04 可用 / OK e2fsck 1.47.0 /
#              找到磁盘 N：<容量> / 找到根分区 /dev/sdXN

# 1) 真正修复（管理员 PowerShell）
.\sd_offline_repair.ps1 -Repair
```

`-Repair` 会依次做：

1. `wsl --mount <disk> --bare` —— 只把盘交给 WSL，**不自动挂载**（关键）
2. `e2image -Q` —— 存一份元数据快照，作为修复前的取证参照
3. `e2fsck -fn` —— 只读体检，完整报告存到 `a7a-rescue-2026-09-28/repair-logs/`
4. **【要你手打磁盘号确认】** —— 这一步故意不能跳过
5. `e2fsck -fy` —— 真正修复
6. `e2fsck -fn` —— 再体检一次，确认干净
7. `wsl --unmount`

### 修复前先看的两个硬指标

```
判据 8：dumpe2fs -h 的 Block count == 16299259   ← 几何未变 = e2fsck 能修
判据 9：e2fsck -fn 的输出里「文件系统结构」类错误应远多于「inode 内容」类
```

`Block count` 已实测为 `16299259`（与 9/18 报告一致），**几何完好**。

> ⚠️ **`e2fsck` 只修文件系统结构**（块位图、inode、目录项、extent 树），
> **完全不修文件内容**。跑完 e2fsck 之后，那 207 个文件**还是坏的** —— 必须走阶段 C。

---

## 4. 阶段 C：把 207 个文件放回去

### 4.1 现成的材料（已经做好并校验过）

目录：`D:\Code\radxa\a7a-rescue-2026-09-28\`

| 文件 | 说明 |
|---|---|
| `restore-payload.tar` | **148 MB，207 个文件**，保留 deb 里的 mode/uid/gid/mtime。抽取时逐个核对 md5 == dpkg 记录的期望值，**207/207 通过；回读校验 207 文件 / 0 不符** |
| `restore-manifest.json` | 207 个文件的清单（路径 / md5 / 归属包 / 来源 deb）+ 19 个假阳性及其原因 |
| `restore-debs/` | 33 个官方 deb（110 MB），**每个都过了 SHA256**（Debian 官方索引里的值） |
| `apply-restore.sh` | 落地脚本，带 `--check` 干跑模式 |
| `_DO-NOT-APPLY/` | 那个**绝不能拷回去**的 vmlinuz（附说明） |
| `kernel-6.6.98-4/`、`kernel-6.6.98-5/` | 备用内核包（本次用不上，留着） |

**来源与校验方式**（这是可信度的关键）：

- 26 个包从 **Debian 官方源**按 `Filename` + `SHA256` 下载（版本由板子 apt 缓存的文件名确定，
  例如 `at-spi2-core_2.56.2-1+deb13u2_arm64.deb`）；
- 3 个 radxa 包从 **`radxa-pkg` 的 GitHub Release**（`0.7.3` tag 还在）下载；
- 1 个内核包从 `radxa-pkg/linux-aw2511` 的 `6.6.98-4` release 下载；
- **交叉验证**：官方 `at-spi2-common_…deb` 里的 `.mo` 文件 md5 == 板子 md5sums 里的期望值
  → 证明「md5sums 数据库可信」+「官方 deb 就是正确内容」。

### 4.2 怎么落地

**方式一（推荐，最省事）：在 WSL 里挂载根分区后拷**

```bash
# WSL 里（管理员）
wsl --mount \\.\PHYSICALDRIVE<N> --partition 3     # p3 = 根分区，读挂
# 进入 WSL：
sudo mkdir -p /mnt/a7a && sudo mount /dev/sdX3 /mnt/a7a     # 若 --mount 没自动挂
cd /mnt/d/Code/radxa/a7a-rescue-2026-09-28
sudo bash apply-restore.sh --root /mnt/a7a --check          # 先干跑
sudo bash apply-restore.sh --root /mnt/a7a                  # 再真跑
```

`apply-restore.sh` 的行为：解包 → 逐个比 md5 → 只拷**内容不一致**的那些（用「写临时文件再 rename」
避免中途掉电留半个文件）→ 按 deb 里记的权限 `chmod`。

**方式二：上电后在板子上跑**

把 `restore-payload.tar` + `apply-restore.sh` 拷进板子（U 盘 / scp），
`sudo bash apply-restore.sh`（默认 `--root /`）。
⚠️ 前提是板子能起来 —— 所以**优先用方式一**，因为方式一不依赖板子能启动。

### 4.3 恢复 dpkg 数据库

```bash
# 用 09-22 的备份覆盖那个全随机的 status
sudo zcat /var/backups/dpkg.status.1.gz > /mnt/a7a/var/lib/dpkg/status
sudo chmod 644 /mnt/a7a/var/lib/dpkg/status
```

同时把 `rescue5` 里的 `/var/lib/dpkg/info/` 整体铺回去（那里有 3392 个文件：
`.md5sums` / `.list` / postinst 脚本），因为 status 需要它们配套：

```bash
tar -xzf a7a-rescue-2026-09-28/rescue5.tgz -C /mnt/a7a var/lib/dpkg/info
```

> 说明：09-22 的 status 里那 93 个包的**版本号偏旧**（少了 09-27 那次升级）。
> 这是**故意的**——上电后用 `apt` 重新升一遍即可（见 §6），
> 让 postinst 正常跑完，比手工摆文件干净得多。

### 4.4 关于内核（本轮结论：什么都不用做）

留档备查，**本次不执行**：

- 原版 `linux-image-6.6.98-4-aw2511_6.6.98-4_arm64.deb` 从
  `https://github.com/radxa-pkg/linux-aw2511/releases/download/6.6.98-4/` 可下载
  （23,693,128 B）；已下载并放在 `kernel-6.6.98-4/`。
- 若将来真需要换到 6.6.98-5：从
  `https://radxa-repo.github.io/a733-trixie-test/pool/main/l/linux-upstream/linux-image-6.6.98-5-aw2511_6.6.98-5_arm64.deb`
  （23,717,256 B，md5 `2f84192ee997b346f8c840794ea54583`）下载，已放在 `kernel-6.6.98-5/`。
  该包含 A7A 的 DTB（`sun60i-a733-cubie-a7a.dtb`）与 1139 个模块。
  **注意：deb 里的 vmlinuz 是 gzip 压缩态，装完后必须由 `radxa-bootutils` 的 postinst 解压**
  —— 手工拷文件的话要自己 `gzip -dc`，否则 U-Boot 起不来。
- 好消息（若真要走这条路）：6.6.98-5 的配置里 `CONFIG_EXT4_FS=y`、`CONFIG_MMC=y`、
  `CONFIG_AW_MMC=y`、`CONFIG_AW_UART_NG=y`、`CONFIG_DWMAC_SUNXI=y` **全是内建**，
  所以它**不依赖 initrd 就能挂载 ext4 根分区并起网卡**，可以临时把 `initrd` 那行去掉应急。

---

## 5. 阶段 D：上电前检查清单

在 `wsl --unmount` 之前，对着挂载点逐条确认：

```bash
R=/mnt/a7a
# 1) 内核链完整
ls -la $R/boot/vmlinuz-6.6.98-4-aw2511        # 应存在，大小 24336896（未压缩）
file $R/boot/vmlinuz-6.6.98-4-aw2511          # 应含 "PE32+" / "AArch64"；若显示 gzip 就错了
ls -la $R/boot/initrd.img-6.6.98-4-aw2511     # 应存在，约 48 MB
ls $R/usr/lib/linux-image-6.6.98-4-aw2511/allwinner/sun60i-a733-cubie-a7a.dtb

# 2) extlinux 指向正确
cat $R/boot/extlinux/extlinux.conf
#   linux /boot/vmlinuz-6.6.98-4-aw2511
#   initrd /boot/initrd.img-6.6.98-4-aw2511
#   fdtdir /usr/lib/linux-image-6.6.98-4-aw2511/

# 3) fstab 完好（fstab 在 rescue1 里，坏了就铺回去）
cat $R/etc/fstab

# 4) 关键可执行文件已恢复（不再是随机字节）
for f in usr/bin/python3.13 usr/bin/git usr/libexec/at-spi2-registryd; do
  printf '%s  ' "$f"; head -c 4 "$R/$f" | xxd -p
done
#   前 4 字节应是 7f454c46（ELF 魔数），不是随机值

# 5) dpkg status 可解析
LC_ALL=C grep -c '^Package: ' $R/var/lib/dpkg/status     # 应约 749
```

**全部通过 → `wsl --unmount \\.\PHYSICALDRIVE<N>` → 拔卡 → 插回板子 → 上电。**

---

## 6. 阶段 E：上电与验收

### 6.1 上电

- 插卡，接串口（`console=ttyAS0,115200n8`）看启动日志；U-Boot 菜单有 10 秒超时，
  默认项 `l0`，另有 `l0r`（追加 `single`，救援模式）。
- 起不来的话：`l0r` 进单用户，先看 `dmesg | grep -i -e ext4 -e mmc`。

### 6.2 起来之后立刻做

```bash
# 1) 让 apt 把 09-27 那次升级重新做一遍（会重跑 postinst：depmod / initramfs / ldconfig）
sudo apt-get update
sudo apt-get -f install
sudo apt-get dist-upgrade

# 2) 重新生成模块依赖与 initramfs（保险）
sudo depmod -a $(uname -r)
sudo update-initramfs -u -k all
sudo u-boot-update

# 3) 验收
dpkg -V 2>&1 | head -50
```

**验收时的预期**：`dpkg -V` **仍然会报**下面这些 —— 它们是 §1.2 的假阳性，
**不是没修好**：

```
??5??????   /boot/vmlinuz-6.6.98-4-aw2511                    ← 形态转换
??5??????   /lib/modules/6.6.98-4-aw2511/modules.dep         ← depmod 再生（6 个）
??5??????   /usr/share/applications/mimeapps.list            ← 共享路径
??5??????   /usr/share/desktop-base/kf5-settings/kdeglobals  ← 本地合法改写
??5??????   /usr/lib/firmware/cypress/cyfmac43455-sdio.bin   ← 共享路径
```

判断「修好了」的正确标准是：**除了这 10 条，其余 `??5??????` 全部消失**。

---

## 7. 阶段 F：根因线（不改这些，下次还会坏）

按「性价比」排序：

### 7.1 立刻做（零成本，改配置文件）

```bash
# (a) 关掉 fstrim.timer —— 它与错误爆发点高度相关
#     实测：fstrim.timer 上次运行 01:29:42，最后一条 EXT4 错误 01:29:43
sudo systemctl disable --now fstrim.timer
#     原因：fstrim 对根 ext4 报 "FITRIM ioctl failed: Bad message"（失败），
#     但对两个 vfat 分区报了「整盘 trim」（299.8 MiB = 整个 p2）。
#     vfat 没有块位图，整盘 discard 在廉价卡固件上是已知的坑。

# (b) journald 改成内存日志，砍掉一大块周期性写入
sudo sed -i 's/^#\?Storage=.*/Storage=volatile/' /etc/systemd/journald.conf
sudo systemctl restart systemd-journald

# (c) 挂载加 errors=remount-ro（出错就只读，而不是继续坏）
#     在 /etc/fstab 里根分区的 options 追加 errors=remount-ro

# (d) 更频繁地自检，早发现早处理
sudo tune2fs -c 20 -i 7d /dev/mmcblk1p3

# (e) 把每 60 秒跑一次的 net-ensure 降频（它是持续的写入源之一）
#     确认 radxa-net-ensure.timer 能否改成 5 分钟
```

### 7.2 需要动手的（一次）

- **换电源**：用官方 5V/3A 或更好的适配器 + 换一根粗线，**只试一次**。
  如果换完 `dmesg` 里仍然出现
  `No vqmmc/vd18sw/vq18sw regulator found` / `manual set ocr` / `Cann't get pin bias hs pinstate`
  这类信息，就说明**不是电源问题**，别再折腾电源。
- **换一张好卡**：至少换一张 A1/A2 的工业级或三星/闪迪正品卡。廉价卡在高频写 +
  整盘 discard 下很容易出这种「位图与数据不一致」。

### 7.3 结构性方案（推荐，一劳永逸）

**把根分区迁到 USB SSD 或 eMMC**，SD 卡只留 `/boot` 或干脆不用。
理由：本次坏的是 SD 卡；A733 的 SD 控制器与卡固件之间的组合是已知薄弱环节。
迁移手册已存在：`docs/a7a-migrate-to-usb-ssd.md`（方案 A 温和迁移、零重启风险）。

### 7.4 关于根因的判断（诚实版）

- 9/18 的报告把根因定为 **`CONTROLLER_FAULT`（板子侧）**，本次启动日志仍能看到同类信息；
- **但** `No vqmmc regulator found` 这类告警在 Allwinner 板子上**很常见**，
  只能当**旁证**，不能单独定案；
- 目前能确定的是：**文件系统层的表现是「块位图与已分配块不一致」**，
  至于它是由控制器时序、卡固件、电源还是别的引起，**本轮没有定论**。
  所以 §7.2 的「换电源只试一次」是**排除法**，不是诊断结论。

---

## 8. 回滚点与放弃条件

### 什么时候该放弃修、直接重刷

- `e2fsck -fy` 跑完之后 `e2fsck -fn` **仍然报大量错误**（尤其 `Block bitmap differences`
  反复出现）→ 说明盘本身在恶化，别修了；
- 修完上电，卡在 U-Boot 或 `initramfs` 之前，且 `l0r` 单用户也进不去；
- 修完能进系统但 `dmesg` 里 mmc 报 `timeout` / `I/O error` 持续刷屏。

### 重刷的现成材料

- `docs/rebuild-manual.md`、`docs/a7a-rebuild-manual.md` — 重建手册
- `docs/a7a-mirror-and-safe-upgrade.md` — 换源与安全升级
- `docs/a7a-power-and-storage-checklist.md` — 电源与存储检查清单
- `docs/a7a-migrate-to-usb-ssd.md` — 迁到 USB SSD

### 数据面：已经抢救出来的东西

`D:\Code\radxa\a7a-rescue-2026-09-28\`（每个包都双向 md5 核对过）：

| 包 | 大小 | md5 | 内容 |
|---|---|---|---|
| `rescue1.tgz` | 438,733,124 | `fc47a7556fdb9e5623f77ec6c914e5ec` | `/etc` `/root` `/home/radxa` |
| `rescue2.tgz` | 34,280,062 | `647983bc76429c25469292b458bbe276` | `/usr/local` `/var/log` `/var/spool` |
| `rescue3.tgz` | 58,861,593 | `60422070c6caa84a25e8edfcaa0f8065` | `/boot` `/config` |
| `rescue4.tgz` | 391,980 | `1f4700b1358d344410503ec89a932fae` | dpkg 数据库备份 |
| `rescue5.tgz` | 72,095,773 | `123c8a969259e4d8a440c6c2ffe9d159` | `/var/lib/dpkg/info`（3392 个）+ apt 缓存（93 个 deb） |
| `packages-2026-09-22.txt` | 61,448 | — | 749 个包 + 精确版本号 |

**没抢救的**：`/usr/bin`、`/usr/lib`、`/usr/share`、`/var/lib`（除 dpkg）、`/lib/modules` 的大部分。
—— 但这些**都能从官方源重装**（`apt-get --reinstall`），所以不是损失。

---

## 9. 命令速查（按顺序抄）

```powershell
# ── 阶段 A：停写 ─────────────────────────────────
# （物理动作）断电 → 拔 microSD → 插读卡器

# ── 阶段 B：离线修卡 ─────────────────────────────
cd D:\Code\radxa\radxa_utlra\recovery\tools
.\sd_offline_repair.ps1 -Check
.\sd_offline_repair.ps1 -Repair          # 管理员

# ── 阶段 C：拷回 207 个文件 + 恢复 dpkg status ────
# （管理员 PowerShell）
wsl --mount \\.\PHYSICALDRIVE<N> --partition 3
wsl -d Ubuntu-24.04 -- bash -lc "mount /dev/sdX3 /mnt/a7a 2>/dev/null; cd /mnt/d/Code/radxa/a7a-rescue-2026-09-28 && bash apply-restore.sh --root /mnt/a7a --check"
wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/d/Code/radxa/a7a-rescue-2026-09-28 && bash apply-restore.sh --root /mnt/a7a"
wsl -d Ubuntu-24.04 -- bash -lc "zcat /mnt/a7a/var/backups/dpkg.status.1.gz > /mnt/a7a/var/lib/dpkg/status"
wsl -d Ubuntu-24.04 -- bash -lc "tar -xzf /mnt/d/Code/radxa/a7a-rescue-2026-09-28/rescue5.tgz -C /mnt/a7a var/lib/dpkg/info"

# ── 阶段 D：上电前检查（见 §5）────────────────────
# ── 阶段 E：上电 + apt 收尾（见 §6）───────────────
# ── 阶段 F：根因线（见 §7）───────────────────────
```

---

## 10. 本轮新增/修正的判据（补进 skill）

1. **「文件内容变了但 ctime/mtime/size 不变」= 块被别的文件覆盖。**
   这是块位图损坏的独有指纹，也是唯一可靠的现场判据。
2. **EXT4 错误计数不涨 ≠ 损坏已停止。** 覆盖是「合法写入」，内核不会报错。
   → 必须用「重复快照 + 比 md5」来测，不能用 `journalctl | grep -i ext4`。
3. **`dpkg -V` / `md5sums` 比对的假阳性有 6 类**（见 §1.2）。
   看到「N 个文件损坏」不要直接下结论，先按这 6 类筛一遍。
   **尤其**：`/boot/vmlinuz-*` 几乎必然是假阳性（发行版会在 postinst 里解压内核）。
4. **判「内核能不能用」不要比 md5**，要比**解压后**的内容：
   `gzip -dc <deb 内 vmlinuz> | cmp - <板子上的 vmlinuz>`。
5. **判 initrd 好坏用 `unmkinitramfs`，不要用 `zcat`** ——
   Debian initrd 是「未压缩 cpio + zstd 压缩 cpio」的**拼接**，`zcat` 直接失败。
   更强的校验：把 initrd 里的 `.ko.xz` 与磁盘上的同名模块逐个比 md5。
6. **`md5sums` 里记录的路径可能带空格**，用 `awk '{print $2}'` 会截断，
   产生一批「文件不存在」的假阳性。要取整行（`sed 's/^[0-9a-f]\{32\}[ \t]*//'`）。
7. **发行版旧版本的包不会消失**：`radxa-repo.github.io` 是 GitHub Pages，
   包实体在 `radxa-pkg/<pkg>` 的 **GitHub Release** 里，旧 tag 长期保留。
   下架只是从 `Packages` 索引里移除，**Release 还能下**。
8. **apt 缓存不可信**：块覆盖会保留文件大小，`ls -la` 看不出来。
   批量验真的快办法：`head -c 8 f.deb` 必须等于 `!<arch>`。
9. **`xargs` 不带 `-r`、空输入会执行一次无参数命令**；`md5sum` 无参数会读 stdin
   → SSH 会话永久挂死。（实测 `timeout 5` RC=124，加 `-r` RC=0）
10. **所有文本处理都要 `LC_ALL=C`**，`sort` / `comm` / `grep` / `join` 一个都不能漏。
    非 C locale 下 `[!-~]` 按 collation 解释，同一条记录匹配 0 次 vs 1 次。

# A7A 第三次烧卡事件（2026-09-27 ~ 09-28）—— 现场记录与处置

> 板子：`radxa-cubie-a7a` @ `192.168.10.165`，内核 `6.6.98-4-aw2511`
> 现场状态：**板子活着、SSH 可达；文件系统已损坏，且仍在低频偶发改写（未停止）**
> 处置：**全程没有重启**（用户明确要求：「重启后就打不开了，上次也这样」）
> 数据：**5 个抢救包，全部双向 md5 核对通过**
>
> 🔴 **本文已按 09-28 晚间的二次取证更正。** 首次成文时的四条结论被推翻，
> 文中凡带 **[更正]** 标记的都是改过的。**以 `a7a-2026-09-28-repair-plan.md` 为准**
> （那份是可执行方案，结论已是更正后的）。

---

## 一、结论（先看这七条）

1. **数据安全了。** 见 §五。不可替代的东西（`/etc/mihomo` 节点配置、`commander-*` 全套、
   `sau/cookies` 登录态、`nodes_data`、`/var/log` 的 5 次启动日志、`/boot` 全套、
   **apt 缓存的 93 个 .deb**、**损坏前的 dpkg 数据库**）都出来了，且逐包校验。
   **[更正]** 其中 apt 缓存那 93 个 deb **74% 已坏**（见 §2.11），只能当「版本号线索」用，
   不能当恢复源。

2. **不要重启 —— 这次终于有物理解释了。** 不是迷信，是「块位图缓存 + `ext4lazyinit`」
   的必然结果，见 §三。**[更正]** 但「不重启 = 不坏」是错的，见第 3 条。

3. **损坏没有停止，只是低频偶发。** **[更正 · 推翻了首次成文的结论]**
   首次成文时看到「EXT4 错误数 9.7 小时零新增」，就写了「已停止扩散」。
   **这是错的** —— 实测同一个 deb 在 11:11:39（rescue5 打包）与 11:19 之间**内容被改写**，
   而它的 `ctime`/`mtime`/`size` **一个字节都没动**。**EXT4 不会为这种覆盖报错**
   （分配器以为那个块是空闲的，写进去完全合法）。见 §2.12。
   → 结论：**只要板子还在跑，写入就在发生，就还有可能盖掉新东西。尽快停掉。**

4. **`e2fsck` 只是止血，不是治伤。** 它修的是**文件系统结构**（块位图、inode、目录项、extent 树），
   **完全修不了已经被写坏的文件内容**。修完 `e2fsck` 之后，那 207 个文件仍然是坏的。
   「e2fsck 跑完就好了」是错觉。

5. **损坏范围已精确量化：`226 条 = 207 个真损坏 + 19 个假阳性`。** **[更正]**
   首次成文报的「226 个文件 / 43558（0.52%）、涉及 33 个包」里，
   **有 19 个是 `md5sums` 比对的系统性假阳性**（形态转换 / 共享路径 / 本地合法改写 /
   depmod 再生 / 路径截断 bug），见 §2.7。真损坏是 **207 个**。

6. **apt 缓存不可用。** **[更正]** 93 个 deb 里 **69 个的 `ar` 魔数已坏（74%）**。
   坏包大小正确、`ls -la` 看不出来 —— 和文件被覆盖是同一个指纹。
   → 恢复用的 deb **一律从官方源重下**，见 §2.11。

7. **内核启动链完好，不需要换内核。** **[更正 · 推翻了首次成文最重要的那条]**
   首次成文说「`vmlinuz` 已损坏，且 `6.6.98-4` 已下架，上电前必须先换 6.6.98-5」。
   **整条作废。** 实测 `gzip -dc`（官方 6.6.98-4 deb 内的 vmlinuz）与板子上那份
   **逐字节相同** —— 板子上的 `vmlinuz` 是**未压缩的 arm64 Image**，
   而 `md5sums` 记的是**出厂 gzip 压缩态**，所以「对不上」是**形态转换的假阳性**。
   加上 DTB / 模块 / initrd 全部验证通过，**启动链整体完好**，见 §2.6 与 §2.13。

---

## 二、现场事实（全部实测）

### 2.1 文件系统

| 项 | 实测值 | 含义 |
|---|---|---|
| `Filesystem state` | `clean with errors` | 有错，但没被强制转只读 |
| **`Block count`** | **16299259（与 9/18 一致，没变）** | **判据 8：几何完好 → `e2fsck` 可修** |
| `Free blocks` | 14012777 | 与 `df` 的 7.7G/62G 自洽 |
| `Block size` | 4096 | |
| `Lifetime writes` | 13 GB | **写入量很低 → 卡没被写坏，不是磨损** |
| `Last mount time` | `Thu Jan 1 00:00:08 1970` | **可疑**：挂载时 RTC 还在 epoch。别据此判断「最后一次挂载时间」 |
| `FS Error count` | **16** | 首次 `09-27 11:59:43`，最后 `09-28 01:29:43`。⚠️ **[更正]** 这个数字停住**不代表损坏停了**，见 §2.12 |
| `First/Last error function` | `ext4_validate_block_bitmap:421` | **块位图校验和错**（元数据层） |
| 根分区挂载参数 | `rw,relatime` | ⚠️ **没有 `errors=remount-ro`** → 出错只记日志、继续跑 |
| `/etc/fstab` 根分区行 | `UUID=… / ext4 defaults 0 1` | 同上 |

### 2.2 块设备布局（**注意 `/boot` 不是独立分区**）

```
mmcblk1p1   16M vfat  /config        ← U-Boot 配置（config.txt + logo.bmp）
mmcblk1p2  300M vfat  /boot/efi      ← UEFI 变量存储（ubootefi.var）
mmcblk1p3 62.2G ext4  /              ← **被损坏的就是它**
zram0      1.9G swap  [SWAP]
mtd0          8M      "spi0.0"       ← SPI NOR（U-Boot 在这里）
```

**`/boot` 是根分区上的普通目录**（`findmnt /boot` 返回「不是挂载点」）。
→ 所以 `extlinux.conf`、`vmlinuz`、`initrd`、三份 A7x dtb **全都躺在被损坏的 p3 上**。

> ⚠️ 这是第一轮抢救（rescue1 = `/etc /root /home/radxa`）**漏掉的一整块**，
> 后来补成了 rescue3。

### 2.3 坏掉的块组（本次启动，15 个）

```
64  127  240  249  250  251  252  254  256  368  375  377  378  384  497
```

`497` 是**最后一个块组**；`249–256`、`375–384` 是成簇的。
`bg 64` 与 9/18 那次事故是同一个 —— **同一个位置反复坏**。

### 2.4 启动历史（`journalctl --list-boots`）

```
-4  2026-04-13 19:38 → 09-27 10:43:13    跑了 5 个多月   ext4 错误 0 条
-3  09-27 10:43:18 → 10:51:38             8 分钟         ext4 错误 0 条
-2  09-27 10:51:24 → 11:15:04            24 分钟         ext4 错误 4 条
-1  09-27 11:14:14 → 11:59:51            46 分钟         ext4 错误 4 条
 0  09-27 11:59:43 → 现在（22h+）                         ext4 错误 16 条
```

**首次启动跑了 5 个多月、零 ext4 错误。** 损坏不是慢慢磨出来的 ——
它从 9/27 那个重启周期开始。76 分钟内重启 4 次。

**而本次（boot 0）最后一条错误是 `09-28 01:29:43`，之后 9.7 小时零新增** ——
因为期间没有重启。

> ⚠️ **[更正] 这条不能推出「已停止扩散」。**
> EXT4 的错误计数**天然会停**：块位图在内存里有缓存，某个块组校验失败一次就被标记，
> 后续分配不再重复校验它 → 日志自然安静下来。
> 而真正在造成损害的动作（把已占用块当空闲块发出去）是**合法写入**，内核**不会记 error**。
> 本次实测到：安静期内**仍有文件被改写**，见 §2.12。
> **测「还在不在坏」的唯一办法是「重复快照 + 比 md5」，不是看 `journalctl | grep -i ext4`。**

### 2.5 两种损坏形态（**本次最重要的取证**）

文件内容被写坏有**两种截然不同的指纹**：

| 文件 | 大小 | 实测形态 | 说明 |
|---|---|---|---|
| `/boot/extlinux/extlinux.conf.bak` | 1,361 B | **1,361 字节全是 NUL（100%）** | 长度对、内容全 0 |
| `/var/backups/dpkg.status.0` | 712,228 B | **非 NUL 字节数 = 0** | 同上，而且这两个文件的 mtime 都是 **09-27 14:37 / 14:45** |
| `/usr/bin/python3.13` | 6,673,712 B | 头 16 字节 = `c2 e7 a6 88 5d 6d 1d da …` | 应为 `7f 45 4c 46`（ELF 魔数） |
| `/usr/bin/git` | — | 头 16 字节 = `31 34 b2 4a ae be 09 18 …` | 应为 ELF |
| `/var/lib/dpkg/status` | — | 开头就是乱码 | `field name '??nxuk?*K~…' must be followed by colon` |
| `/boot/vmlinuz-6.6.98-4-aw2511` | 24,336,896 B | **头完全正常**，md5 对不上 | **[更正] 假阳性**（形态转换），见 §2.6 |
| `/lib/modules/…/modules.symbols` | — | **头正常**（`# Alias`），md5 对不上 | **[更正] 假阳性**（depmod 再生），见 §2.7 |

**两条必须记住的推论：**

- **「头 4 字节坏了」是 ELF 可执行文件死掉的直接原因**：
  内核拒绝执行坏魔数的 ELF → 回退让 shell 当脚本解析 →
  `python3 --version` 报 `syntax error near unexpected token ')'`、
  `git --version` 报 `cannot execute binary file: Exec format error`。
  **这两个错误信息看起来完全不像存储损坏，极易误判成「系统装坏了」。**
- **头正常不代表文件好。** 损坏是**块级的、位置随机的**，可能落在中后部。
  > ⚠️ **[更正]** 首次成文时举的两个例子（`vmlinuz`、`modules.symbols`）
  > **后来都被证明是假阳性** —— 它们的 md5 对不上不是损坏，而是「文件被合法地重写过」。
  > 所以判断「头好但 md5 不对」时，**必须先排除 §2.7 的 6 类假阳性**，
  > 再下「中后部损坏」的结论。

### 2.6 `vmlinuz` 的结构分析（**结论：它是好的，这是假阳性**）**[更正]**

用 Python 解了 PE 头：

```
file size : 24336896
DOS 头    : 4d 5a 40 fa 19 1c 4a 14       ← MZ
0x38      : b'ARMd'                        ← arm64 标记，正常
e_lfanew  : 0x40                           ← arm64 EFI stub 的正常值
PE 签名   : b'PE\x00\x00'                  ← 正常
Machine   : 0xaa64  (ARM64)                ← 正常
节数      : 2
  .text  rawsize=0x1330000  rawptr=0x0010000  end=0x1340000
  .data  rawsize=0x03f5a00  rawptr=0x1340000  end=0x1735a00   ← = 文件长度，分毫不差
```

**PE 结构完全完好**。而 512KB 粒度的**熵分布全程在 6~7.5，没有 ≥7.9 的高熵区**
→ 这个 arm64 内核是**未压缩的 `Image`**（不是 `EFI_ZBOOT` 的压缩载荷）。

**🔴 [更正] 首次成文时，我据此写了「能不能启动无法从结构上预判，只能保守换掉」。错。**

真相比这简单得多 —— **它根本没坏**：

```
① 从官方 deb 里取出出厂 vmlinuz（gzip 压缩态，1f8b 开头）
② gzip -dc 解压 → 得到未压缩 arm64 Image
③ 与板子上 /boot/vmlinuz-6.6.98-4-aw2511 逐字节比对 → cmp 无差异
④ 两者 md5 都是 f7c1fcbef265dd741a4dbbd1e2d71852
```

**为什么 `md5sums` 会说它不对？** 因为 `radxa-bootutils` 的 **postinst** 会把内核
**解压**成未压缩 Image 再落盘（U-Boot 直接引导 `Image`），
而 `md5sums` 里记的是 **deb 里的出厂压缩态**。两者天生不同。

→ **这是「形态转换」类假阳性**（§2.7 类别 1），**不是损坏**。

**这条更正推翻了首次成文最重要的一个结论**（原文：「上电前必须先把 vmlinuz 换回好的」）。
现在：**启动链整体完好，不需要换内核，也不需要去找 6.6.98-4 的原版包重装**。
（原版包确实还找得到，见 §2.13 与 repair-plan §4.4，但**本次用不上**。）

（旁证：`vmlinuz` 内嵌的 config blob 在 `0xE00038`，`gzip -t` 通过、解出 241,277 字节 =
`/boot/config-6.6.98-4-aw2511` 的大小。）

### 2.7 损坏范围量化：`226 条 = 207 真 + 19 假` **[更正]**

`dpkg -V` 跑不了（`/var/lib/dpkg/status` 开头就是乱码），
改用 **`/var/lib/dpkg/info/*.md5sums` 绕过**它做全量比对：

```
md5sums 数据库          : 792 个文件，43766 行
其中格式合法（可比对）  : 43558 行        （208 行是数据库自身被写坏的垃圾行）
完全一致                : 43331
不一致 / 读不出         : 227  → 去重后 226 个文件
内核 I/O error（同期）  : 0 条            （读这张卡是安全的）
```

**⚠️ 关键更正：226 不是「226 个损坏」，而是「226 条 md5 不一致」，其中 19 条是假阳性。**

```
226 条  =  207 个真损坏  +  19 个假阳性
```

#### 19 个假阳性的确切构成（来自 `restore-manifest.json`）

| 类别 | 条数 | 具体文件 |
|---|---|---|
| **形态转换** | 1 | `/boot/vmlinuz-6.6.98-4-aw2511`（postinst 解压内核，md5sums 记的是压缩态） |
| **共享路径** | 2 | `/usr/share/applications/mimeapps.list`（radxa-system-config-common vs -allwinner）<br>`/usr/lib/firmware/cypress/cyfmac43455-sdio.bin`（firmware-brcm80211 vs radxa-firmware） |
| **本地合法改写** | 1 | `/usr/share/desktop-base/kf5-settings/kdeglobals`（只差 `BrowserApplication=` 一处，正好 +5 字节） |
| **depmod 再生** | 6 | `/lib/modules/6.6.98-4-aw2511/modules.{dep,dep.bin,symbols,symbols.bin,alias,alias.bin}` |
| **路径解析 bug** | 9 | 7 个 `/usr/lib/firmware/brcm/brcmfmac*-sdio.*.txt` + 2 个 `/usr/share/alsa/ucm2/NXP/iMX8/…/Librem 5*.conf`（**文件名含空格，板子侧生成清单时被截断**） |
| **合计** | **19** | |

**为什么这 19 个会被误报？** 因为 `md5sums` 比对的前提是「文件被安装后就没再动过」。
而这 5 类都违反了前提：内核被 postinst 解压、共享路径只有一份落盘、
`kdeglobals` 被本地改过、`modules.*` 被 `depmod` 重算、含空格的路径被清单生成器截断。

> **教训：看到「N 个文件损坏」，先按这 5 类筛一遍再下结论。**
> 尤其 `/boot/vmlinuz-*` —— 在 Debian 系上几乎必然是假阳性。

#### 抽样验证 5/5 确认为真（这 5 个都在 207 里）

| 文件 | 头 8 字节实测 | 应该是什么 |
|---|---|---|
| `…/lv/LC_MESSAGES/at-spi2-core.mo` | `d5 47 f2 28 4a cd 49 7a` | `de 12 04 95`（GNU MO 魔数） |
| `libXmuu.so.1.0.0` | `5f 7e 0b c6 c0 f4 b0 94` | `7f 45 4c 46`（ELF） |
| `man1/git-log.1.gz` | `39 b3 02 b7 a6 39 b9 e5` | `1f 8b 08`（gzip） |
| `libexec/at-spi-bus-launcher` | `48 65 8f 33 d7 ef 34 61` | `7f 45 4c 46`（ELF） |
| `NotoSansCJK-Regular.ttc` | `a8 67 d0 f4 37 ed 6b a6` | `00 01 00 00`（TrueType） |
| **对照组** `libc.so.6` | `7f 45 4c 46 02 01 01 03` | ✅ 正常 |
| **对照组** `/usr/bin/tar` | `7f 45 4c 46 02 01 01 00` | ✅ 正常 |

**207 个真损坏文件分布在 27 个包**（**[更正]** 首次成文写的「226 个 / 33 个包」把 19 个假阳性也算进去了；下表按 `restore-manifest.json` 重算）：

```
     93 at-spi2-common               1 libxss1:arm64
     25 git ★                        1 libxpm4:arm64
     22 libauthen-sasl-perl          1 libxmuu1:arm64
     14 git-man                      1 libtext-iconv-perl:arm64
     12 fonts-liberation             1 libopenal1:arm64
     11 fonts-noto-cjk               1 libio-compress-brotli-perl
      4 libcdio19t64:arm64           1 libdecor-0-0:arm64
      3 libnspr4:arm64               1 libclone-perl:arm64
      2 libxxf86dga1:arm64           1 libatspi2.0-0t64:arm64
      2 at-spi2-core                 1 libatopology2t64:arm64
      2 python3-pip-whl              1 libatk-bridge2.0-0t64:arm64
      2 python3-setuptools-whl       1 libatk1.0-0t64:arm64
      1 python3.13-minimal           1 firmware-brcm80211
      1 python3.13 ★                 ─────────────
                                     （共 27 个包 / 207 个文件）
```

★ = 直接导致板子功能失效的两个：`python3.13`（`/usr/bin/python3.13` 坏 → 整个 python 生态停摆）
和 `git`（`/usr/bin/git`、`git-shell`、`scalar` 等 25 个文件坏 → git 完全不可用）。

> ⚠️ **[更正] `linux-image-6.6.98-4-aw2511` 已经从这个表里消失了** ——
> 它原来那 7 条（`vmlinuz` + 6 个 `modules.*`）**全部是假阳性**（§2.6 / §2.7 类别 1、4）。
> **所以「内核坏了、重启起不来」这个说法不成立。**
> `firmware-brcm80211` 也从 9 条降到 **1 条真损坏**（其余 8 条是路径截断假阳性）。

**这 27 个包和 apt 缓存高度重合** —— 缓存里 93 个 deb 的 mtime 是 `09-27 14:30`，
正是那次 apt 升级；坏文件落的也正是那批包。
（`firmware-brcm80211` 不在缓存里，说明它**没有**在 9/27 升级 ——
它是被「分配器发错块」覆盖的受害者，`vmlinuz` 的 mtime 还是 `08-04` 也是同类证据。）

### 2.8 还活着的命令 vs 已死的命令

**已死（ELF 头被写坏）：**

```
python3        → /usr/bin/python3: line 1: syntax error near unexpected token `)'
git            → bash: /usr/bin/git: cannot execute binary file: Exec format error
```

**还活着（都是完好 ELF）：**

```
dpkg (1.22.22)   apt (3.0.3)   systemctl/journalctl (systemd 257)   tar (1.35)
md5sum (coreutils 9.7)   bash   ls   cat   cpio   perl   e2fsck   tune2fs   dumpe2fs
```

> ⚠️ **更正 9/18 的判断**：`apt` / `dpkg` **没有**整体报废。
> `dpkg --version`、`apt --version` 都能跑；只有**依赖 `/var/lib/dpkg/status` 的操作**会失败
> （`dpkg -l`、`dpkg -V`、`apt install`）。这个区别很重要 —— 它意味着
> **只要把 `status` 从备份恢复回去，dpkg/apt 就能用**。

**引导链（[更正] 整条链都是好的）：**

- `/boot/vmlinuz-6.6.98-4-aw2511` → ✅ **完好**（§2.6：md5 不符是形态转换假阳性）
- `/boot/initrd.img-6.6.98-4-aw2511` → ✅ **完好**。
  判据见 §六 错误 2（`lsinitramfs`）。**[更正]** 首次成文说它「只有 `lsinitramfs` 佐证、
  没有 md5 级证据」—— **现在有了**：`unmkinitramfs` 解出 124 个模块，
  与磁盘上同名模块**逐个 md5 比对全部一致**。见 §2.13。
- `/boot/extlinux/extlinux.conf` → 干净文本（mtime 还是 `08-04`），指向正确
- `/boot/extlinux/extlinux.conf.bak` → **全 NUL**（§2.5）—— 这个是真的坏了，但它只是个 `.bak`，不影响启动

### 2.9 `systemctl --failed`

```
ap-power.service                   ← ExecStart=/usr/bin/python3 …  （python3 坏了）
fstrim.service                     ← 见 §2.10
lightdm.service
man-db.service
NetworkManager-dispatcher.service
plymouth-quit.service
```

### 2.10 周期性写入源（`systemctl list-timers --all`）

```
radxa-net-ensure.timer        每 60 秒        ← 最频繁！每轮都写 journal
ap-power.timer                每 ~5 分钟
systemd-tmpfiles-clean.timer  每天
dpkg-db-backup.timer          每天 00:00      ← 会重写 /var/backups/dpkg.status.0
man-db.timer                  每天
apt-daily.timer               每天
apt-daily-upgrade.timer       每天
e2scrub_all.timer             每周
fstrim.timer                  每周
```

`60 秒周期与 radxa-net-ensure.timer（OnUnitActiveSec=60s）完全重合` ——
这解释了一个反直觉现象：**「报错」本身在制造写入**。
一个每 60 秒写日志的服务，在坏盘上就是一台每分钟戳一次伤口的机器。

**`fstrim` 的实况（这条更正了 9/18 的猜测）：**

```
Sep 28 01:29:42  Starting fstrim.service …
Sep 28 01:29:43  fstrim: /: FITRIM ioctl failed: Bad message            ← 根分区失败！
Sep 28 01:29:43  /boot/efi: 299.8 MiB (314368000 bytes) trimmed on /dev/mmcblk1p2
Sep 28 01:29:43  /config:   15.6 MiB  (16308224 bytes) trimmed on /dev/mmcblk1p1
Sep 28 01:29:43  fstrim.service: Main process exited, code=exited, status=64/USAGE
```

- **对根分区（ext4）它是失败的**（块位图校验和错 → FITRIM 拒绝）→
  **所以 fstrim 不是「全 NUL 文件」的元凶。**
- **但它对两个 vfat 分区报了「整盘 trim」**（299.8 MiB = 整个 p2，15.6 MiB = 整个 p1）。
  vfat 没有块位图，这种「整盘 discard」在廉价卡固件上是**出了名的坑**。
  旁证：`/boot/efi/ubootefi.var` 304 字节里只有 170 字节非 NUL。
- **结论：这块卡上 `fstrim.timer` 是净负面的，修完之后应该禁掉。**

### 2.11 apt 缓存本身也不可用 **[新增]**

`/var/cache/apt/archives` 有 93 个 deb（86 MB，mtime `09-27 14:30`）。
**其中 69 个的 `ar` 魔数已经损坏（74%）：**

```
$ head -c 8 at-spi2-core_2.56.2-1+deb13u2_arm64.deb
88104697 179afdc5 ...        ← 应为 "!<arch>\n"
```

大小正确（59916）但内容是随机字节 —— 与文件被覆盖是**同一个指纹**，
`ls -la` 完全看不出来。

→ **结论：恢复用的 deb 一律从官方源重新下载，不用缓存里的。**
（缓存的价值只剩「记录出事时到底装了哪 93 个包 / 什么版本」。）

### 2.12 损坏**仍在发生**，而且 EXT4 不会告诉你 **[新增 · 最重要的一条更正]**

首次成文时看到「EXT4 错误数 9.7 小时零新增」，写了「已停止扩散」。**错。**

**实测证据：**

| 时间 | 事件 |
|---|---|
| 09-28 11:11:39 | `rescue5.tgz` 打包完成，其中 `/var/cache/apt/archives/at-spi2-core_*.deb` 的 md5 = `78272a37…` |
| 09-28 11:19 | 再读同一个文件，md5 = `2a798987…` —— **内容变了** |
| —— | 但它的 `ctime` 始终是 `2026-09-27 13:03:47`，`mtime` 始终是 `2026-09-04 10:15:29`，**大小始终 59916** |

**inode 元数据一个字节都没动，文件内容却变了。** 这是「块被别的文件覆盖」的**独有指纹**。

**为什么 `journalctl | grep -i ext4` 看不到？**
因为**把已占用块当空闲块分配出去是合法写入** —— 分配器认为那个块是空闲的，
写进去完全合规，内核没有任何理由记一条 error。**所以错误计数不涨 ≠ 没在坏。**

**频率是低频偶发**：只观察到那一次改写（11:11→11:19 那 8 分钟）；
之后 7 分钟内 214 个样本文件做了 4 次快照（snap1..snap4，`11:21:29Z` / `11:22:17Z` /
`11:25:57Z` / `11:28:35Z`），md5 **完全一致**（都是 `b625bfe499999750da872ed409710be3`）。
→ **不是持续高频，但只要板子还在跑，写入就在发生，就还有可能盖掉新东西。**

> **测「还在不在坏」的唯一办法是「重复快照 + 比 md5」，不是看 ext4 错误数。**

### 2.13 内核启动链 —— 全部完好 **[新增]**

这是本轮最重要的更正。逐项验证：

| 组件 | 结论 | 依据 |
|---|---|---|
| `/boot/vmlinuz-6.6.98-4-aw2511` | ✅ **完好** | `gzip -dc`(官方 6.6.98-4 deb 内的 vmlinuz) 与板子上那份**逐字节相同**，md5 都是 `f7c1fcbef265dd741a4dbbd1e2d71852` |
| DTB | ✅ **完好** | 207 个坏文件里没有任何 dtb；`sun60i-a733-cubie-a7a.dtb` 未出现在清单里 |
| `/lib/modules/6.6.98-4-aw2511/**/*.ko.xz` | ✅ **完好** | 没有任何 `.ko` 被标记；被标记的只有 6 个 depmod 索引文件（假阳性） |
| `/boot/initrd.img-6.6.98-4-aw2511` | ✅ **完好** | `unmkinitramfs` 解出 **953 个文件 / 124 个模块**，与磁盘上同名模块**逐个 md5 一致（124 一致 / 0 不一致）** —— **这就是 md5 级证据**，见 §六 错误 2 的更正 |
| `extlinux.conf` | ✅ 指向正确 | `linux /boot/vmlinuz-6.6.98-4-aw2511`、`initrd /boot/initrd.img-6.6.98-4-aw2511`、`fdtdir /usr/lib/linux-image-6.6.98-4-aw2511/` |

> **→ 「上电前必须先把内核换成 6.6.98-5」这条旧要求作废。原版 6.6.98-4 也不用去找回来重装。**
> 唯一要做的是：**别把假阳性里的那个 `vmlinuz` 拷回去**（载荷包里已经把它排除并挪到
> `_DO-NOT-APPLY/`）。

### 2.14 dpkg 数据库现状 **[新增]**

| 文件 | 状态 |
|---|---|
| `/var/lib/dpkg/status`（当前） | ❌ **整文件随机**（非 NUL 709382/712228 ≈ 随机数据期望值） |
| `/var/backups/dpkg.status.0`（09-27 14:37） | ❌ **整文件全 NUL**（非 NUL 字节 = 0）——「刚写下去没落盘」的指纹 |
| `/var/backups/dpkg.status.1.gz`（09-22） | ✅ **gzip 完好，749 个包** → 已导出 `packages-2026-09-22.txt` |
| `/var/backups/dpkg.status.2.gz`（08-04） | ✅ 完好，752 个包（更旧，备用） |
| `/var/lib/dpkg/info/*` | ✅ 已整目录抢救；**md5sums 数据库本身可信** —— 已用官方 deb 反证：`at-spi2-common` 的 `.mo` md5 与 md5sums 期望值**完全一致** |

---

## 三、机理：为什么「重启会加速损坏」

这一条以前只有经验（「上次重启就坏了」），**这次有物理解释了**：

1. ext4 的**块位图在内存里有缓存**（buffer_head / page cache）。
   某个块组一旦校验失败，就被标记并缓存，**后续分配不再重复校验它**
   → 所以日志里的错误数**会停下来**。
2. **重启会清空这份缓存** → 所有块组重新校验 →
   一次性刷出大量 `bad block bitmap checksum`。
3. 同时 `ext4lazyinit` 在启动时**遍历并批量回写 inode 表** ——
   这是一次大范围写入，正好踩在最不可靠的写路径上。

**实测对照：**

| 事件 | ext4 错误数 |
|---|---|
| 首次启动跑了 5 个多月 | 0 |
| 9/27 一天重启 4 次 | 0 → 4 → 4 → 16 |
| 本次：01:29 之后 **9.7 小时不重启** | 16 → **16（零新增）** |

**所以「不要重启」不是保守建议，是延长寿命的直接手段。**

> ⚠️ **[更正] 但别把这张表读成「不重启 = 不坏」。**
> 错误数停住是**缓存效应**（块组校验失败一次就被标记，后续不再重复校验）。
> 真正在盖文件的动作是**合法写入**，内核不报错。
> 实测：这 9.7 小时的「安静期」里**仍有文件被改写**，见 §2.12。
> → 「不重启」能**减少**损坏机会，**不能消除**它。**该停还是得停。**

### 伤害链（与 9/18 报告一致）

```
① 控制器电压/时序配置拿不到（regulator 缺、pin bias 缺）
        ↓
② 高速写路径不可靠（9/18 实测：写错误 60 行 / 读错误 0 行）
        ↓
③ ext4 元数据被写坏（ext4_validate_block_bitmap 校验和错）
        ↓
④ 块位图一坏，分配器就把**已占用**的块当空闲发出去
        ↓
⑤ 写新文件时覆盖别人的数据 → 这就是 python3 / git / at-spi2 那些库的死因
   （**[更正]** `vmlinuz` 不在其中 —— 它是假阳性，见 §2.6）
```

第 ④⑤ 步解释了那个最反直觉的现象：
**「写 200MB 读回来一致」能过，因为被吃掉的是别的文件。**

### 卡在什么状态（9/18 实测，本次未变）

```
/sys/kernel/debug/mmc1/ios
  clock:           50000000 Hz
  signal voltage:  3.30 V
  timing spec:     2 (sd high-speed)
  bus width:       4 bits
```

卡跑在 **3.3V / 50MHz / SD high-speed** —— 标准可靠档位。
`vqmmc` 缺失 → 切不到 1.8V → 退回 3.3V。
**问题不在「跑得快不快」，在「这条写路径本身不可靠」。**

> ⚠️ 需要克制的地方：`No vmmc/vqmmc/vd33sw/vd18sw/vq33sw/vq18sw regulator found`、
> `manual set ocr`、`Cann't get pin bias hs pinstate` 这几条，
> **在 Allwinner 板子上是常见告警**（很多板子就是固定 3.3V，本来就不做 1.8V 切换）。
> 把它们当成「设备树配错了」的铁证是**过度解读**。
> 它们只能作为「写路径可疑」的**旁证**，不能单独定案。

---

## 四、板型：软件侧证据一致指向 **A7A**（但仍建议看丝印）

| 来源 | 值 |
|---|---|
| `/proc/device-tree/model` | `sun60iw2` |
| `/sys/firmware/devicetree/base/compatible` | `radxa,cubie-a7a` `radxa,a733` `arm,sun60iw2p1` `allwinner,sun60i-a733` |
| 内核启动日志 | `Machine model: sun60iw2` |
| `hostname` | `radxa-cubie-a7a` |
| USB 设备 | `AIC 8800D80`（AIC8800 USB WiFi，A7A 的标配） |
| 网口 `end0` | `DRIVER=dwmac-sunxi` / `allwinner,sunxi-gmac-210` / `snps,dwmac-5.20` |
| 内存 | 4005032 kB ≈ 4 GiB |
| U-Boot 版本 | `2026.04-3-boot-dlan17-g24da4dae7637-dirty` |
| journal 里所有 `Radxa A7` 命中 | 全是用户自己的服务描述 `Lain42 compute market agent (Radxa A7A)` |

**关于 9/18 记的那条「U-Boot 报 `Model: Radxa A7S`」：**
本次把 `journalctl --since 2026-09-17` 全查了一遍，**没有任何 A7S 的命中**。
9/18 那两条很可能是**我自己的 grep 命令被 sudo 记进了日志**（这次也踩过一次，见 §六）。

**其他已排除的线索：**

- SPI NOR（`/dev/mtd0`，8 MB）里 **没有** `fdtfile`、也没有 `cubie-a7` 字符串
- `/config/config.txt` 只有 `rsetup` 的模板注释
- `/boot/efi/ubootefi.var` 是 UEFI 变量存储（`UbEfiVa` 头 + `Boot0000` / `BootOrder`）
- `/boot/extlinux/extlinux.conf` 里只有 `fdtdir`（**目录**），没有 `fdtfile`：

```
label l0
	menu label Debian GNU/Linux 13 (trixie) 6.6.98-4-aw2511
	linux /boot/vmlinuz-6.6.98-4-aw2511
	initrd /boot/initrd.img-6.6.98-4-aw2511
	fdtdir /usr/lib/linux-image-6.6.98-4-aw2511/
	append root=UUID=ce788441-061f-4c4b-a90b-feabdcd8790c … mac_addr=${mac} mac1_addr=${mac1} …
```

**⚠️ 一个附带发现**：`/proc/cmdline` 里 `mac_addr=` 和 `mac1_addr=` 都是**空的** ——
说明 U-Boot 环境里 `mac` / `mac1` 变量没有值，`${mac}` 占位符被替换成了空串。
这不是存储损坏，但意味着**网卡 MAC 每次开机可能是随机的**，值得单独处理。

**要定案还是得看丝印**（或串口抓 U-Boot 开头的 `Model:` 行）——
因为 dtb 是 U-Boot 选的，选错的话它会「自证」成 A7A。

---

## 五、已完成的抢救：5 个包

### 方法：**一个字节都不落到坏盘上**

- `/tmp` 是 **tmpfs（内存盘，2.0 G）** → 打包写在 `/tmp` 完全不碰坏盘
- 拉取走 paramiko 的 **raw 通道**（`rsh.py` 按 `utf-8 decode(errors="replace")`，二进制过一趟就废）

### 包清单

| 包 | 大小 | md5 | 内容 |
|---|---|---|---|
| `rescue1.tgz` | 438,733,124 | `fc47a7556fdb9e5623f77ec6c914e5ec` | `/etc` `/root` `/home/radxa`（排除 `.cache`、`models/*.gguf`、`sau/.venv`） |
| `rescue2.tgz` | 34,280,062 | `647983bc76429c25469292b458bbe276` | `/usr/local` `/var/log` `/var/spool` |
| `rescue3.tgz` | 58,861,593 | `60422070c6caa84a25e8edfcaa0f8065` | **`/boot`** `/config` `/usr/lib/linux-image-6.6.98-4-aw2511` |
| `rescue4.tgz` | 391,980 | `1f4700b1358d344410503ec89a932fae` | **`/var/backups`**（含损坏前的 dpkg 数据库） |
| `rescue5.tgz` | 72,095,773 | `123c8a969259e4d8a440c6c2ffe9d159` | **`/var/cache/apt/archives`（93 个 deb）** + `/var/lib/dpkg/info` |

存放位置：`D:\Code\radxa\a7a-rescue-2026-09-28\`（含 `MD5SUMS.txt`）

**校验方式（两边独立算 md5，不是「传完就信」）：**
板子上 `tar czf … ; md5sum` → 本机 `md5sum` 比对 → 逐包 `gzip -t` → `tar tzf | wc -l`。
**五项全部一致**（5147 / 95 / 95 / 16 / 3489 条目，零读失败）。

### rescue3 补的是之前漏掉的一整块

`/boot` 在根分区 p3 上（§2.2），里面有：

```
boot/extlinux/extlinux.conf            ← 改 fdtfile 要用的原文
boot/extlinux/extlinux.conf.bak        ← 真的坏了（1361 字节全 NUL），但只是 .bak，不影响启动
boot/vmlinuz-6.6.98-4-aw2511           ← ✅ 完好的那份（未压缩 Image）★
boot/initrd.img-6.6.98-4-aw2511
boot/dtbo/*.dtbo.disabled              ← Radxa 设备树 overlay
usr/lib/linux-image-6.6.98-4-aw2511/allwinner/sun60i-a733-cubie-{a7a,a7s,a7z}.dtb
config/config.txt                      ← p1 分区
boot/efi/ubootefi.var                  ← p2 分区
```

> 🔴 **[更正] 首次成文写的「rescue3 里的 `vmlinuz` 是坏的那一份，不能当恢复源」—— 完全说反了。**
> rescue3 里那份 `vmlinuz` **就是好的**（未压缩 arm64 Image，md5 `f7c1fcbe…`），
> 它与官方 6.6.98-4 deb 里的内核解压后**逐字节相同**（§2.6）。
> **它反而是本次唯一「已知完好且已在手」的内核副本** —— 但**不需要用它做任何事**，
> 因为板子上那份本来就是好的。

### rescue4 / rescue5 的价值

- **`rescue4`** 里的 `/var/backups/dpkg.status.1.gz`（169,777 B，`09-22 05:02`）
  **gzip 完整、解出 749 个 `Package:` 条目** → 这是**损坏前的权威包数据库**
  （含每个包的精确版本号）。已导出成可读清单：
  `D:\Code\radxa\a7a-rescue-2026-09-28\packages-2026-09-22.txt`
  （对照：`dpkg.status.0`（09-27 14:37 那份）712,228 字节**全是 NUL**）
- **`rescue5`** 里的 `/var/lib/dpkg/info/`（3392 个文件）**价值极高** ——
  它是「哪些文件属于哪个包」的权威依据。
  **[更正]** 但同一包里的 **93 个 deb 只剩 26 个能用**（74% 魔数已坏，见 §2.11），
  **不能拿它当离线修复源**；恢复内容已改为从官方源重下（见 §七 与 repair-plan §4.1）。

### 特意排除的（都可重建）

`/home/radxa/.cache` 1.3 G、`models/*.gguf` 1.4 G、`sau/.venv`、
`mihomo-linux-arm64`(57 M) / `.gz`(20 M)、`ghboost-aarch64`(7 M)、`Country.mmdb`(8.5 M)。
`/var/lib/apt/lists`（98 M）也没拿 —— 需要时 `apt update` 就能重建。

### 清点结论

`/opt` 不存在；`/srv` 空（只有 `local-apt-repository` 的空索引）；
`/home` 只有 `radxa`(3.8 G) 和 `rock`(20 K，空)；
`/var` 剩余大头是 `/var/log`(122 M，已拿) 和 `/var/lib`(127 M，dpkg info 已拿)。
**没有遗漏的自装内容。**

---

## 六、这次我犯的错 / 被自检抓出的 bug

### 错误 1：拿 `asdfg` 判「假卡」（**第二次犯**）

`cat /sys/block/mmcblk1/device/name` = `asdfg`，我当场说「白牌/假卡的典型特征」。
**这正是 9/18 报告里明确记下来的错误。** 用户当时纠正过：卡面有 `A2` + `U3` 标记。

`asdfg` 是**读卡器/卡不报告 CID 产品名**时的占位值。
**教训：单一来源的可疑字段不足以定案，必须交叉验证。**
（已同步修掉 `sbc-storage-corruption-diagnosis` skill 里那条错误判据。）

### 错误 2：拿 initrd 的「头 4 字节」判它坏了

`head -c 4` 得到 `30 37 30 37`（ASCII `0707`），我按旧判据判它损坏，
还准备据此下「重启必砖」的结论。**错。**

Debian 的 initrd 是「**未压缩 cpio + zstd**」拼接格式：
- `070701` 是 cpio `newc` 的**正常魔数**
- `zstd -t` 报 `unsupported format` 是**必然的**（文件开头不是 zstd）
- **正确判据**：`lsinitramfs <initrd> | wc -l`。本次实测 `rc=0`、1326 条、stderr 零行
  → **initrd 完好**
- **[更正] 强判据（本轮补上）**：`unmkinitramfs` 把 initrd 解出来（953 个文件 / 124 个模块），
  与磁盘上 `/lib/modules/6.6.98-4-aw2511/` 里的同名模块**逐个 md5 比对** ——
  **124 一致 / 0 不一致**。首次成文说「没有 md5 级证据」，现在有了。见 §2.13。

### 错误 3：拿「超长行 + ERE 二次方退化」解释卡住（**推断错了**）

`probe6` 卡了 17 分钟，我推断是 `grep -E '^[0-9a-f]{32}  /[!-~]+$'` 在超长行上退化。
**错。** 实测 `exp.txt` 最长行只有 **485 字节**。

**真凶是 `xargs`**（见判据 11）。而且那条 grep 之所以「没输出」，
是**另一个完全无关的原因**：locale（见判据 10）。**两个坑叠在一起，我猜了第三个。**

### 自检抓出的 3 个脚本 bug（`--selftest` 的价值）

写 `dpkg-verify-shim.sh` 时，`--selftest` 连续抓出 3 个我自己看不出来的问题：

1. **`"$0"` 是相对路径** → 递归调用报 `command not found`。改成绝对路径。
2. **过滤写在「补 `/` 前缀之后」** → `$2 ~ /^\//` 恒真，**等于没过滤**。
   必须在补前缀**之前**判断。
3. **`md5sum` 在 Git Bash 下默认二进制模式** → 输出 `<hash> *<path>`（星号），
   而 Linux 是 `<hash>  <path>`（两空格）→ 全表误判为坏。
   修法：`sed 's/^\([0-9a-f]\{32\}\) [ *]/\1  /'` 归一化。

> **教训：判据类脚本必须带 `--selftest`，而且要「造一个已知坏、一个已知好」两边都断言。**
> 只看「能不能跑出结果」是不够的 —— 上面 2 和 3 都会「跑出结果」，只是全错。

### 错误 4：一次成文下了**四条错结论**（本轮最大的教训）

首次成文（09-28 白天）时，我基于当时的证据写了四条结论，**晚间二次取证时全部被推翻**：

| # | 首次成文的错结论 | 真相 | 错在哪 |
|---|---|---|---|
| 1 | 「文件系统已损坏，**但已停止扩散**」 | **仍在低频偶发改写** | 把「EXT4 错误数不涨」当成了「没在坏」。**EXT4 不为块覆盖报错** |
| 2 | 「**内核已经坏了**，`vmlinuz` 中后部损坏」 | **内核完好**，是形态转换假阳性 | 看到 md5 不符就下结论，**没做「解压后再比」** |
| 3 | 「上电前**必须**先换 6.6.98-5」 | **什么都不用做** | 建立在前一条错结论上，**错误会传递** |
| 4 | 「226 个文件损坏、33 个包」 | **207 真损坏、27 个包** | 没排除 `md5sums` 比对的 5 类**系统性假阳性** |

**共同根因：把「某个自动化工具报的不一致」直接当成了事实，没有追问「这个不一致有没有别的解释」。**

- md5 不符 → 有没有可能是「文件被合法重写过」？（内核解压 / depmod / 本地改配置 / 共享路径）
- 错误数不涨 → 有没有可能是「这类损坏本来就不报错」？
- 一个数字（226）→ 有没有可能它包含了**工具自身的 bug**（路径截断）造成的假阳性？

> **新规矩：任何「N 个东西坏了」的结论，先做三件事 ——
> ① 抽 5 个样本看**字节级证据**；② 按**已知的合法改写类别**筛一遍；
> ③ 问一句「如果这个数字是错的，最可能是哪种系统性偏差」。**

---

## 七、下一步

**详细的、可执行的分步方案写在同目录 `a7a-2026-09-28-repair-plan.md`。**

核心要点（**[更正] 三条硬约束已按本轮结论重写**）：

1. **先停写** —— 断电拔卡，**立刻做**。损坏仍在低频发生（§2.12），
   再等就是在赌「下一个被盖的不是关键文件」。
2. **`e2fsck` 必须做，但它只止血** —— 修完文件系统结构后，**207 个文件仍然是坏的**，
   必须走「拷回正确内容」这一步（repair-plan 阶段 C）。
3. **内核什么都不用做** **[更正]** —— 启动链整体完好（§2.13），
   **不需要换 6.6.98-5，也不需要去找 6.6.98-4 的原版包**。
   唯一要注意的是：**别把假阳性里的那个 `vmlinuz` 拷回去**（载荷包已排除）。
4. **`/var/lib/dpkg/status` 从 `rescue4` 的 `dpkg.status.1.gz` 恢复** ——
   恢复后 dpkg/apt 就活了，然后 207 个文件里的 `md5sums`/`list` 配套信息也就位了。
   **[更正]** 不能再指望「用 apt 缓存里的 deb 离线修」—— 那批 deb 74% 已坏（§2.11），
   正确做法是用已经准备好的 `restore-payload.tar`（207 个文件，全部 md5 核对通过）。

**⚠️ 必须一起处理的根因线**（不处理，修完还会再坏）：

- ☐ 把周期 fsck 打开：`tune2fs -c 20 -i 7d /dev/mmcblk1p3`
- ☐ `/etc/fstab` 根分区加 `errors=remount-ro`（现在是裸 `defaults` → `errors=continue`）
- ☐ `journald` 改 `Storage=volatile` + 给 `radxa-net-ensure` 加 `LogLevelMax`
- ☐ **禁掉 `fstrim.timer`**（§2.10：对 vfat 分区会整盘 discard）
- ☐ 供电：换品牌氮化镓（5V/3A 以上）+ 好线，**只试一次**
- ☐ 确认板型丝印 → 必要时在 `extlinux.conf` 里显式写 `fdtfile`
- ☐ **考虑把根分区搬到 USB SSD**（`docs/a7a-migrate-to-usb-ssd.md` 已有现成方案）
  —— 这是唯一能绕开「SD 控制器写路径不可靠」这个根因的办法

---

## 八、判据更新（写给未来的自己）

| # | 新判据 | 来源 |
|---|---|---|
| 1 | **`asdfg` 不是假卡证据。** 它是读卡器/卡不报 CID 产品名时的占位值，必须交叉验证 | 9/18 已记，本次**我又踩了一次** |
| 2 | **判 initrd 好坏看 `lsinitramfs`，不看头 4 字节。** Debian initrd 以 cpio `070701` 开头是正常的，`zstd -t` 报错也是正常的 | 本次 |
| 3 | **`dpkg -V` 输出 0 行可能是假绿。** 先看它有没有报 `parsing file '/var/lib/dpkg/status'` —— dpkg 数据库自己坏了就会静默返回空 | 本次 |
| 4 | **「报错」本身在制造写入。** 排查时要找「周期性写入源」，不只是找「周期性报错源」 | 本次 |
| 5 | **`/tmp` 是 tmpfs 的话，抢救包写在 `/tmp` 完全不碰坏盘** | 本次 |
| 6 | **传输工具的编码会毁掉二进制。** `rsh.py` 按 `utf-8 decode(errors="replace")`，tar 过一趟就废 | 本次 |
| 7 | **护栏的误报比漏报更伤。** 已修 + 加了回归测试 `_selftest_rsh_guard.py` | 本次 |
| 8 | **单测能抓出「读代码看不出来」的漏报。** `systemctl poweroff` 根本没被拦（`reboot` 前面是 `systemctl`） | 本次 |
| 9 | **`e2fsck` 只修文件系统结构，不修文件内容。** 「跑完 e2fsck 就好了」是错觉 —— 被覆盖的文件仍然坏着，必须 `reinstall` 或重刷 | 本次 |
| 10 | **非 C locale 下 `[!-~]` 这类范围类按 collation 解释，不按字节序。** 实测同一条记录 `en_US.UTF-8` 下匹配 **0** 次、`C` 下匹配 **1** 次。**所有文本处理都要 `LC_ALL=C`**（`sort`、`comm`、`grep`、`join` 一个都不能漏 —— 我漏了 `comm`，结果拿到一份不可信的数字） | 本次 |
| 11 | **`xargs` 不带 `-r`，输入为空时会执行一次不带参数的命令。** 若该命令读 stdin（`md5sum`/`cat`/`wc`），在 SSH 会话里会**永久挂死**。现场表现：「脚本卡住、输出文件 0 字节、`ps` 里看不出谁在等」。实测 `timeout 5` → RC=124，加 `-r` → RC=0 | 本次 |
| 12 | **`md5sum` 在 MSYS/Git Bash 下默认二进制模式**，输出 `<hash> *<path>`；Linux 是 `<hash>  <path>`。跨平台比对前必须归一化 | 本次 |
| 13 | **`dpkg -V` 挂了就用 `*.md5sums` 绕过**（它不依赖 `status`）。但**数据库自身可能也是受害者**，必须先按格式过滤掉垃圾行 —— 且过滤要在「补 `/` 前缀之前」做 | 本次 |
| 14 | **比对结果要自证。** 超过 1/3 不一致就先怀疑数据库坏了，而不是「机器坏成这样了」。脚本应主动提示 | 本次 |
| 15 | **「文件头 4 字节坏了」是 ELF 可执行文件死掉的直接原因**，症状是 `syntax error` / `Exec format error` —— **看起来完全不像存储损坏**。反之，**头正常不代表文件好**（损坏位置随机，可能在中后部）。⚠️ **但「头好但 md5 不对」必须先排除 §2.7 的 5 类假阳性** —— 首次成文举的 `vmlinuz` 例子后来证明是假阳性 | 本次（已更正） |
| 16 | **全 NUL 文件是「刚写下去、没落盘」的指纹**；随机字节是「块被别人的数据覆盖」的指纹。看 mtime 能定位到出事的写操作窗口 | 本次 |
| 17 | **`grep` 卡住时不要只怀疑「输入太大」**。本次真正的原因是 locale 导致 0 匹配 + `xargs` 空输入挂死，**两个无关的坑叠在一起** | 本次 |
| 18 | **「文件内容变了但 ctime/mtime/size 一个字节都没动」= 块被别的文件覆盖。** 这是块位图损坏的**独有指纹**，也是唯一可靠的现场判据 | 本轮二次取证 |
| 19 | **EXT4 错误计数不涨 ≠ 损坏已停止。** 覆盖是「合法写入」，内核不报错。**测「还在不在坏」必须用「重复快照 + 比 md5」**，不能看 `journalctl \| grep -i ext4` | 本轮二次取证（推翻首次成文） |
| 20 | **`md5sums` / `dpkg -V` 的「不一致」有 5 类系统性假阳性**：形态转换 / 共享路径 / 本地合法改写 / depmod 再生 / 路径截断。**尤其 `/boot/vmlinuz-*` 在 Debian 系上几乎必然是假阳性**（postinst 会解压内核） | 本轮二次取证 |
| 21 | **判「内核能不能用」不要比 md5**，要比**解压后**的内容：`gzip -dc <deb 内 vmlinuz> \| cmp - <板子上的 vmlinuz>` | 本轮二次取证 |
| 22 | **判 initrd 好坏用 `unmkinitramfs`，不要用 `zcat`**（Debian initrd 是「未压缩 cpio + zstd」拼接）。**最强校验：把 initrd 里的 `.ko.xz` 与磁盘上同名模块逐个比 md5** | 本轮二次取证 |
| 23 | **`md5sums` 里的路径可能带空格**，`awk '{print $2}'` 会截断 → 产生一批「文件不存在」的假阳性。取整行用 `sed 's/^[0-9a-f]\{32\}[ \t]*//'` | 本轮二次取证 |
| 24 | **发行版旧版本的包不会消失。** `radxa-repo.github.io` 是 GitHub Pages，包实体在 `radxa-pkg/<pkg>` 的 **GitHub Release** 里，旧 tag 长期保留；下架只是从 `Packages` 索引移除 | 本轮二次取证 |
| 25 | **apt 缓存不可信。** 块覆盖保留文件大小，`ls -la` 看不出来。批量验真的快办法：`head -c 8 f.deb` 必须等于 `!<arch>`。本次实测 93 个 deb 里 69 个已坏（74%） | 本轮二次取证 |
| 26 | **任何「N 个东西坏了」的结论，先做三件事**：① 抽 5 个看**字节级证据**；② 按**已知的合法改写类别**筛一遍；③ 问「如果这个数字是错的，最可能是哪种系统性偏差」。**本次一次成文下了 4 条错结论，全部源于跳过这三步** | 本轮二次取证 |

---

## 九、工具与产物清单

### 抢救产物

| 产物 | 位置 |
|---|---|
| `rescue1..5.tgz` + `MD5SUMS.txt` | `D:\Code\radxa\a7a-rescue-2026-09-28\` |
| 损坏前的包清单（749 个包 + 版本号） | 同目录 `packages-2026-09-22.txt` |
| **226 条**不一致清单（板子上） | `/tmp/dpkg-bad-files.txt`（md5 `3b7d3865d361b59d2e11f1a91ca0cb80`） |

### 恢复材料（**[新增] 本轮为「阶段 C」准备好的**）

| 产物 | 说明 |
|---|---|
| `restore-payload.tar` | **155,750,400 字节 / 207 个文件**，保留 deb 里的 mode/uid/gid/mtime。抽取时逐个核对 md5 == dpkg 期望值，**207/207 通过；回读校验 207 文件 / 0 不符** |
| `restore-manifest.json` | `summary{flagged:226, damaged:207, false_positive:19}` + 19 个假阳性及原因 + 207 个文件清单（path/md5/owner/src_deb） |
| `restore-debs/` | **33 个官方 deb（110 MB）**，每个都过 **SHA256**（Debian 官方索引里的值） |
| `restore-payload/` | 解包副本（225 个文件 / 161 MB），供人工核对 |
| `_DO-NOT-APPLY/` | **绝不能拷回板子**的 `vmlinuz`（假阳性那一份）+ `README.txt` 说明 |
| `kernel-6.6.98-4/`、`kernel-6.6.98-5/` | 备用内核包（**本次用不上**，留档） |
| `apply-restore.sh` | 落地脚本：带 `--check` 干跑、`--root <挂载点>`；只拷内容不一致的，先写 `.tmp-restore` 再 `mv`；**明确不含 `vmlinuz`** |

**来源与可信度链**：26 个包从 **Debian 官方源**按 `Filename`+`SHA256` 下载；
3 个 radxa 包从 **`radxa-pkg` 的 GitHub Release（tag `0.7.3`）**；
1 个内核包从 `radxa-pkg/linux-aw2511` 的 `6.6.98-4` release。
**交叉验证闭合**：官方 `at-spi2-common_…deb` 里的 `.mo` md5 == 板子 `md5sums` 里的期望值
→ 同时证明「md5sums 数据库可信」与「官方 deb 就是正确内容」。

### 工具（`radxa_utlra/recovery/tools/`）

| 工具 | 说明 |
|---|---|
| `pull_board.py` | **二进制安全**的板子文件拉取器（paramiko raw 通道）。`rsh.py` 会毁掉二进制，必须用这个 |
| `rsh.py` | 最小 SSH 命令执行器（只跑命令，不往板子写东西）。本次修了护栏的引号误报 + 重启漏报 |
| `_selftest_rsh_guard.py` | 护栏回归测试（**27 拦 / 10 放全绿**） |
| `dpkg-verify-shim.sh` | 在 `dpkg -V` 跑不了的机器上，用 `*.md5sums` 反查内容损坏。带 `--selftest`（本次转绿：`SELFTEST PASS`，抓出坏文件 1 个 / 未误报好文件 / 2 行垃圾已过滤） |
| `sd_offline_repair.ps1` | Windows 侧离线修卡（`-Check` 零风险 / `-Repair` 七步 / `-PatchBoot`） |

### 本次原始取证输出

`D:\tmp\board_health*.out`、`scope.out`、`rescue*.out`、
`probe_now.out`、`probe2..19.out`、`probe10.out`、`pe_scan.py`；
**[新增]** `finddeb.py`（解析 Packages 索引，29/29 命中）、`fetch_restore.py`（下载+校验+抽取）、
`bad_fixed.json` / `badpaths_full.txt`（227 条修正后记录）、`board_sizes.txt`、`badclass.txt`。

### 板子上留下的（tmpfs，掉电即失）

`/tmp/wl.txt`（214 条工作清单）、`/tmp/snap1..4.txt`（四次快照，md5 均为 `b625bfe4…`）、
`/tmp/ir.txt`、`/tmp/irx2/`（initrd 解包）。

# 换源与安全升级（Radxa / Debian trixie）

> 场景：板子刚重刷，官方源（`deb.debian.org`）慢，想换国内源并升级系统包。
> **难点不是换源，是"升级里有没有会碰 bootargs 的包"。**
> 本文记录一套可复现、且经过实测验证的流程。
>
> 实测日期：2026-09-19 · 板子：Radxa Cubie A7A（A733）· Debian 13 trixie

---

## 一、先说 rsetup 的真实形态

很多人以为 `rsetup` 是个能脚本化的 CLI 工具。**不是。**

`/usr/bin/rsetup` 只有 868 字节，逻辑是：

```sh
if (( $# == 0 )) && [[ "$RSETUP_EXEC_NAME" == "rsetup" ]]
then
    ...
    tui_start __tui_main "RSETUP"        # 无参数 → 开 TUI
else
    __parameter_type_check "$1" "function"
    "$@"                                  # 有参数 → 调用同名 shell 函数
fi
```

看起来「有参数就调函数」，但**换源那几个函数在 CLI 上下文里根本不存在**：

```
$ rsetup change_sources_list_deb_urls
'main' expects 'change_sources_list_deb_urls' type to be 'function', but it is ''.
```

原因：`change_sources.sh` 只被 **TUI 的** `tui/system/system.sh` source，
而 `cli/main.sh` 的 source 列表里没有它。

> **结论：rsetup 的换源本质上是交互式的，没法直接非交互调用。**

---

## 二、非交互复用 rsetup 的实现

不要自己手写 `sed` 去改 `sources.list` —— 用 rsetup 自己的实现，
它的镜像列表和替换逻辑是官方维护的：

```bash
# 换源模块在 /usr/lib/rsetup/mod/change_sources.sh
# 它依赖 __in_array（来自 librtui），所以要先把两个都 source 进来
bash -c '
  source /usr/lib/librtui/utils/utils.sh
  source /usr/lib/rsetup/mod/change_sources.sh
  change_sources_set_deb_mirror https://mirrors.ustc.edu.cn
'
```

可用函数：

| 函数 | 作用 |
|---|---|
| `change_sources_list_deb_urls` | 列出 rsetup 支持的 Debian 镜像 |
| `change_sources_set_deb_mirror <url>` | 换 Debian/Ubuntu 源 |
| `change_sources_set_radxa_mirror <url>` | 换 Radxa 源 |
| `change_sources_restore` | 还原官方源 |

**行为要点**：它 sed 替换 `/etc/apt/sources.list` 和 `sources.list.d/*.list` 里
所有已知镜像，但**会跳过 `*radxa*.list`** —— Radxa 专属仓库（`radxa-repo.github.io`）
不在替换范围内，这是对的。

**换源前先备份**：

```bash
BK=/root/apt-sources-backup-$(date +%Y%m%d-%H%M%S)
cp -a /etc/apt/sources.list.d "$BK"
```

---

## 三、选哪个源：实测，不要猜

rsetup 内置这些镜像：

```
https://mirrors.ustc.edu.cn          中科大
https://mirrors.tuna.tsinghua.edu.cn 清华
https://mirrors.cqu.edu.cn           重庆大学
https://mirrors.lzu.edu.cn           兰州大学
https://mirrors.hust.edu.cn          华中科技
https://mirrors.sdu.edu.cn           山东大学
https://mirror.nju.edu.cn            南京大学
https://mirror.nyist.edu.cn          南阳理工
```

**别按名气选，在板子上实测**：

```bash
for m in mirrors.ustc.edu.cn mirrors.tuna.tsinghua.edu.cn mirrors.cqu.edu.cn \
         mirrors.lzu.edu.cn mirrors.hust.edu.cn mirrors.sdu.edu.cn \
         mirror.nju.edu.cn mirror.nyist.edu.cn; do
  curl -s -o /dev/null -w "%{time_total}s %{http_code}  $m\n" \
    --max-time 8 "https://$m/debian/dists/trixie/Release"
done
# 官方源对照
curl -s -o /dev/null -w "%{time_total}s %{http_code}  deb.debian.org\n" \
  --max-time 15 "https://deb.debian.org/debian/dists/trixie/Release"
```

2026-09-19 实测结果：

| 镜像 | 耗时 | |
|---|---|---|
| **mirrors.ustc.edu.cn** | **0.126 s** | ← 选中 |
| mirrors.tuna.tsinghua.edu.cn | 0.152 s | |
| mirror.nju.edu.cn | 0.170 s | |
| mirrors.hust.edu.cn | 0.261 s | |
| mirror.nyist.edu.cn | 0.274 s | |
| mirrors.lzu.edu.cn | 0.311 s | |
| mirrors.sdu.edu.cn | 0.330 s | |
| **mirrors.cqu.edu.cn** | **8 s 超时** | ✗ 不通 |
| **deb.debian.org**（原用） | **1.184 s** | 慢 9.4 倍 |

换源后 `apt update`：**17.8 MB / 6 秒（3.2 MB/s）**。

> **注意**：某个镜像不通（如这次的 cqu）不代表它坏，可能只是临时抖动或本地路由问题。
> 每次换源都重测一遍，不要照抄别人的结论。

---

## 四、⚠️ 升级前的排雷（本文最重要的一节）

**升级包里凡是名字带 `cmdline` / `boot` / `u-boot` / `kernel` 的，先读 postinst 再决定。**

本项目的硬约束是「不动 U-Boot、不改 bootargs」。而升级列表里出现了：

```
radxa-system-config-kernel-cmdline-ttyas0   0.7.3 → 0.7.4
```

名字直指「改内核命令行」。**不要因为怕就不升，也不要闭眼升 —— 去读它的 postinst：**

```bash
cat /var/lib/dpkg/info/radxa-system-config-kernel-cmdline-ttyas0.postinst
```

内容：

```sh
if [ ! -f /etc/kernel/cmdline ]     # ← 只在文件不存在时才写
then
    install -m 644 /usr/share/radxa-system-config/cmdline /etc/kernel/cmdline
    u-boot-update
fi
```

`/etc/kernel/cmdline` **已经存在** → postinst **什么都不做** → **升级是安全的。**

**事后必须验证地雷没被引爆**：

```bash
cat /etc/kernel/cmdline
stat -c '%y  %n' /etc/kernel/cmdline /boot/extlinux/extlinux.conf
uname -r
```

实测结果（确认未触碰）：

| 项 | 值 |
|---|---|
| `/etc/kernel/cmdline` | 内容一致，mtime 仍是 `2026-08-04 07:45:14`（镜像构建时间） |
| `/boot/extlinux/extlinux.conf` | mtime 同样 `2026-08-04 07:45:14`，**未被触碰** |
| 内核 | `6.6.98-4-aw2511` 未变 |

> **如果 postinst 里没有 `if [ ! -f ... ]` 这个守卫，就必须 `apt-mark hold` 住这个包**，
> 或者用 `apt-get upgrade` 前先 `dpkg --set-selections` 排除它。
> 判据是**读脚本**，不是看包名。

### 顺带：确认没有内核/u-boot/initramfs 升级

```bash
apt list --upgradable 2>/dev/null | grep -iE 'linux-image|linux-header|u-boot|kernel|initramfs'
```

有内核升级就意味着**必须重启才能生效** —— 而在写路径可疑的卡上，
重启本身是高风险操作（见 `a7a-healthy-boot-log-analysis.md` 第七节）。

---

## 五、执行升级

```bash
DEBIAN_FRONTEND=noninteractive \
  apt-get upgrade -y -o Dpkg::Options::=--force-confold
```

| 选项 | 为什么 |
|---|---|
| `apt-get upgrade`（不是 `full-upgrade`） | 不删包。`full-upgrade` 可能为了解依赖而移除包 |
| `-o Dpkg::Options::=--force-confold` | 配置冲突时**保留现有配置**，不用新版覆盖 |
| `DEBIAN_FRONTEND=noninteractive` | 避免卡在交互式提问上 |

实测：93 个包，耗时 **3 分 49 秒**，exit 0。

---

## 六、升级后验证（必做）

**最关键的指标是 `Block count` —— 它必须和升级前一致。**

```bash
# 失败单元
systemctl --failed --no-legend --no-pager
systemctl is-system-running

# 文件系统健康 —— 重点看 Block count 有没有变
sudo dumpe2fs -h /dev/mmcblk1p3 | grep -E 'Filesystem state|Block count|Mount count'

# 存储错误（滤掉空插槽噪音）
sudo dmesg | grep -iE 'blk_update_request|I/O error|remounting filesystem read-only|bad block bitmap|Aborting journal|ext4-fs error' | wc -l

# 剩余可升级
apt list --upgradable 2>/dev/null | grep -c upgradable
```

2026-09-19 实测（全部通过）：

| 指标 | 结果 |
|---|---|
| 剩余可升级 | **0** |
| 失败单元 | **0** |
| `is-system-running` | **running** |
| 文件系统状态 | `clean` |
| **`Block count`** | **`16299259` —— 未变** ✅ |
| 存储错误 | **0** |

> **为什么盯 `Block count`**：
> 它是判断「这批写入有没有损坏元数据」的最直接证据。
> **块数不变 = 几何结构完好**；块数增长或缩小 = 数据区在恶化，要立刻停手排查。
> 本项目上一次故障就是靠这个指标区分出「只是校验和坏了（fsck 可修）」
> 与「数据区在烂（只能重刷）」的。

---

## 七、收尾：有一批服务不会立刻用上新二进制

升级后 `needrestart` 会列出：

```
Services to be restarted:
 systemctl restart avahi-daemon.service
 systemctl restart haveged.service
 ...

Service restarts being deferred:
 systemctl restart NetworkManager.service
 systemctl restart bluetooth.service
 systemctl restart systemd-logind.service
 systemctl restart wpa_supplicant.service
 ...
```

**deferred 那批不要手动重启**：

- 重启 `systemd-logind` / `dbus` 会**打断当前 SSH 会话**
- 重启 `NetworkManager` / `wpa_supplicant` 会**直接断网**

它们会在下次自然重启时生效。**没必要为此专门重启一次板子。**

---

## 八、一句话清单

```text
1. 备份 sources.list.d
2. source rsetup 的 change_sources.sh → change_sources_set_deb_mirror
3. apt update
4. ⚠️ 排雷：apt list --upgradable | grep -iE 'cmdline|boot|u-boot|kernel'
        → 有命中就 cat 它的 postinst 看有没有守卫
5. apt-get upgrade -y -o Dpkg::Options::=--force-confold
6. 验证：Block count 未变 + 0 失败单元 + 0 存储错误
7. 不要重启；deferred 的服务留给下次自然重启
```

---

## 相关文档

- [一次健康启动日志的完整解读](./a7a-healthy-boot-log-analysis.md) —— 含"重启为什么会烧卡"
- [反复烧卡根因排查报告](./a7a-sd-card-corruption.md)
- [重建手册](./a7a-rebuild-manual.md)

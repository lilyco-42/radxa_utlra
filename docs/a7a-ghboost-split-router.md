# A7A × ghboost：AP 软路由 + 智能分流实施规划

> 目标：在 Radxa Cubie A7A 上开 WiFi 热点，让所有连上热点的设备
> **像用软路由一样自动分流** —— 国内流量直连、GitHub/Google/YouTube 走加速，
> 全网设备无需任何本机配置。
>
> 规划日期：2026-09-23 · 依赖：[a7a-router-mode.md](a7a-router-mode.md)（已实测打通）· ghboost v0.3.14

## 一、现状盘点（读完 radxa_utlra + ghboost 两个仓库后的事实）

| 资产 | 来源 | 状态 |
|---|---|---|
| PPPoE + WiFi 热点 + NAT 端到端实测 | `docs/a7a-router-mode.md` | ✅ 2026-09-14 打通，**运行时配置重启即丢** |
| 板载 AIC8800 支持 AP 模式 | 同上（`iw phy` 含 `AP`） | ✅ NM 一条命令开热点，不需 hostapd |
| 千兆口 tx-delay 必须 9 | 同上（出厂 12 丢 42% 大帧） | ✅ 已有 NM dispatcher 持久化脚本 |
| 板上已在跑 `mihomo + nginx + avahi` | radxa-monitor SSH 快检项 | ℹ️ 板子本身已是代理节点/状态页服务器 |
| 局域网可发现（mDNS `_http._tcp`） | radxa-monitor | ✅ 便于手机端发现状态页 |
| ghboost aarch64 Linux CLI | ghboost Release v0.3.14 | ✅ `ghboost-aarch64-unknown-linux-gnu`(+musl) |
| ghboost 四命令：scan/test/add/boost | `ghboost --help` | ✅ 节点获取→测速→导出 + GitHub hosts 加速 |
| ghboost mihomo 配置生成 | `src/mihomo.rs::generate_config` | ⚠️ 默认 `allow-lan:false`、规则仅 `GEOIP,CN,DIRECT` + `MATCH` |
| ghboost `find_binary()` | `src/mihomo.rs:126` | ❌ 只认 Windows（`where`/`mihomo.exe`/`C:\...`），Linux 需显式 `binary_path` 或改代码 |
| 板子准入 | LAN 扫描 | 🔒 `192.168.10.165`（Debian 13 trixie，疑为板子）SSH 拒绝现有两把钥匙，**待提供密码/授权** |

## 二、总体架构

```
                       ┌────────────────────────── Radxa A7A ──────────────────────────┐
 互联网                 │                                                              │
   │                   │  end0 ──WAN──▶ ppp0(方案A: PPPoE) 或 DHCP(方案B: 接现有光猫)   │
   │                   │                     │                                        │
   │                   │   ┌─────────────────┴──────────────────┐                     │
   │                   │   │  mihomo 内核（分流中枢）              │                     │
   │                   │   │  mixed-port 7890  allow-lan: true   │                     │
   │                   │   │  规则: GEOIP,CN→DIRECT               │                     │
   │                   │   │        github/google/youtube→加速组   │                     │
   │                   │   │        MATCH→节点选择                 │                     │
   │                   │   └─────────────────┬──────────────────┘                     │
   │                   │                     │ nftables: wlan0 的 TCP ──REDIRECT──▶7890│
   │                   │  wlan0 AP 10.42.0.1/24 ── dnsmasq(NM自带) ──DNS──▶ mihomo:1053│
   │                   │        │  (ghboost boost 写 hosts 播给全网)                    │
   │                   └────────┼────────────────────────────────────────────────────┘
   │                            │ SSID: Radxa-AP / ch6
   ▼                            ▼
 WiFi 客户端（手机/笔记本/电视）—— 零配置，连上即自动智能分流
```

### 三层分流（为什么"智能"）

| 层 | 手段 | 效果 |
|---|---|---|
| **DNS 层** | 客户端 DNS→10.42.0.1(dnsmasq)→127.0.0.1#1053(mihomo, fake-ip) | 域名级判别：国内域名拿真 IP 直连，境外域名拿 fake-ip 进代理路径 |
| **TCP 层** | nftables：`wlan0` 非本机 TCP 全部 `REDIRECT → 7890` | 客户端零配置透明代理，mihomo 按规则决定 DIRECT 还是走节点 |
| **hosts 层** | `ghboost boost` 优选 GitHub IP 写板级 hosts，dnsmasq 读 `/etc/hosts` | 不走代理也能加速 `github.com/raw/assets` 解析（配合 GEOIP 分流直连时） |

> UDP/QUIC 第一阶段放行直连（浏览器 QUIC 被墙时自动回落 TCP→被劫持进代理），
> 后续需要再上 mihomo TUN 或 nftables TPROXY 补全 UDP 分流（见 §7 可选项）。

### 上联拓扑：两个方案

| | 方案 A：主路由（仓库已实测） | 方案 B：旁路由/二级路由（推荐起步） |
|---|---|---|
| end0 | 入户线直插，PPPoE 拨号 | 网线接现有光猫/路由器，DHCP 拿 192.168.10.x |
| 依赖 | 宽带账号密码、tx-delay=9 必修 | 仅 tx-delay=9 必修，不动现有网络 |
| 影响 | 现有路由器下线，全家切 A7A | **零中断**，原 WiFi 照常，连新 SSID 才走分流 |
| 建议 | 长期目标 | **先跑通 B，验收后再切 A** |

下文步骤对 A/B 通用，差异只在 Phase 1 的 WAN 获取方式。

## 三、分阶段实施

### Phase 0：板子准入与体检（阻塞项，~10 分钟）

```bash
# 0.1 拿到访问权（当前 192.168.10.165 拒绝 lain42.pem / id_ed25519）
ssh root@192.168.10.165            # 需要板子密码，或把钥匙装上去：
# 在能登录的终端执行：
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo '<你的公钥>' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys

# 0.2 体检（仓库自带，只读不改）
sudo ./scripts/deploy-router.sh --check

# 0.3 确认既有服务与硬件事实
systemctl status mihomo nginx avahi-daemon     # 板上已有的三个服务
iw phy | grep -A3 "Supported interface modes"  # 确认 AP
ls /sys/class/net/end0/device/tx_delay && cat /sys/class/net/end0/device/tx_delay   # 期望 9
env | grep -i proxy                            # 清掉残留代理变量再测试
```

### Phase 1：恢复软路由底座（复用已验证脚本）

```bash
cd radxa_utlra && git pull
cp router-config.example.toml router-config.toml   # 填宽带账号(方案A)/WiFi 密码
sudo python3 scripts/deploy-router-from-toml.py --config router-config.toml --dry-run
sudo python3 scripts/deploy-router-from-toml.py --config router-config.toml

# tx-delay 持久化（必做，重启回 12 会复发"假坏网线"）
sudo install -m 0755 scripts/a7a-txdelay /etc/NetworkManager/dispatcher.d/90-a7a-txdelay
```

验收：手机连 `Radxa-AP` → 拿到 `10.42.0.x` → `curl baidu` 200。
**此时能上网但还没有分流**（NAT 直出）。

> 记住 §a7a-router-mode.md 的坑 2：NM 不会自动加 NAT，脚本 `--nat` 步骤不能漏；
> 以及所有这些运行时配置重启即丢，Phase 4 统一做持久化。

### Phase 2：ghboost + mihomo 常驻（分流中枢）

```bash
# 2.1 装 ghboost CLI（Release aarch64 gnu 版，Debian 13 直接跑）
curl -Lo /usr/local/bin/ghboost \
  https://github.com/lilyco-42/ghboost/releases/latest/download/ghboost-aarch64-unknown-linux-gnu
chmod +x /usr/local/bin/ghboost && ghboost --help

# 2.2 节点供给（三选一）
ghboost scan --max-sources 100 --concurrency 32   # 扫公开节点
ghboost test --top 300 --timeout-ms 8000          # 真实延迟测试
ghboost add --keep 20 --apply                      # 导出最优 20 个为订阅
# 或：贴已有订阅 / lain42.top 节点订阅服务卡密

# 2.3 GitHub hosts 加速（板级，dnsmasq 会把 /etc/hosts 播给全网）
ghboost boost && cat /etc/hosts | grep -A2 ghboost

# 2.4 mihomo 常驻服务（aarch64 内核 + 手写 config.yaml，绕开 ghboost 的
#     Windows-only find_binary；allow-lan 是本方案的命门）
install -m 0755 <mihomo-aarch64> /usr/local/bin/mihomo
mkdir -p /etc/mihomo
cat > /etc/mihomo/config.yaml <<'YAML'
mixed-port: 7890
allow-lan: true
bind-address: '*'
mode: rule
log-level: info
external-controller: 127.0.0.1:9091
dns:
  enable: true
  listen: 0.0.0.0:1053        # 与 ghboost 默认不同：给 dnsmasq 上游用，必须 listen
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  nameserver: [https://dns.alidns.com/dns-query, https://doh.pub/dns-query]
  fallback: [https://1.1.1.1/dns-query, https://dns.google/dns-query]
  fallback-filter: {geoip: true, geoip-code: CN}
proxies: []                   # 由 ghboost add 导出的订阅注入
proxy-groups:
  - {name: "🚀 节点选择", type: select, proxies: [DIRECT]}
rules:
  - GEOSITE,github,DIRECT     # 配合 ghboost hosts 优选直连；不稳则改代理组
  - GEOIP,CN,DIRECT
  - MATCH,🚀 节点选择
YAML

cat > /etc/systemd/system/mihomo.service <<'UNIT'
[Unit]
Description=mihomo (ghboost split-router core)
After=network-online.target NetworkManager.service
Wants=network-online.target
[Service]
ExecStart=/usr/local/bin/mihomo -d /etc/mihomo
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now mihomo
```

验收：`curl -x http://127.0.0.1:7890 http://cp.cloudflare.com` 在板上通；
`external-controller` 能列出节点与连接。

### Phase 3：透明分流接线（AP 全网生效）

```bash
# 3.1 DNS：热点 dnsmasq 上游改指 mihomo（NM 自带实例，加 conf.d 片段）
cat > /etc/NetworkManager/dnsmasq.d/ghboost-split.conf <<'CONF'
server=127.0.0.1#1053
no-resolv
CONF
nmcli connection reload && nmcli connection down radxa-ap && nmcli connection up radxa-ap

# 3.2 TCP 透明劫持：wlan0 上非本机流量 REDIRECT 到 mixed-port
cat > /etc/nftables.d/ghboost-split.conf <<'NFT'
table inet ghboost_split {
  chain prerouting {
    type nat hook prerouting priority dstnat; policy accept;
    # 排除发给路由器自身的目的地（否则板上 SSH/DNS 被劫走）
    iifname "wlan0" meta l4proto tcp fib daddr type != local redirect to :7890
    # 排除内网互访
    iifname "wlan0" ip daddr 10.42.0.0/24 accept
  }
}
NFT
# Debian: apt install nftables && systemctl enable --now nftables（include /etc/nftables.d/）

# 3.3 NAT 前置确认（router-mode 的 MASQUERADE 是分流的出口，缺了必上不了网）
nft list ruleset | grep -i masquerade || echo "先跑 Phase 1 的 --nat"
```

验收（**在连着 Radxa-AP 的手机上**，零配置）：

```bash
ping 10.42.0.1                      # 网关通
curl baidu.com -sI -o /dev/null -w '%{http_code}\n'     # 200 → 国内直连
curl https://api.ipify.org           # 出口 IP = 方案A: ppp0 公网 / 方案B: 家里宽带
# 浏览器开 github.com / google.com / youtube.com → 秒开 → 代理组生效
# 板上 mihomo 面板看连接：github 的连接走节点、baidu 的连接 DIRECT
```

### Phase 4：持久化与开机自启

| 组件 | 持久化方式 |
|---|---|
| tx_delay=9 | NM dispatcher `90-a7a-txdelay`（**不能**用开机单元，载波初始化会覆盖） |
| PPPoE/AP/NAT | `deploy-router-from-toml.py` 写的 NM connection + nftables 规则存 `/etc/nftables.conf` |
| mihomo | systemd `enable`（Phase 2 已含） |
| ghboost hosts | `ghboost boost` 结果写在 `/etc/hosts`（天然持久），可加 cron 每日刷新优选 |
| 分流 nftables | nftables.service include |
| 验证 | `reboot` 后重复 Phase 3 验收清单，全过才算完 |

## 四、ghboost 代码侧待办（让"板子上跑 ghboost"名副其实）

现状是 **mihomo 干分流、ghboost 干节点和 hosts**，ghboost 自己的
`MihomoManager` 在 Linux 上还有三个 gap，建议修完再把服务管理收编进 ghboost：

1. **`find_binary()` Windows-only**（`src/mihomo.rs:126`）：用 `where`、找 `mihomo.exe`、
   查 `C:\Program Files` —— Linux 上除 `binary_path` 外全部落空。
   → 加 `which` 分支 + `/usr/local/bin/mihomo`、`/usr/bin/mihomo` 常见路径。
2. **订阅/节点配置硬编码 `allow-lan: false`**（`src/nodes.rs:814`、`src/web.rs:780`）：
   软路由场景必须 `true`，否则 WAN 侧规则全灭。
   → 接上 `MihomoConfig.allow_lan` 已有字段，提供 server profile。
3. **`generate_config` 规则太薄**（仅 `GEOIP,CN` + `MATCH`）：
   → 增加 GEOSITE github/google/youtube/youtube 预置组，与 README 的
   "一键加速 Google/YouTube/GitHub"定位对齐；`dns.listen` 按需开放（现有注释已给出
   特权端口教训，服务化时用 1053 高位端口）。

> 顺带：`MihomoConfig` 注释与实际默认值不一致（注释说 mixed 端口默认 7890/面板 9090，
> 实际 `mixed_port: 9090`、`api_port: 9091`），修的时候一起校正。

## 五、风险与已知坑（全部来自仓库实测记录）

1. **tx-delay=12 是隐形杀手** —— 不改，方案 A 的 PPPoE 必挂；方案 B 大包也丢 42%。
2. **NM 不自动加 NAT** —— 热点能连、能 ping 网关、**上不了网**，最常见踩空。
3. **残留代理环境变量** —— `HTTPS_PROXY=127.0.0.1:1080` 让板上 curl 秒失败(000)，
   误判"网络不通"。测试前 `env | grep -i proxy`。
4. **WiFi 走 USB 2.0 总线**（aic8800 挂 480M 口）—— AP 吞吐有天花板，分流延迟
   增量（mihomo 转发）要算进预算；CPU 侧 8 核 A733 转发 7890 无压力。
5. **单网口** —— 方案 A 下 end0 被 PPPoE 独占，下游只能 WiFi（或加 USB 网卡）。
6. **运行时配置重启即丢** —— 不做 Phase 4 = 每次重启手动重来。
7. **fake-ip 与 UDP** —— 第一阶段 UDP 不分流，QUIC 场景可能出现"慢但能用"，
   属预期行为；要补全见 §7。
8. **STA+AP 同频共存未测** —— 本方案 end0 走有线、wlan0 纯 AP，刻意避开该风险。

## 六、里程碑

- [ ] **M0** 板子准入（.165 密码/公钥）+ `--check` 体检通过
- [ ] **M1** 热点+NAT 恢复，手机直连可上网（复用实测脚本）
- [ ] **M2** mihomo 常驻 + ghboost 节点/hosts 就位，板上 `-x 7890` 可翻
- [ ] **M3** DNS+TCP 透明分流接线，手机零配置自动分流（本规划核心验收）
- [ ] **M4** 全组件持久化，`reboot` 后回归通过
- [ ] **M5** ghboost 三处 Linux gap 修复合入 main（CI 已全绿可直接发版）

## 七、可选进阶

- **UDP/QUIC 完整分流**：mihomo `tun: {enable: true, stack: gvisor, auto-route: true}`
  或 nftables `tproxy`（需 `IP_TRANSPARENT`，复杂度高一档）。先跑通 TCP 再上。
- **切换方案 A 主路由**：B 验收通过后，把入户线从光猫移到 end0 走 PPPoE，
  `deploy-router-from-toml.py` 一键完成，全家所有设备（含原光猫 WiFi）都过 A7A 分流。
- **状态页联动**：板上 nginx 状态页 + mihomo `external-controller:9091` →
  手机 radxa-monitor 一键看当前分流连接数。

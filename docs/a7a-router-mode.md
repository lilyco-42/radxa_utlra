# A7A 当路由器：PPPoE 拨号 + WiFi 热点 + NAT 转发

把 Radxa Cubie A7A（Allwinner A733）变成一台**能用的路由器** —— 入户网线插网口拨号，板载 WiFi 开热点，NAT 转发给下游设备。

> 验证环境：Radxa Cubie A7A / Debian 13 trixie / 内核 `6.6.98-4-aw2511` / 8 核 4G
> 实测日期：2026-09-14

## 一句话结论

| 环节 | 实测结果 |
|---|---|
| PPPoE 拨号 | ✅ 拿到 `100.75.18.191/32`（CGNAT），CHAP `Authentication success,Welcome!` |
| 公网出口 | ✅ `36.33.45.68`（联通安徽），另有 IPv6 `2408:8244:b00:516f::/64` |
| WiFi 热点 | ✅ SSID 正常广播，ch6 / 2437MHz / 20MHz，`type AP` |
| DHCP | ✅ dnsmasq 自动发 `10.42.0.10-254` |
| NAT | ✅ 下游设备出口 IP 与板子自身直连**完全一致** |
| **真实客户端验证** | ✅ ping 网关 4ms、ping 223.5.5.5 40ms、`curl baidu` **200 / 58ms** |

硬件底子够，但有一个**必须修的前置**（见第一节）。只有 1 个网口，所以下游只能走 WiFi。

## 快速开始

```bash
git clone https://github.com/lilyco-42/radxa_utlra.git
cd radxa_utlra

# 1. 体检：看网口/WiFi/驱动/模块现状，不做任何改动
sudo ./scripts/deploy-router.sh --check

# 2. 推荐：复制 TOML 配置，在本机填写账号、密码和 WiFi 参数
cp router-config.example.toml router-config.toml
# 编辑 router-config.toml；含真实密码的文件不要提交到 GitHub
sudo python3 scripts/deploy-router-from-toml.py --config router-config.toml --dry-run
sudo python3 scripts/deploy-router-from-toml.py --config router-config.toml
```

TOML 一键配置会依次完成：PPPoE 拨号、wlan0 热点、DHCP、IP 转发和 NAT。

也可以继续使用分步命令：

```bash
sudo ./scripts/deploy-router.sh --pppoe --user 宽带账号 --pass 密码
sudo ./scripts/deploy-router.sh --ap --ssid MyAP --ap-pass 12345678
sudo ./scripts/deploy-router.sh --nat
```

---

## 一、前置：必须先把千兆网口的 tx-delay 改掉

**这是最容易被忽略、也最致命的一步。**

Radxa 官方镜像（含 `6.6.98-4-aw2511`）把 RGMII **tx-delay 设成厂商值 `12`**，而这个值下**发帧是损坏的** —— 实测 1200 字节帧丢 42%。症状非常像「坏网线」：

- 链路状态正常（1000/full、`LOWER_UP`）
- `tx_errors` 计数为 **0**
- 短 ping 正常
- **但 PPPoE 连发现阶段都过不去**，SSH 密钥交换后卡死，`apt` 卡住

正解是 `9`（`9/10/11` 都行）：

```bash
echo 9 | sudo tee /sys/class/net/end0/device/tx_delay
```

⚠️ **数据相关**：重复字节负载 0 丢包，随机数据才暴露 —— 别用 `ping -s` 或重复字节自测，会得到假结论。

> 本项目实测：改 `9` 之前 PPPoE 完全不通；改完**一次拨通**。

## 二、PPPoE 拨号

### 2.1 装 pppd

Debian 里插件叫 **`pppoe.so`**，不是 `rp-pppoe.so`：

```bash
sudo apt install ppp
ls /usr/lib/pppd/*/pppoe.so
```

### 2.2 内核模块

```bash
sudo modprobe pppoe       # 会自动带上 ppp_generic + pppox
```

`/dev/ppp` 报 `No such device or address` 就是这一步没做。

### 2.3 释放网口（+ 视情况克隆 MAC）

```bash
sudo nmcli device set end0 managed no             # 让 NetworkManager 松手
```

**MAC 要不要克隆，取决于你这条线路绑不绑 MAC。** 实测我们这条线路**不绑** ——
用一个明显没登记过的 MAC 也照样拨通（详见[第七节](#七mac-克隆到底需不需要2026-09-14-实测)）。
所以先按默认来，**拨不通再试克隆**：

```bash
sudo ip link set end0 address B0:25:AA:7F:0C:2D   # 换成局端放行的 MAC
```

### 2.4 拨号

```bash
sudo pppd plugin pppoe.so end0 \
     user 宽带账号 password 密码 \
     mtu 1480 mru 1480 noauth defaultroute usepeerdns \
     logfile /var/log/ppp.log &
```

- `mtu/mru 1480`：PPPoE 头占 8 字节，再留余量更稳
- `usepeerdns`：自动把运营商 DNS 写进 `/etc/resolv.conf`
- `defaultroute`：自动加默认路由

看结果：

```bash
ip -4 -br addr show ppp0        # 期望拿到 100.x.x.x/32 或公网段
tail -40 /var/log/ppp.log       # 找 "CHAP authentication succeeded"
```

## 三、WiFi 热点

板载 AIC8800 驱动声明支持 AP 模式（`iw phy` 的 `Supported interface modes` 里含 `AP`）。用 NetworkManager 一条命令搞定：

```bash
sudo nmcli device wifi hotspot ifname wlan0 con-name radxa-ap \
     ssid MyAP band bg channel 6 password 12345678
```

NM 会自动配好：

- `wlan0 = 10.42.0.1/24`
- dnsmasq 实例，`--dhcp-range=10.42.0.10,10.42.0.254`

**不需要单独装 hostapd**（NM 自带 AP 能力）。

## 四、NAT 转发 ← 最容易踩空的一步

**NetworkManager 不会自动加 NAT 规则。** 在 Debian 13 / NM 1.52 上 `nft list ruleset` 是空的，`iptables -t nat -S` 里也**没有** `10.42.0.0/24` 的 MASQUERADE。

结果就是：客户端能连上热点、能拿到 IP、能 ping 通网关，**但上不了网**。

手动补上：

```bash
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A POSTROUTING -s 10.42.0.0/24 -o ppp0 -j MASQUERADE
sudo iptables -A FORWARD -i wlan0 -o ppp0 -j ACCEPT
sudo iptables -A FORWARD -i ppp0 -o wlan0 -m state --state RELATED,ESTABLISHED -j ACCEPT
```

## 五、验证

在**另一台设备**上（手机 / 笔记本）连上热点，然后：

```bash
ip a                                           # 应拿到 10.42.0.x
ping 10.42.0.1                                 # 网关，期望 <10ms
ping 223.5.5.5                                 # 出公网
curl -s http://members.3322.org/dyndns/getip   # 出口 IP
```

**判据**：出口 IP 应该和板子自己 `curl` 出来的一致 —— 说明流量真的走了 A7A 的 `ppp0`。

> 本项目实测：客户端 ping 网关 4ms、ping 223.5.5.5 40ms、`curl baidu` 200 / 58ms，出口 IP 与板子直连完全一致。

## 六、踩过的坑

1. **板子残留代理环境变量**：`HTTPS_PROXY=http://127.0.0.1:1080` 之类的残留会让 `curl` **全部返回 `000` 且耗时 0.0003s**（秒失败），极易误判成「网络不通」。先 `env | grep -i proxy` 看一眼。
2. **NM 不自动加 NAT**（见第四节）—— 这是最容易漏的一步。
3. **tx-delay 不改，PPPoE 永远不通**（见第一节）。
4. **PPPoE 会独占物理网口**，所以「拨号 + 热点」这条路**不需要**第二个网口。
5. `hostapd` 没装也没关系，NM 自带 AP 能力。

## 七、MAC 克隆到底需不需要？（2026-09-14 实测）

很多教程（包括本文早期版本）都会说「局端按 MAC 放行，必须克隆 MAC」。
**这不一定成立** —— 我们做了一次干净的对照实验：**只改 WAN MAC，其他参数一律不动**。

| WAN MAC | 结果 |
|---|---|
| `b0:25:aa:7f:0c:2d`（电脑有线网卡） | ✅ 拨通 `100.75.18.191/32` |
| `02:00:00:00:00:01`（本地管理地址，明显未登记） | ✅ **照样拨通** `100.75.73.160/32`，CHAP 一样 success |

**结论**：我们这条线路的局端**不做 MAC 绑定**，任意 MAC 都能拨通。

**怎么判断你这条线路绑不绑** —— 就用上面这个实验，换一个明显没登记过的 MAC 拨一次：

- **拨通了** → 不绑，`--mac` 参数可以不传
- **拨不通** → 绑了，老老实实克隆已登记的 MAC，或者打客服要求解绑

> ⚠️ 换 MAC 拨号前，**先停掉其他用同一账号拨号的设备**（否则会互相踢），
> 不然结论不可信。

同理，「拨前静置 ~70s」也不是铁律 —— 实测停 pppd 后静置 **20~45 秒**再拨就能一次通过。

---

## 八、当前限制

- ⚠️ **全是运行时配置**：`tx_delay`、`pppd`、NM hotspot、iptables 规则 —— **重启全丢**，要长期用需自己做持久化（systemd unit / NM connection / `iptables-persistent`）。
- **只有 1 个网口**：入户线占了 `end0`，下游只能走 WiFi（或加 USB 千兆网卡）。
- **WiFi 走 USB 2.0**：`aic8800_fdrv` 挂在 480M 端口上（`lsusb -t` 可验），WiFi 6 的速率优势被总线卡死。
- 未测：AP 实际吞吐、STA+AP 同频共存、长时间稳定性、多客户端。

## 九、为什么不用 OpenWrt

OpenWrt 有 A733 正式 target（`chainsx/openwrt-sunxi-aiot`，基线 24.10.3），但：

- **没有预编译镜像**，必须自建
- AIC8800 驱动要自己塞进 target
- target 很新，社区验证少

想快速见效，Debian + 本文这套组合更省事；要长期稳定跑路由器，再考虑 OpenWrt 路线。

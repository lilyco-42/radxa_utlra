# Radxa Cubie A7A Debian 13 刷卡教程

## 结论

绿灯常亮只代表电源正常，不代表系统已经启动。Cubie A7A 的蓝灯才是状态灯：

- 蓝灯闪烁：系统正常启动
- 蓝灯熄灭：启动或内核出错

如果之前局域网扫不到 A7A，最常见原因是 **SD 卡里没有系统**。本文介绍在 Windows 下把官方 Debian 13 (trixie) 镜像写入 SD 卡。

## 1. 确认 SD 卡盘符

以管理员身份打开 PowerShell，执行：

```powershell
Get-Disk
Get-Partition
Get-Volume
```

识别依据：

- 系统盘通常是 NVMe / SSD，容量最大
- 读卡器会显示为 `USB` / `Removable`
- 本次环境中的 SD 卡是 `Disk 2`，62.5GB，USB

刷写前必须确认目标盘号，避免误刷 C/D 系统盘。

## 2. 下载镜像

官方 A733 统一镜像发布页：

```text
https://github.com/radxa-build/radxa-a733/releases
```

Debian 13 (trixie) 目前为官方测试版，标准 SD/eMMC 镜像：

```text
https://github.com/radxa-build/radxa-a733/releases/download/rsdk-t5/radxa-a733_trixie_kde_t5.output_512.img.xz
```

如果测试版不稳定，可先刷官方稳定 Debian 11：

```text
https://github.com/radxa-build/radxa-a733/releases/download/rsdk-r6/radxa-a733_bullseye_kde_r6.output_512.img.xz
```

下载后核对 SHA-512：

```powershell
Get-FileHash .\radxa-a733_trixie_kde_t5.output_512.img.xz -Algorithm SHA512
```

trixie KDE t5 镜像校验值：

```text
082090701529c56b4db90770f6df7724b84e9304faa3498259d426b6266a0764e4c7fbb863a367509c6a2765c7913e4b05ade2424cbe2dc77de94768e99f2f2a
```

## 3. Windows 刷写方法

### 方法 A：图形工具（推荐新手）

1. 下载并安装 [balenaEtcher](https://etcher.balena.io/) 或 Rufus
2. 选择下载好的 `.img.xz` 镜像
3. 选择 SD 卡（再次核对盘符/容量）
4. 点击 Flash

### 方法 B：WSL + dd（命令行）

需要 WSL2 和 Ubuntu，且需要管理员权限。

以管理员身份打开 PowerShell，先挂载 SD 卡为裸设备：

```powershell
wsl.exe --mount \\.\PHYSICALDRIVE2 --bare
```

然后在 WSL 中查看新出现的设备：

```bash
lsblk
```

假设设备是 `/dev/sde`，流式解压并写入：

```bash
sudo xzcat /mnt/c/Users/<你的用户名>/Downloads/radxa-a733_trixie_kde_t5.output_512.img.xz | sudo dd of=/dev/sde bs=4M status=progress conv=fsync
```

刷写完成后卸载：

```powershell
wsl.exe --unmount \\.\PHYSICALDRIVE2
```

## 4. 启动 A7A

1. 把 SD 卡插入 Cubie A7A 的 microSD 卡槽
2. 接上网线
3. 使用 5V Type-C 电源供电
4. 观察蓝灯：正常启动应闪烁

## 5. 查找 A7A 的 IP

### 从路由器后台查看

小米路由器后台通常为：

```text
http://192.168.10.1
```

登录后在设备列表中找到 `radxa` / `cubie` 主机名。

### 从本机扫描

```powershell
.\scripts\radxa-ctl.ps1 -ScanLan
```

或直接扫描本网段：

```powershell
1..254 | ForEach-Object -Parallel {
  $ip = "192.168.10.$_"
  if (Test-Connection $ip -Count 1 -Quiet) { $ip }
} -ThrottleLimit 64
```

### 通过域名解析公网地址

```powershell
Resolve-DnsName lain42.top
```

## 6. SSH 登录

```powershell
.\scripts\radxa-ctl.ps1
```

或用系统自带 SSH：

```powershell
ssh -i $HOME\.ssh\lain42.pem root@<A7A-IP>
```

如果 SSH 连不上，先确认：

```bash
ping <A7A-IP>
ssh root@<A7A-IP>
sudo systemctl status ssh
```

## 7. 常见问题

### 蓝灯不亮

- 确认供电是 5V Type-C
- 确认 SD 卡已插紧
- 重新用 Etcher 刷一遍镜像
- 接 USB-TTL 串口看启动日志，A7A 的 UART 在 40-pin GPIO 上

### 局域网仍扫不到

- 确认网线两端灯亮
- 换一根网线或路由器端口
- 登录路由器后台看 DHCP 客户端列表
- 如果 A7A 没拿到 IP，说明系统仍未正常启动

### 只有绿灯常亮

绿灯只代表供电。请检查蓝灯是否闪烁；如果蓝灯熄灭，按“蓝灯不亮”处理。

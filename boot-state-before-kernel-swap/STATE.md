# 动内核前系统状态快照（GitHub 备份）

> 用途：在把 Radxa `6.6.98-4-aw2511` 换成 Rabs9 自定义 `6.6.98-a7a` 内核（修 NPU 量化通道）之前，
> 把当前启动配置固化进仓库。这是"工作成果备份"，**不能还原 SD 卡本身**——若 SD 写损坏仍需重刷。
> 真正的安全网是：dpkg 装新内核**不会删旧内核**，extlinux 保留本文件的 `6.6.98-4-aw2511` 启动项可回退。

## 抓取时间
2026-09-19

## uname
```
Linux radxa-cubie-a7a 6.6.98-4-aw2511 #4 SMP Tue Aug  4 04:21:09 UTC 2026 aarch64 GNU/Linux
```

## 已安装内核相关包
```
linux-headers-6.6.98-4-aw2511   6.6.98-4
linux-headers-radxa-a733        6.6.98-4
linux-image-6.6.98-4-aw2511     6.6.98-4
linux-image-radxa-a733          6.6.98-4
```

## /boot 关键文件
- vmlinuz-6.6.98-4-aw2511
- initrd.img-6.6.98-4-aw2511
- System.map-6.6.98-4-aw2511
- config-6.6.98-4-aw2511
- DTB 目录：`fdtdir /usr/lib/linux-image-6.6.98-4-aw2511/`

## 根分区
```
/dev/mmcblk1p3   62G  3.3G   56G   6%   /
```

## 计划中的变更（Rabs9 debs-20260825）
- `linux-image-6.6.98-a7a_6.6.98-5_arm64.deb`
- `linux-dtb-6.6.98-a7a_6.6.98-5_arm64.deb`
- `linux-headers-6.6.98-a7a_6.6.98-5_arm64.deb`（headers 另有 6.6.98-6 修复版）
- 安装后 extlinux 应新增 `6.6.98-a7a` 启动项，旧 `6.6.98-4-aw2511` 保留。

## ⚠️ 风险提示
1. **Rabs9 内核是超频内核**（A76 3.0GHz / A55 2.8GHz），官方要求**必须散热风扇**，否则有烧板风险。
2. 本机**无 USB SSD 备份**（用户选择用 GitHub 仓库备份代替），SD 卡写路径有已知缺陷；写内核 + 重启是高风险操作。
3. 回退方式：重启在 extlinux 菜单选 `6.6.98-4-aw2511` 旧项；或 `dpkg -r linux-image-6.6.98-a7a linux-dtb-6.6.98-a7a`。

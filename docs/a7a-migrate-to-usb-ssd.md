# A7A SD 卡 → USB SSD 迁移手册

> 背景：当前 SD 卡(`mmcblk1`)写路径有已知硬件缺陷，换内核后已触发 `ext4lazyinit` 坏位图(`bg 112/240/368/497`)。虽然 FS 仍 `rw`、系统仍可用，但**不能再重启、不能再写 SD 卡**。
>
> 本手册提供两条迁移路线：
> - **方案 A（推荐，零风险）**：SSD 当数据盘，SD 继续负责启动，写密集型操作落 SSD
> - **方案 B（彻底）**：完整系统迁到 SSD，改从 SSD 启动（需 U-Boot 支持 USB boot）

---

## 前置条件

- USB SSD（SATA/NVMe 均可，推荐 ≥64GB）
- A7A 板子当前在线、SSH 可达（不要重启 SD 卡）
- 本手册命令在板端执行（通过 SSH 或串口）

---

## 方案 A：温和迁移（SSD 当数据盘，SD 只读启动）

**目标**：SD 卡不再承受写负载，延长寿命；系统仍从 SD 启动，但 `/home`、`/var/log`、`/tmp` 等大写量目录实际落在 SSD。

### A1. 接 SSD 并识别

```bash
# 板端执行
lsblk -d -o NAME,TRAN,MODEL,SIZE
# 预期看到类似 sda / nvme0n1，确认 SSD 被识别
dmesg | tail -20 | grep -iE 'usb|scsi|nvme|sda'
```

### A2. 分区 + 格式化（**在 SSD 上做，不碰 SD**）

```bash
# 假设 SSD 是 /dev/sda（根据 lsblk 结果调整）
SSD=/dev/sda

# 清掉旧分区表（若有）
sudo parted -s $SSD mklabel gpt

# 创建一个大的 ext4 分区（整块盘）
sudo parted -s $SSD mkpart primary ext4 1MiB 100%
sudo mkfs.ext4 -F -L a7a-data ${SSD}1

# 获取 UUID（后面 fstab 用）
UUID=$(sudo blkid -s UUID -o value ${SSD}1)
echo "SSD UUID = $UUID"
```

### A3. 挂载并创建目录结构

```bash
sudo mkdir -p /mnt/ssd
sudo mount UUID=$UUID /mnt/ssd
sudo mkdir -p /mnt/ssd/home /mnt/ssd/var/log /mnt/ssd/var/tmp /mnt/ssd/tmp
sudo chmod 1777 /mnt/ssd/tmp
```

### A4. 把现有数据同步到 SSD（**rsync，不是 dd**）

```bash
# 同步 /home/radxa（含 npu_zoo、kws-min、vpm-min 等）
sudo rsync -aHx --progress /home/radxa/ /mnt/ssd/home/radxa/

# 同步日志（可选，减少 SD 写）
sudo rsync -aHx --progress /var/log/ /mnt/ssd/var/log/

# 同步 vp-pipeline 输出目录（若已配置）
# sudo rsync -aHx --progress /home/radxa/vp-output/ /mnt/ssd/vp-output/
```

### A5. 用 bind mount 把写操作重定向到 SSD（**不重启，立即生效**）

```bash
# 先备份现有 /home/radxa（万一要回退）
sudo mv /home/radxa /home/radxa.sd.bak
sudo mkdir /home/radxa
sudo mount --bind /mnt/ssd/home/radxa /home/radxa

# 同样处理 /var/log
sudo mount --bind /mnt/ssd/var/log /var/log

# 验证
df -h | grep ssd
ls -la /home/radxa
```

### A6. 固化到 fstab（**只写 SSD 上的 fstab，不碰 SD 根**）

> 注意：不要把 SSD bind mount 直接写进 SD 卡的 `/etc/fstab`——那样 SD 卡重启时若 SSD 没就绪会起不来。改用 systemd `.mount` unit 或开机脚本延迟挂载。

更简单的方式：写一个 systemd mount unit，让 `local-fs.target` 在 SSD 就绪后再 bind mount：

```bash
sudo tee /etc/systemd/system/home-radxa.mount <<'EOF'
[Unit]
Description=Bind mount /home/radxa to SSD
After=local-fs.target

[Mount]
What=/mnt/ssd/home/radxa
Where=/home/radxa
Type=none
Options=bind

[Install]
WantedBy=local-fs.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now home-radxa.mount
```

### A7. 把 tmpfs 也用起来（进一步减少 SD 写）

```bash
# 确认 /tmp 已经是 tmpfs
mount | grep ' /tmp '
# 若否，加一行到 /etc/fstab（tmpfs 不写 SD）：
# tmpfs /tmp tmpfs defaults,nosuid,nodev,size=512M 0 0
```

### A8. 验证 NPU 仍在正常工作

```bash
cd /home/radxa/kws-min
LD_LIBRARY_PATH=/home/radxa/kws-min/lib ./vpm_run -s s_join.txt -l 1 2>&1 | grep -E 'vpm run ret|profile inference'
# 预期：vpm run ret=0，inference 约 90us
```

### 方案 A 完成 ✅

- SD 卡不再承受 `/home` 和 `/var/log` 的写负载
- 系统仍从 SD 启动，但启动后写操作全落 SSD
- **不需要重启、不需要改 U-Boot**
- 若 SSD 掉线，系统仍能从 SD 的备份 `/home/radxa.sd.bak` 恢复

---

## 方案 B：完全迁移（系统盘迁到 SSD，从 SSD 启动）

> ⚠️ **风险更高**：需要改 U-Boot 启动顺序、需要一次重启验证。若 U-Boot 不支持 USB boot，会起不来。
> 建议先做完方案 A 保数据，再择机尝试方案 B。

### B1–B4 同方案 A（分区、格式化、rsync 同步）

额外同步 `/boot`：

```bash
sudo mkdir -p /mnt/ssd/boot
cp -a /boot/* /mnt/ssd/boot/
```

### B5. 更新 SSD 上的 extlinux.conf

```bash
# 编辑 SSD 上的 boot 配置
sudo nano /mnt/ssd/boot/extlinux/extlinux.conf
```

把 `root=` 指向 SSD 分区 UUID：

```ini
timeout 30
default l1

label l1
	menu label A7A on SSD (6.6.98+)
	linux /vmlinuz-6.6.98+
	initrd /initrd.img-6.6.98+
	fdtdir /dtbs/6.6.98+/
	append root=UUID=<SSD_ROOT_UUID> rw rootfstype=ext4 console=ttyS0,115200 earlycon=uart,mmio32,0x02500000

label l0
	menu label A7A on SD (fallback)
	linux /vmlinuz-6.6.98-4-aw2511
	initrd /initrd.img-6.6.98-4-aw2511
	fdtdir /dtbs/
	append root=UUID=<SD_ROOT_UUID> rw rootfstype=ext4 console=ttyS0,115200 earlycon=uart,mmio32,0x02500000
```

### B6. 更新 SSD 上的 /etc/fstab

```bash
sudo nano /mnt/ssd/etc/fstab
```

确保根分区指向 SSD UUID，保留 SD 卡 UUID 作为注释：

```
UUID=<SSD_ROOT_UUID>  /  ext4  defaults,noatime  0 1
# SD 卡备份（应急挂载）
# UUID=<SD_ROOT_UUID>  /mnt/sd-backup  ext4  defaults,noatime  0 0
```

### B7. 串口接入，改 U-Boot env（**需要 saveenv，用户禁止 saveenv**）

⚠️ **此步违反用户硬约束"不动 U-Boot/saveenv"**。

若你的 U-Boot 支持 USB boot，可以在 U-Boot 提示符临时改（**不 saveenv**）：

```
setenv bootcmd "usb start; ext4load usb 0:1 ${kernel_addr_r} /vmlinuz-6.6.98+; ext4load usb 0:1 ${fdt_addr_r} /dtbs/6.6.98+/allwinner/sun60i-a733-cubie-a7a.dtb; booti ${kernel_addr_r} - ${fdt_addr_r}"
boot
```

但这需要每次手动输入。**彻底方案需要 saveenv**，用户需自行评估风险。

### 方案 B 建议

由于 `saveenv` 被禁止，**方案 B 目前不推荐**。优先用方案 A 保数据，等以后有可靠方式（如 U-Boot 脚本在 SD 卡上、或 DT overlay 改 boot order）再尝试完全迁移。

---

## 回退方案（若方案 A 后出问题）

```bash
# 取消 bind mount，恢复 SD 卡的 /home/radxa
sudo umount /home/radxa
sudo rm -rf /home/radxa
sudo mv /home/radxa.sd.bak /home/radxa
sudo systemctl disable home-radxa.mount
```

---

## 关键检查清单

- [ ] SSD 被识别 (`lsblk` 看到 sda/nvme0n1)
- [ ] SSD 分区已格式化 ext4
- [ ] `rsync` 同步完成且没有 I/O 错误
- [ ] bind mount 成功 (`df -h` 显示 SSD)
- [ ] NPU 验证通过 (`vpm_run ret=0`)
- [ ] **没有重启 SD 卡**

---

## 来源与约束

- 用户硬约束：不动 U-Boot、`saveenv`、`mmc write`、bootargs
- SD 卡缺陷：`ext4lazyinit` 写位图会进一步损坏，禁止 `fsck`/`e2fsck` 在板上执行
- 本手册命令全部针对 SSD（`/dev/sda` 或 `/dev/nvme0n1`），**不碰 `/dev/mmcblk1`**

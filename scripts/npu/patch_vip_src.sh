#!/bin/sh
# patch_vip_src.sh —— 把 Radxa BSP 的 aw_nna_vip 源码适配到 6.6 内核并编译出 vipcore.ko
#
# 背景: 官方 BSP 的 vip 驱动是为更早内核写的, 直接编会有三处硬伤:
#   ① 头文件缺失:  asm/dma-iommu.h 在 6.6 已移除
#   ② Kbuild 路径: OBJS += $(srctree)/$(src)/... 在 M=<绝对路径> 下会双写路径
#   ③ 目标过期:    条件分支引用的 gc_vip_kernel_ion.o 已改名 vip_drv_device_ion.o
#   ④ 警告当错:    Kbuild 写死 -Werror, 而新 gcc 会报 %d/%p 格式警告
#
# 用法: sh patch_vip_src.sh <源码根>   (默认 ~/npu_src/drivers/npu/aw_nna_vip)
set -e
SRC=${1:-$HOME/npu_src/drivers/npu/aw_nna_vip}
V=$SRC/vip2
K=$V/Kbuild
KERN=/lib/modules/$(uname -r)/build

echo "=== 1) asm/dma-iommu.h (6.6 已移除) ==="
grep -rl "asm/dma-iommu.h" "$V" 2>/dev/null | while read -r f; do
  sed -i 's|#include <asm/dma-iommu.h>|/* PATCH 6.6: asm/dma-iommu.h removed */|' "$f"
  echo "  patched: $f"
done

echo "=== 2) Kbuild 绝对路径双写 + 过期目标名 ==="
sed -i 's|\$(srctree)/\$(src)/||g' "$K"
sed -i 's|gc_vip_kernel_ion\.o|vip_drv_device_ion.o|g' "$K"
echo "  patched: $K"

echo "=== 3) -Werror -> -Wno-error ==="
sed -i 's|EXTRA_CFLAGS += -Werror|EXTRA_CFLAGS += -Wno-error|g' "$K"

echo "=== 4) 编译 (M=<绝对路径> + 显式 KCFLAGS 补 include) ==="
INC="-I$V -I$V/inc -I$V/memory -I$V/os/linux -I$V/os/linux/allocator \
-I$V/os/linux/platform/allwinner -I$V/task"
cd "$V"
make -C "$KERN" M="$V" ARCH=arm64 \
  CONFIG_AW_NNA_VIP=m CONFIG_NNA_VIP2=y CONFIG_NPU_USER_IOMMU=n \
  KCFLAGS="$INC" modules

ls -la "$V/vipcore.ko"
echo "OK: $V/vipcore.ko"
echo "下一步: 部署 lyco-vipcore.service (unbind galcore -> insmod -> bind 3600000.npu)"

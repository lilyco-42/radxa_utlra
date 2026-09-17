#!/bin/sh
# build_npu.sh —— 在板上用 6.6 头文件编译 aw_nna_vip (vip2 / A733) 模块
# 注意: 老式 Kbuild 用 $(srctree)/$(src) 拼 include 路径, M= 绝对路径时会失效,
#       所以显式用 KCFLAGS 给出全部 -I。
set -e

B=/home/radxa/npu_src/drivers/npu/aw_nna_vip/vip2
K=/lib/modules/6.6.98-4-aw2511/build
INC="-I$B -I$B/inc -I$B/memory -I$B/os/linux -I$B/os/linux/allocator -I$B/os/linux/platform/allwinner -I$B/task"

cd "$B"
make -C "$K" M="$B" ARCH=arm64 \
  CONFIG_AW_NNA_VIP=m CONFIG_NNA_VIP2=y CONFIG_NPU_USER_IOMMU=n \
  KCFLAGS="$INC" modules
ls -la "$B"/*.ko

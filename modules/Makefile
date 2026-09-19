obj-m += npu_clk_fix.o
KDIR ?= /lib/modules/$(shell uname -r)/build

# 必须用 $(CURDIR) 而不是 $(PWD)。
#   $(PWD)   是 shell 的环境变量，`make -C <dir>` **不会**更新它 ——
#            于是从别处调用时会取到调用者的目录，M= 指错，报
#            "No rule to make target '<caller>/Makefile'"。
#   $(CURDIR) 是 make 自己在 chdir 之后设置的，指向真正的工作目录。
# 踩过：scripts/install-npu-clk-fix.sh 用 `make -C "$ROOT/modules"` 调用，
#       结果 M 变成 $ROOT 而不是 $ROOT/modules，编译失败。
M ?= $(CURDIR)

all:
	$(MAKE) -C $(KDIR) M=$(M) modules

clean:
	$(MAKE) -C $(KDIR) M=$(M) clean

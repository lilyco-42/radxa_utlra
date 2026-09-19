// npu_clk_fix2.c — A733 NPU clock enable, extended.
//
// The stock Radxa 6.6.98-4-aw2511 BSP leaves the NPU's quantized-compute
// datapath ungated. Empirically on this board:
//   - clk_npu (main compute)        enable=1  -> float inference works
//   - npu-gate (feeds clk_bus)      enable=0  -> quantized inference hangs
//   - nsi_master/npu power subdomain suspended
//
// This module enables clk_npu (as before) PLUS clk_bus (npu-gate) and the
// mbus/ahb gates, to test whether gating the bus clock is what blocks
// quantized (uint8/int16) NBG execution. Built in tmpfs, insmod'd at runtime;
// reversible via rmmod. Does not touch the SD card.

#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/clk.h>
#include <linux/err.h>

#define MAX_CLKS 8
static struct clk *clks[MAX_CLKS];
static int nclks;

static void enable_one(struct device *dev, const char *name)
{
	if (nclks >= MAX_CLKS)
		return;
	{
		struct clk *c = clk_get(dev, name);
		if (IS_ERR(c)) {
			pr_err("npu-clk-fix2: clk_get(%s) failed: %pe\n", name, c);
			return;
		}
		{
			int ret = clk_prepare_enable(c);
			if (ret) {
				pr_err("npu-clk-fix2: clk_prepare_enable(%s) failed: %d\n", name, ret);
				clk_put(c);
				return;
			}
		}
		clks[nclks++] = c;
		pr_info("npu-clk-fix2: enabled %s @ %lu Hz\n", name, clk_get_rate(c));
	}
}

static int __init npu_clk_fix2_init(void)
{
	struct device *dev;

	dev = bus_find_device_by_name(&platform_bus_type, NULL, "3600000.npu");
	if (!dev) {
		pr_err("npu-clk-fix2: platform device 3600000.npu not found\n");
		return -ENODEV;
	}

	enable_one(dev, "clk_npu");
	enable_one(dev, "clk_bus");
	enable_one(dev, "clk_mbus_gate");
	enable_one(dev, "clk_ahb_gate");

	put_device(dev);
	pr_info("npu-clk-fix2: init done (nclks=%d)\n", nclks);
	return 0;
}

static void __exit npu_clk_fix2_exit(void)
{
	int i;
	for (i = 0; i < nclks; i++) {
		clk_disable_unprepare(clks[i]);
		clk_put(clks[i]);
	}
	pr_info("npu-clk-fix2: unloaded (disabled %d clks)\n", nclks);
}

module_init(npu_clk_fix2_init);
module_exit(npu_clk_fix2_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("A733 NPU: enable core + bus clocks (quantized-path probe)");

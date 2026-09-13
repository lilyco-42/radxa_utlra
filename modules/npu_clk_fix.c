// npu_clk_fix.c — Enable the NPU core clock that galcore prepares but never
// enables on the Radxa Cubie A7A 6.6 BSP (6.6.98-4-aw2511).
//
// Symptom: galcore probes fine (clk_prepare at probe -> prep=1) but the
// clk_enable_count stays 0, so VIP9000 jobs never execute and every submission
// ends in "core0 hang, automatic recovery" (galcore) or "VIP not going to idle"
// (vipcore). Loading this module runs clk_prepare_enable("clk_npu") on the
// 3600000.npu platform device; CCF propagates enable up to pll-npu.
//
// Verified safe: does not touch galcore, no rmmod (rmmod galcore panics the
// board), reversible via module unload.

#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/clk.h>
#include <linux/err.h>

static struct clk *clk_npu;

static int __init npu_clk_fix_init(void)
{
	struct device *dev;
	int ret;

	dev = bus_find_device_by_name(&platform_bus_type, NULL, "3600000.npu");
	if (!dev) {
		pr_err("npu-clk-fix: platform device 3600000.npu not found\n");
		return -ENODEV;
	}

	clk_npu = clk_get(dev, "clk_npu");
	put_device(dev);
	if (IS_ERR(clk_npu)) {
		pr_err("npu-clk-fix: clk_get(clk_npu) failed: %pe\n", clk_npu);
		return PTR_ERR(clk_npu);
	}

	ret = clk_prepare_enable(clk_npu);
	if (ret) {
		pr_err("npu-clk-fix: clk_prepare_enable failed: %d\n", ret);
		clk_put(clk_npu);
		return ret;
	}

	pr_info("npu-clk-fix: clk_npu enabled at %lu Hz\n", clk_get_rate(clk_npu));
	return 0;
}

static void __exit npu_clk_fix_exit(void)
{
	if (clk_npu && !IS_ERR(clk_npu)) {
		clk_disable_unprepare(clk_npu);
		clk_put(clk_npu);
	}
}

module_init(npu_clk_fix_init);
module_exit(npu_clk_fix_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("A733: enable NPU core clock left gated by galcore (6.6 BSP)");

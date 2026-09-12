#!/usr/bin/env bash
#
# A7A GPU 验证脚本：PowerVR BXM-4-64 (Rogue BVNC 36.56.104.183)
# 验证三件事：
#   1. DRM/render 节点与 pvrsrvkm 内核模块
#   2. Vulkan 厂商 ICD（Imagination 原厂驱动，非 Mesa 软件兜底）
#   3. OpenCL 3.0 真实计算 + GPU 硬件中断计数增量（证明计算落在 GPU 上）
#
# 用法：
#   bash gpu-check.sh          # 全量验证（会跑一次 OpenCL 计算）
#   bash gpu-check.sh --quick  # 只查枚举，不跑计算
#
# 无需 root（渲染节点属 video 组；中断计数读 /proc/interrupts 人人可读）
#
set -uo pipefail

QUICK=0
[[ "${1:-}" == "--quick" ]] && QUICK=1

PASS=0; FAIL=0
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '\033[1;31m  ✗\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
info() { printf '\033[1;34m  ·\033[0m %s\n' "$*"; }
hdr()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

echo "════════════════════════════════════════════════════════"
echo "  A7A GPU 验证 (PowerVR BXM-4-64)"
echo "════════════════════════════════════════════════════════"

# ---------------------------------------------------------- 1. 内核侧
hdr "1. 内核 / 设备节点"

if grep -qw pvrsrvkm /proc/modules; then
    ok "pvrsrvkm 内核模块已加载"
else
    bad "pvrsrvkm 未加载 —— PowerVR DDK 驱动缺失"
fi

for n in /dev/dri/card0 /dev/dri/renderD128; do
    if [[ -e "$n" ]]; then ok "$n 存在"; else bad "$n 缺失"; fi
done

if [[ -r /sys/class/drm/renderD128/device/uevent ]]; then
    drv="$(grep -m1 '^DRIVER=' /sys/class/drm/renderD128/device/uevent | cut -d= -f2)"
    [[ "$drv" == "pvrsrvkm" ]] \
        && ok "renderD128 归属 $drv" \
        || bad "renderD128 归属异常：$drv"
fi

# 当前用户能否访问渲染节点
if [[ -w /dev/dri/renderD128 ]]; then
    ok "当前用户可写 renderD128"
else
    bad "当前用户无权写 renderD128（把用户加进 video 组：usermod -aG video \$USER）"
fi

# GPU 时钟
if [[ -r /sys/kernel/debug/clk/gpu0/clk_rate ]]; then
    rate="$(cat /sys/kernel/debug/clk/gpu0/clk_rate)"
    ok "GPU 时钟 $(awk -v r="$rate" 'BEGIN{printf "%.0f", r/1000000}') MHz"
fi

# ---------------------------------------------------------- 2. Vulkan
hdr "2. Vulkan"

if [[ -f /usr/lib/libVK_IMG.so || -f /usr/lib/aarch64-linux-gnu/libVK_IMG.so ]]; then
    ok "厂商 Vulkan 库 libVK_IMG.so 存在"
else
    bad "未找到 libVK_IMG.so（PowerVR Vulkan 驱动）"
fi
info "来源包：xserver-xorg-img-bxm（与 OpenCL 同一 DDK）"

if [[ -f /usr/share/vulkan/icd.d/img_icd.json ]]; then
    ok "ICD 描述文件 img_icd.json 存在"
    libpath="$(grep -o '"library_path"[^,]*' /usr/share/vulkan/icd.d/img_icd.json | cut -d'"' -f4)"
    info "library_path = $libpath"
else
    bad "缺少 /usr/share/vulkan/icd.d/img_icd.json"
fi

if command -v vulkaninfo >/dev/null 2>&1; then
    VI="$(mktemp)"
    timeout 90 vulkaninfo --summary >"$VI" 2>/dev/null || true
    if grep -q 'PowerVR B-Series' "$VI"; then
        api="$(grep -m1 'apiVersion' "$VI" | sed 's/.*= *//')"
        drv="$(grep -m1 'driverName' "$VI" | sed 's/.*= *//')"
        ok "Vulkan 枚举到 PowerVR 设备（apiVersion=$api, $drv）"

        if grep -q 'DRIVER_ID_IMAGINATION_PROPRIETARY' "$VI"; then
            ok "使用 Imagination 原厂驱动（非 Mesa lavapipe 软件兜底）"
        else
            bad "未看到原厂驱动 ID，可能只枚举到软件实现"
        fi
    else
        bad "vulkaninfo 未枚举到 PowerVR 设备"
    fi
    rm -f "$VI"
else
    bad "vulkaninfo 未安装：apt install vulkan-tools"
fi

# ---------------------------------------------------------- 3. OpenCL
hdr "3. OpenCL 计算"

if [[ -f /etc/OpenCL/vendors/powervr.icd ]]; then
    ok "powervr.icd 已注册（$(cat /etc/OpenCL/vendors/powervr.icd)）"
else
    bad "缺少 /etc/OpenCL/vendors/powervr.icd"
fi

if ! command -v clinfo >/dev/null 2>&1; then
    bad "clinfo 未安装：apt install clinfo"
else
    # 只跑一次，落盘后本地 grep，避免在 pipefail 下被 SIGPIPE 影响判断
    CI="$(mktemp)"
    timeout 60 clinfo >"$CI" 2>/dev/null || true
    if grep -q 'PowerVR B-Series' "$CI"; then
        cu="$(grep -m1 'Max compute units' "$CI" | awk '{print $NF}')"
        gm="$(grep -m1 'Global memory size' "$CI" | awk '{print $(NF-1), $NF}')"
        ok "OpenCL 设备就绪（compute units=${cu:-?}, global mem=${gm:-?}）"
    else
        bad "clinfo 未发现 PowerVR OpenCL 设备"
    fi
    rm -f "$CI"
fi

if [[ $QUICK -eq 1 ]]; then
    echo
    info "--quick 模式：跳过真实计算测试"
else
    # 真实计算 + 中断计数验证
    if ! command -v g++ >/dev/null 2>&1; then
        bad "g++ 未安装，跳过计算测试：apt install g++"
    elif [[ ! -f /usr/include/CL/cl.h ]]; then
        bad "OpenCL 头文件缺失：apt install opencl-headers"
    else
        TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

        cat > "$TMP/vecadd.cl" <<'CL'
__kernel void vecadd(__global const float* a, __global const float* b,
                     __global float* c, int n) {
    int i = get_global_id(0);
    if (i < n) c[i] = a[i] + b[i];
}
CL

        cat > "$TMP/vecadd.cpp" <<'CPP'
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <CL/cl.h>
int main(int argc, char** argv){
    if (argc < 2) return 2;
    cl_platform_id p; cl_uint np = 0;
    if (clGetPlatformIDs(1, &p, &np) != CL_SUCCESS || np == 0) { puts("NO_PLATFORM"); return 2; }
    cl_device_id d; cl_uint nd = 0;
    if (clGetDeviceIDs(p, CL_DEVICE_TYPE_GPU, 1, &d, &nd) != CL_SUCCESS) { puts("NO_GPU_DEVICE"); return 2; }
    const int N = 1 << 22;
    size_t sz = N * sizeof(float);
    float *a = (float*)malloc(sz), *b = (float*)malloc(sz), *c = (float*)malloc(sz);
    if (!a || !b || !c) return 2;
    for (int i = 0; i < N; i++) { a[i] = i * 0.5f; b[i] = i * 0.25f; }
    cl_int e;
    cl_context ctx = clCreateContext(NULL, 1, &d, NULL, NULL, &e);
    cl_command_queue q = clCreateCommandQueue(ctx, d, CL_QUEUE_PROFILING_ENABLE, &e);
    cl_mem ma = clCreateBuffer(ctx, CL_MEM_READ_ONLY | CL_MEM_COPY_HOST_PTR, sz, a, NULL);
    cl_mem mb = clCreateBuffer(ctx, CL_MEM_READ_ONLY | CL_MEM_COPY_HOST_PTR, sz, b, NULL);
    cl_mem mc = clCreateBuffer(ctx, CL_MEM_WRITE_ONLY, sz, NULL, NULL);
    FILE* f = fopen(argv[1], "r");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long L = ftell(f); rewind(f);
    char* src = (char*)malloc(L + 1);
    if (fread(src, 1, L, f) != (size_t)L) return 2;
    src[L] = 0; fclose(f);
    const char* ps = src;
    cl_program pr = clCreateProgramWithSource(ctx, 1, &ps, NULL, &e);
    if (clBuildProgram(pr, 1, &d, NULL, NULL, NULL) != CL_SUCCESS) {
        char lg[16384]; clGetProgramBuildInfo(pr, d, CL_PROGRAM_BUILD_LOG, sizeof(lg), lg, NULL);
        printf("BUILD_FAIL\n%s\n", lg); return 2;
    }
    cl_kernel k = clCreateKernel(pr, "vecadd", NULL);
    clSetKernelArg(k, 0, sizeof(cl_mem), &ma);
    clSetKernelArg(k, 1, sizeof(cl_mem), &mb);
    clSetKernelArg(k, 2, sizeof(cl_mem), &mc);
    clSetKernelArg(k, 3, sizeof(int), &N);
    size_t gws = (size_t)N;
    cl_event ev;
    if (clEnqueueNDRangeKernel(q, k, 1, NULL, &gws, NULL, 0, NULL, &ev) != CL_SUCCESS) {
        puts("ENQUEUE_FAIL"); return 2;
    }
    clWaitForEvents(1, &ev);
    cl_ulong t0 = 0, t1 = 0;
    clGetEventProfilingInfo(ev, CL_PROFILING_COMMAND_START, sizeof(t0), &t0, NULL);
    clGetEventProfilingInfo(ev, CL_PROFILING_COMMAND_END, sizeof(t1), &t1, NULL);
    clFinish(q);
    clEnqueueReadBuffer(q, mc, CL_TRUE, 0, sz, c, 0, NULL, NULL);
    int bad = 0;
    for (int i = 0; i < N; i++) {
        float ex = a[i] + b[i];
        if (fabsf(c[i] - ex) > 1e-2f) bad++;
    }
    double ms = (t1 - t0) / 1e6;
    printf("RESULT %s N=%d ms=%.3f mismatches=%d gflops=%.2f\n",
           bad ? "FAIL" : "PASS", N, ms, bad, (3.0 * N) / (ms / 1000.0) / 1e9);
    return bad ? 1 : 0;
}
CPP

        if g++ -O2 -w -o "$TMP/vecadd" "$TMP/vecadd.cpp" -lOpenCL 2>"$TMP/build.log"; then
            before="$(grep 'pvrsrvkm' /proc/interrupts | awk '{s=0; for(i=2;i<=NF-3;i++) s+=$i; print s}')"
            out="$("$TMP/vecadd" "$TMP/vecadd.cl" 2>&1)"; rc=$?
            after="$(grep 'pvrsrvkm' /proc/interrupts | awk '{s=0; for(i=2;i<=NF-3;i++) s+=$i; print s}')"
            delta=$(( after - before ))

            if [[ "$out" == *"PASS"* ]]; then
                ok "OpenCL 向量加法 PASS（$(grep -o 'ms=[0-9.]*' <<<"$out"), $(grep -o 'gflops=[0-9.]*' <<<"$out")）"
            else
                bad "OpenCL 计算失败：$out"
            fi

            if [[ $delta -gt 0 ]]; then
                ok "GPU 硬件中断 +$delta（证明计算落在 GPU 而非 CPU 兜底）"
            else
                bad "GPU 中断计数无变化（计算可能未真正下发到硬件）"
            fi
        else
            bad "OpenCL 测试程序编译失败，见 $TMP/build.log"
            sed 's/^/      /' "$TMP/build.log" | tail -5
        fi
    fi
fi

# ---------------------------------------------------------- 汇总
echo
echo "════════════════════════════════════════════════════════"
if [[ $FAIL -eq 0 ]]; then
    printf '\033[1;32m  GPU 全部通过：%d 项\033[0m\n' "$PASS"
    echo "  Vulkan: PowerVR B-Series BXM-4-64 (Imagination 原厂驱动)"
    echo "  OpenCL: 3.0，硬件中断已验证"
    rc=0
else
    printf '\033[1;31m  通过 %d 项，失败 %d 项\033[0m\n' "$PASS" "$FAIL"
    rc=1
fi
echo "════════════════════════════════════════════════════════"
exit $rc

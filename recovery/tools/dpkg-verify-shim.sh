#!/bin/bash
# dpkg-verify-shim.sh —— 在 dpkg 数据库已经坏掉、`dpkg -V` 跑不了的机器上，
# 直接用 /var/lib/dpkg/info/*.md5sums 反查「哪些包文件被写坏了」。
#
# ── 为什么需要它 ────────────────────────────────────────────────────
# 存储损坏的典型形态是「**文件大小对、mtime 对、内容全错**」。
# 而 `dpkg -V` 依赖 /var/lib/dpkg/status —— 那个文件往往也是受害者，
# 于是 dpkg 会直接报错退出：
#     dpkg: error: parsing file '/var/lib/dpkg/status' near line 0:
#      field name '??nxuk?*K~…' must be followed by colon
# 更坏的情况是某些版本静默返回 0 行 —— 看起来「没坏」，是**假绿**。
#
# *.md5sums 是每个包一个独立小文件，命中概率高得多，而且它不依赖 status。
# 用它就能在没有 dpkg 数据库的情况下拿到权威的内容损坏清单。
#
# ── 用法 ────────────────────────────────────────────────────────────
#   sudo ./dpkg-verify-shim.sh                      # 全量比对
#   sudo ./dpkg-verify-shim.sh --prefix /usr        # 只看某个路径前缀
#   sudo ./dpkg-verify-shim.sh --out /tmp/bad.txt   # 把清单另存一份
#   sudo ./dpkg-verify-shim.sh --selftest           # 用合成样本自检
#
# ── 四个必须注意的坑（全部实测踩过，别再犯）──────────────────────────
# 1) **必须 `LC_ALL=C`。**
#    非 C locale 下 `[!-~]` 这类**范围类**按 collation 解释，不再按字节序。
#    实测：同一条记录在 `en_US.UTF-8` 下
#    `grep -c -E '^[0-9a-f]{32}  /[!-~]+$'` 匹配 **0** 次，在 `C` 下匹配 **1** 次。
#    → 本脚本显式 `export LC_ALL=C`，并用 awk 而不是带 `$` 锚的 ERE。
#
# 2) **`xargs` 必须带 `-r`。**
#    不带 `-r` 时，输入为空会**执行一次不带参数的命令**；
#    而 `md5sum` 不带参数会去读 **stdin** → 在 SSH 会话里**永久挂死**
#    （实测：`timeout 5` 得到 RC=124；加 `-r` 后 RC=0）。
#    排查现场会表现为「脚本卡住、输出文件 0 字节、ps 里 grep 已经没了」。
#
# 3) **md5sums 数据库自己可能也是坏的。**
#    先按格式过滤掉垃圾行，否则每一条垃圾都变成一条「坏文件」假阳性。
#    ⚠️ 过滤必须在「给路径补 `/` 前缀**之前**」做 —— 补完前缀再判断
#    「以 / 开头」是恒真的，等于没过滤。（这个 bug 是 `--selftest` 抓出来的。）
#
# 4) **比对结果要自证。** 如果超过 1/3 的条目不一致，先怀疑数据库自己坏了，
#    而不是「机器坏成这样了」。脚本会主动提示，并让你改用
#    `/var/backups/dpkg.status.*.gz`（损坏前的备份）重建期望值。
#
# 5) **`md5sum` 的分隔符在 Windows(Git Bash) 下是 `*` 不是两个空格。**
#    二进制模式下输出 `<hash> *<path>`，与 Linux 的 `<hash>  <path>` 只差一个字符，
#    却让 `comm` 把**每一条**都判成不一致 → 好文件全被误报成坏的。
#    本脚本在比对前统一归一化成两空格（Linux 上该 sed 是无副作用的空操作）。
#
# 本脚本只读，不写目标文件系统（临时文件都落在 /tmp）。

set -u
LC_ALL=C
export LC_ALL

# 允许环境变量覆盖，selftest 要用
INFO_DIR="${INFO_DIR:-/var/lib/dpkg/info}"
PREFIX=""
OUT=""
SELFTEST=0

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)   PREFIX="${2:-}"; shift 2 ;;
        --out)      OUT="${2:-}";    shift 2 ;;
        --info-dir) INFO_DIR="${2:-}"; shift 2 ;;
        --selftest) SELFTEST=1; shift ;;
        --_inner)   shift ;;          # selftest 内部递归调用用
        -h|--help)  sed -n '2,50p' "$0"; exit 0 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

# ── 自检：用合成样本验证「抓得住坏文件、不误报好文件、会过滤垃圾行」────
# 设计要点：md5sums 里写**临时目录的真实绝对路径**（相对 / 的形式），
# 这样自检走的就是主流程的同一段代码，不需要任何「换根」的特殊分支。
if [ "$SELFTEST" = 1 ]; then
    d=$(mktemp -d /tmp/dpv-selftest.XXXXXX) || exit 1
    trap 'rm -rf "$d"' EXIT
    mkdir -p "$d/info" "$d/root/usr/bin" "$d/root/etc"
    printf 'GOOD\n'     > "$d/root/usr/bin/good"
    printf 'TAMPERED\n' > "$d/root/usr/bin/bad"
    printf 'ALSO\n'     > "$d/root/etc/conf"
    # 趁 bad 还没被改，先把「正确」的 md5 记下来
    g=$(md5sum "$d/root/usr/bin/good" | cut -d' ' -f1)
    b=$(md5sum "$d/root/usr/bin/bad"  | cut -d' ' -f1)
    a=$(md5sum "$d/root/etc/conf"     | cut -d' ' -f1)
    rel="${d#/}"
    {
        echo "$g  $rel/root/usr/bin/good"          # 应当通过
        echo "$b  $rel/root/usr/bin/bad"           # 下面会被改坏 → 应当被抓出
        echo "$a  $rel/root/etc/conf"              # 应当通过
        echo "!!这行是垃圾行，必须被过滤掉!!"        # 第一列长度不对 → 过滤
        echo "0123456789abcdef0123456789abcdef  非 ASCII 路径 → 过滤"
    } > "$d/info/fake.md5sums"
    printf 'CHANGED\n' > "$d/root/usr/bin/bad"     # 制造损坏

    SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    INFO_DIR="$d/info" "$SELF" --_inner > "$d/out.txt" 2>&1
    rc=$?
    badhit=$(grep -c "/usr/bin/bad\$"  "$d/out.txt" || true)
    goodhit=$(grep -c "/usr/bin/good\$" "$d/out.txt" || true)
    junk=$(grep -c '^其中被写坏的垃圾行 *: 2$' "$d/out.txt" || true)
    if [ "$rc" = 0 ] && [ "$badhit" = 1 ] && [ "$goodhit" = 0 ] && [ "$junk" = 1 ]; then
        echo "SELFTEST PASS  （抓出坏文件 1 个 / 未误报好文件 / 2 行垃圾已过滤）"
        exit 0
    fi
    echo "SELFTEST FAIL  rc=$rc bad=$badhit good=$goodhit junk=$junk"
    sed 's/^/    | /' "$d/out.txt"
    exit 1
fi

if [ ! -d "$INFO_DIR" ]; then
    echo "找不到 $INFO_DIR —— 这台机器上没有 dpkg 数据库？" >&2
    exit 1
fi

TMP=$(mktemp -d /tmp/dpv.XXXXXX) || exit 1
trap 'rm -rf "$TMP"' EXIT

# ── 1. 期望清单（顺手过滤掉被写坏的垃圾行）──────────────────────────
# 判据：第一列必须是 32 位十六进制；路径必须是纯 ASCII 可打印。
# （LC_ALL=C 下非 ASCII 字节会被 [[:print:]] 排除 —— Debian 包路径本来都是 ASCII）
cat "$INFO_DIR"/*.md5sums 2>/dev/null > "$TMP/raw.txt"
TOTAL_ALL=$(wc -l < "$TMP/raw.txt")

awk '
    length($1)==32 && $1 ~ /^[0-9a-f]+$/ {
        p = $0
        sub(/^[0-9a-f]+[ \t]+/, "", p)      # 去掉 md5 字段和分隔空白
        if (length(p) < 1 || length(p) > 1024) next
        if (p ~ /[^[:print:]]/) next
        print $1 "  /" p
    }' "$TMP/raw.txt" | sort -u > "$TMP/exp.txt"
TOTAL=$(wc -l < "$TMP/exp.txt")
JUNK=$((TOTAL_ALL - TOTAL))

if [ -n "$PREFIX" ]; then
    awk -v p="$PREFIX" 'index($2, p)==1' "$TMP/exp.txt" > "$TMP/exp2.txt"
else
    cp "$TMP/exp.txt" "$TMP/exp2.txt"
fi
N=$(wc -l < "$TMP/exp2.txt")

echo "md5sums 原始行数      : $TOTAL_ALL"
echo "格式合法（可比对）    : $TOTAL"
echo "其中被写坏的垃圾行    : $JUNK"
echo "本次实际比对范围      : $N 条${PREFIX:+（前缀 $PREFIX）}"

if [ "$N" = 0 ]; then
    echo "没有可比对的条目 —— 检查 --prefix，或 md5sums 数据库是否整体损坏" >&2
    exit 1
fi

# ── 2. 实际值 ───────────────────────────────────────────────────────
# 注意 -r：没有它，空输入会让 md5sum 读 stdin 永久挂死
#
# ⚠️ 分隔符归一化（本脚本第 5 个坑）：
#    GNU coreutils 的 md5sum 在**文本模式**下输出 `<hash>  <path>`（两空格），
#    但在 MSYS/Git Bash 下默认是**二进制模式**，输出 `<hash> *<path>`（星号）。
#    星号只差一个字符，却会让 `comm` 把**每一个**条目都判成不一致 ——
#    表现是「好文件也全被报成坏的」，看起来像整台机器都烂了。
#    所以比对前统一成两空格。Linux 上这条 sed 是无副作用的空操作。
awk '{print $2}' "$TMP/exp2.txt" > "$TMP/paths.txt"
xargs -r -d '\n' -a "$TMP/paths.txt" -n 300 md5sum 2>"$TMP/err" \
    | sed -E 's/^([0-9a-f]{32}) [ *]/\1  /' \
    | sort -u > "$TMP/got.txt"
GOT=$(wc -l < "$TMP/got.txt")
ERRLINES=$(wc -l < "$TMP/err")

# ── 3. 比对 ─────────────────────────────────────────────────────────
comm -23 "$TMP/exp2.txt" "$TMP/got.txt" > "$TMP/bad.txt"
BAD=$(wc -l < "$TMP/bad.txt")

echo "成功算出 md5          : $GOT"
echo "读失败告警行数        : $ERRLINES"
echo "── 不一致（= 内容被改坏）: $BAD 条"

# 自证：坏得太多就先怀疑数据库，别急着下结论
if [ "$N" -gt 100 ] && [ "$BAD" -gt $((N / 3)) ]; then
    echo "" >&2
    echo "!! 超过 1/3 的条目不一致 —— 高度怀疑 md5sums 数据库**本身**也被写坏了。" >&2
    echo "!! 这个结果不可信。改用 /var/backups/dpkg.status.*.gz（损坏前的备份）重建期望值。" >&2
fi

if [ "$BAD" -gt 0 ]; then
    echo ""
    echo "── 坏文件按目录分布（前 30）"
    awk '{print $2}' "$TMP/bad.txt" | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn | head -30
    echo ""
    echo "── 坏文件清单（前 100）"
    awk '{print $2}' "$TMP/bad.txt" | head -100
    if [ "$BAD" -gt 100 ]; then
        echo "...（共 $BAD 条）"
    fi
    if [ -n "$OUT" ]; then
        cp "$TMP/bad.txt" "$OUT"
        echo ""
        echo "完整清单已写入: $OUT"
    fi
fi

if [ "$ERRLINES" -gt 0 ]; then
    echo ""
    echo "── 读失败详情（前 10）"
    head -10 "$TMP/err"
fi

exit 0

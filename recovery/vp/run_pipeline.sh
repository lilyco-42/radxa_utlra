#!/bin/bash
# run_pipeline.sh — 完整流水线：话题 → 文案 → 配音 → 视频 →（可选）发布
#
# 用法:
#   ./run_pipeline.sh                    # 自动从 topics.txt 取下一个话题
#   ./run_pipeline.sh "指定话题"          # 用指定话题
#   PUBLISH=1 ./run_pipeline.sh          # 生成后自动发布
#
# 退出码: 0=成功  3=TTS失败  4=渲染失败  75=已有实例在跑（flock 冲突）
# 日志在 ~/vp/logs/

set -uo pipefail

VP="$HOME/vp"
cd "$VP" || exit 1

VENV="$HOME/venv-tts/bin"
HV="$HOME/html-video"
LOGDIR="$VP/logs"
mkdir -p "$LOGDIR" videos

# ── 单实例锁（防止 timer 重入 / 手动跑撞车）──────────────
LOCK="$VP/.pipeline.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date '+%H:%M:%S')] 已有流水线在运行（flock 冲突），本次跳过" | tee -a "$LOGDIR/flock.log"
  exit 75
fi

# ── 资源保护（1.6G/3.9G 板子上跑 Chromium + Node 必需）─────
export MALLOC_ARENA_MAX=2
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=768}"

TS=$(date +%Y%m%d-%H%M%S)
LOG="$LOGDIR/run-$TS.log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# ── 清理旧产物（保守策略）─────────────────────────────
# 安全约束（缺一不可）：
#   1. 只处理 $VP/videos 下的**一级子目录**
#   2. 只删**带 .published 标记**的（= 已成功发布过的）
#   3. 必须超过 RETENTION_DAYS 天
#   4. 无论如何**至少保留最近 MIN_KEEP 条**
# 未发布的成品永远不会被自动删除。
do_cleanup() {
  local RETENTION_DAYS="${RETENTION_DAYS:-7}"
  local MIN_KEEP="${MIN_KEEP:-10}"
  local VD="$VP/videos"

  [ -d "$VD" ] || return 0

  # 已发布且超期的候选
  local CAND TOTAL NCAND CAN_DEL DELETED=0 d SZ
  CAND=$(find "$VD" -mindepth 1 -maxdepth 1 -type d \
           -name 'v20*' -mtime +"$RETENTION_DAYS" \
           -exec test -f '{}/.published' \; -print 2>/dev/null | sort)
  TOTAL=$(find "$VD" -mindepth 1 -maxdepth 1 -type d -name 'v20*' 2>/dev/null | wc -l)
  NCAND=$(printf '%s\n' "$CAND" | grep -c . 2>/dev/null || echo 0)

  CAN_DEL=$(( TOTAL - MIN_KEEP ))
  [ "$CAN_DEL" -lt 0 ] && CAN_DEL=0

  for d in $CAND; do
    [ "$DELETED" -ge "$CAN_DEL" ] && break
    # 双保险：路径必须真在 videos/ 下
    case "$d" in
      "$VD"/v20*) ;;
      *) log "   ⚠️ 跳过异常路径: $d"; continue ;;
    esac
    SZ=$(du -sh "$d" 2>/dev/null | cut -f1)
    echo "$(date '+%Y-%m-%d %H:%M:%S')	$d	$SZ" >> "$LOGDIR/cleanup.log"
    rm -rf -- "$d"
    DELETED=$(( DELETED + 1 ))
  done

  if [ "$DELETED" -gt 0 ]; then
    log "   已清理 $DELETED 条（候选 $NCAND / 总 $TOTAL）"
  else
    log "   无需清理（总 $TOTAL 条，候选 $NCAND 条，最多可删 $CAN_DEL 条）"
  fi
}

# ── 只清理模式（方便单独测试）──────────────────────────
if [ "${1:-}" = "--cleanup-only" ]; then
  log "⑤ 清理：保留最近 ${RETENTION_DAYS:-7} 天 / 至少 ${MIN_KEEP:-10} 条（只删已发布的）"
  do_cleanup
  log "✅ 清理完成"
  exit 0
fi

# ── 取话题 ────────────────────────────────────────────
TOPIC="${1:-}"
if [ -z "$TOPIC" ]; then
  if [ -f "$VP/topics.txt" ]; then
    # 轮换：读状态文件里的行号
    IDX_FILE="$VP/.topic_index"
    IDX=$(cat "$IDX_FILE" 2>/dev/null || echo 0)
    TOTAL=$(grep -cve '^\s*$' "$VP/topics.txt")
    [ "$TOTAL" -eq 0 ] && { log "topics.txt 为空"; exit 1; }
    LINE=$(( (IDX % TOTAL) + 1 ))
    TOPIC=$(grep -ve '^\s*$' "$VP/topics.txt" | sed -n "${LINE}p")
    echo $(( IDX + 1 )) > "$IDX_FILE"
    log "轮换话题 #$LINE/$TOTAL: $TOPIC"
  else
    log "没有 topics.txt 且未指定话题"
    exit 1
  fi
fi

# 目录名：话题转成安全的 slug
SLUG=$(echo "$TOPIC" | tr -d '[:space:]' | head -c 20 | md5sum | cut -c1-8)
NAME="v$TS-$SLUG"
OUT="$VP/videos/$NAME"
mkdir -p "$OUT"
log "输出目录: $OUT"

# ── ① 文案 ────────────────────────────────────────────
log "① 生成文案…"
if ! timeout 300 "$VENV/python" "$VP/gen_script.py" \
      --topic "$TOPIC" --out "$OUT" --chars "${CHARS:-300}" >>"$LOG" 2>&1; then
  log "❌ 文案生成失败"
  exit 2
fi
[ -s "$OUT/script.txt" ] || { log "❌ script.txt 为空"; exit 2; }
log "   文案 $(wc -m < "$OUT/script.txt") 字"

# ── ② 配音 + 字幕（带重试：edge-tts 偶发 NoAudioReceived）──
log "② 生成配音与字幕…"
TTS_OK=0
for attempt in 1 2 3; do
  if timeout 180 "$VENV/edge-tts" -f "$OUT/script.txt" \
        --voice "${VOICE:-zh-CN-YunxiNeural}" --rate="${RATE:-+8%}" \
        --write-media "$OUT/tts.mp3" --write-subtitles "$OUT/tts.srt" >>"$LOG" 2>&1 \
     && [ -s "$OUT/tts.srt" ]; then
    TTS_OK=1
    break
  fi
  log "   ⚠️ TTS 第 $attempt 次失败，5 秒后重试…"
  sleep 5
done
if [ "$TTS_OK" -ne 1 ]; then
  log "❌ TTS 失败（3 次重试均失败）"
  exit 3
fi
log "   字幕 $(grep -c ' --> ' "$OUT/tts.srt") 条"

# ── ③ 视频（资源限制 + 低优先级）───────────────────────
log "③ 渲染视频…"
TITLE=$(python3 -c "
import json,sys
try:
    m=json.load(open('$OUT/meta.json',encoding='utf-8'))
    t=m.get('title','')
    print(t)
except Exception: print('')
" 2>/dev/null)
[ -z "$TITLE" ] && TITLE="$TOPIC"

if ! timeout 900 nice -n 5 node "$VP/gen_video_srt.mjs" \
      --srt "$OUT/tts.srt" --audio "$OUT/tts.mp3" --out "$OUT/final.mp4" \
      --name "$NAME" --title "$TITLE" --kicker "${KICKER:-AI · 科技}" >>"$LOG" 2>&1; then
  log "❌ 渲染失败"
  exit 4
fi
[ -s "$OUT/final.mp4" ] || { log "❌ final.mp4 为空"; exit 4; }
SIZE=$(stat -c%s "$OUT/final.mp4")
log "   视频 $(( SIZE / 1024 )) KB"

# ── ④ 发布（可选）─────────────────────────────────────
PUBLISHED=0
if [ "${PUBLISH:-0}" = "1" ]; then
  log "④ 发布…"
  if ! timeout 900 python3 "$VP/publish.py" \
        --video "$OUT/final.mp4" --meta "$OUT/meta.json" >>"$LOG" 2>&1; then
    log "❌ 发布失败（视频已生成，可手动重发）"
    # 发布失败不退出 —— 清理逻辑照常跑，但这条不会被删（没有 .published 标记）
  else
    log "   ✓ 已提交发布"
    date '+%Y-%m-%d %H:%M:%S' > "$OUT/.published"
    PUBLISHED=1
  fi
else
  log "④ 跳过发布（PUBLISH=1 可开启）"
fi

# ── ⑤ 清理旧产物 ──────────────────────────────────────
if [ "${CLEANUP:-1}" = "1" ]; then
  log "⑤ 清理：保留最近 ${RETENTION_DAYS:-7} 天 / 至少 ${MIN_KEEP:-10} 条（只删已发布的）"
  do_cleanup
fi

log "✅ 完成: $OUT/final.mp4"
echo "$OUT" >> "$LOGDIR/success.log"
exit 0

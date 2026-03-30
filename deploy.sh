#!/usr/bin/env bash
set -e

# ── Colors ────────────────────────────────────────────────────
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
CYAN="\033[0;36m"
RESET="\033[0m"

log()  { echo -e "${GREEN}[deploy]${RESET} $*"; }
warn() { echo -e "${YELLOW}[deploy]${RESET} $*"; }
err()  { echo -e "${RED}[deploy]${RESET} $*"; exit 1; }

# ── Parse args ────────────────────────────────────────────────
SERVICES="bot web"   # default: rebuild both
SHOW_LOGS=true

for arg in "$@"; do
  case $arg in
    --bot-only)  SERVICES="bot" ;;
    --web-only)  SERVICES="web" ;;
    --no-log)    SHOW_LOGS=false ;;
    --help)
      echo "Usage: ./deploy.sh [--bot-only] [--web-only] [--no-log]"
      exit 0 ;;
  esac
done

# ── Check .env ────────────────────────────────────────────────
if [ ! -f .env ]; then
  warn ".env 不存在，請確認 TELEGRAM_BOT_TOKEN 已設定"
fi

# ── Build ─────────────────────────────────────────────────────
log "開始重新 build：${SERVICES}"
docker compose build --no-cache $SERVICES

# ── Restart ───────────────────────────────────────────────────
log "重新啟動服務..."
docker compose up -d --force-recreate $SERVICES

# ── Health check ──────────────────────────────────────────────
log "等待服務啟動..."
sleep 3

for svc in $SERVICES; do
  STATUS=$(docker compose ps --format json $svc 2>/dev/null | python3 -c "
import sys, json
lines = sys.stdin.read().strip().splitlines()
for line in lines:
    d = json.loads(line)
    print(d.get('State', d.get('Status', 'unknown')))
" 2>/dev/null || echo "unknown")
  if echo "$STATUS" | grep -qi "running\|up"; then
    log "${svc}: ${GREEN}running${RESET}"
  else
    warn "${svc}: 狀態 = ${STATUS}（可能仍在啟動中）"
  fi
done

# ── Logs ──────────────────────────────────────────────────────
if [ "$SHOW_LOGS" = true ]; then
  echo ""
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo -e "${CYAN}  即時 Log（Ctrl+C 結束）${RESET}"
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo ""
  docker compose logs -f --tail=50 $SERVICES
fi

#!/bin/bash
# =============================================================
#  AiAprint — скрипт деплоя
#  Использование: bash deploy.sh
#  Выполнять на сервере из любой директории
# =============================================================

set -euo pipefail

APP_DIR="/opt/aiaprint"
VENV="$APP_DIR/venv"
SERVICE="aiaprint"
BRANCH="main"
REPO_URL="https://github.com/sleepwalker1296/tg_bot_AiAprint.git"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; }

# ── 1. Клонировать репо, если папки нет ──────────────────────
if [ ! -d "$APP_DIR/.git" ]; then
    warn "Директория $APP_DIR не является git-репозиторием."
    warn "Клонирую $REPO_URL → $APP_DIR ..."
    git clone "$REPO_URL" "$APP_DIR"
    log "Репозиторий склонирован."
fi

cd "$APP_DIR"

# ── 2. Получить обновления ────────────────────────────────────
log "Получаю обновления ветки $BRANCH..."
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
log "Код обновлён до коммита: $(git log -1 --format='%h %s')"

# ── 3. Создать venv, если отсутствует ────────────────────────
if [ ! -d "$VENV" ]; then
    warn "Виртуальное окружение не найдено, создаю..."
    python3 -m venv "$VENV"
    log "Виртуальное окружение создано."
fi

# ── 4. Установить / обновить зависимости ─────────────────────
log "Устанавливаю зависимости..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
log "Зависимости актуальны."

# ── 5. Перезапустить бота ─────────────────────────────────────
if systemctl list-units --type=service --all | grep -q "${SERVICE}.service"; then
    log "Перезапускаю systemd-сервис $SERVICE..."
    systemctl restart "$SERVICE"
    sleep 2
    if systemctl is-active --quiet "$SERVICE"; then
        log "Сервис $SERVICE запущен."
    else
        err "Сервис $SERVICE не запустился. Проверьте: journalctl -u $SERVICE -n 50"
        exit 1
    fi
else
    warn "Systemd-сервис '$SERVICE' не найден."
    warn "Чтобы настроить автозапуск, выполните:"
    warn "  sudo bash $APP_DIR/install_service.sh"
    warn ""
    warn "Перезапускаю бота вручную (pkill + запуск в фоне)..."
    pkill -f "python.*bot.py" 2>/dev/null || true
    sleep 1
    nohup "$VENV/bin/python" "$APP_DIR/bot.py" \
        >> "$APP_DIR/data/bot.log" 2>&1 &
    log "Бот запущен. PID: $!"
fi

echo ""
log "Деплой завершён успешно."

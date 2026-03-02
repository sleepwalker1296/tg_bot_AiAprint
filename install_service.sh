#!/bin/bash
# =============================================================
#  AiAprint — установка systemd-сервиса
#  Запускать один раз: sudo bash install_service.sh
# =============================================================

set -euo pipefail

APP_DIR="/opt/aiaprint"
SERVICE="aiaprint"
SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"

if [ "$EUID" -ne 0 ]; then
    echo "Запустите с sudo: sudo bash $0"
    exit 1
fi

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=AiAprint Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/bot.py
Restart=always
RestartSec=10
EnvironmentFile=$APP_DIR/.env
StandardOutput=append:$APP_DIR/data/bot.log
StandardError=append:$APP_DIR/data/bot.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

echo "Сервис $SERVICE установлен и запущен."
echo "Команды управления:"
echo "  systemctl status $SERVICE"
echo "  journalctl -u $SERVICE -f"

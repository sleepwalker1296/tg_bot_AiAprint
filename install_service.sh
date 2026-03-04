#!/bin/bash
# =============================================================
#  Устанавливает systemd-сервис для AiAprint бота
#  Запускать: sudo bash install_service.sh
# =============================================================

APP_DIR="/opt/aiaprint"
VENV="$APP_DIR/venv"
SERVICE="aiaprint"

cat > /etc/systemd/system/${SERVICE}.service << UNIT
[Unit]
Description=AiAprint Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
ExecStart=${VENV}/bin/python ${APP_DIR}/bot.py
Restart=always
RestartSec=5
StandardOutput=append:${APP_DIR}/data/bot.log
StandardError=append:${APP_DIR}/data/bot.log

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable ${SERVICE}
systemctl start ${SERVICE}

echo "Статус:"
systemctl status ${SERVICE} --no-pager

#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

cd "$(dirname "$0")"

API_PID=""
TUNNEL_PID=""
BOT_PID=""
LOG_DIR="$PWD/logs"

mkdir -p "$LOG_DIR"

cleanup() {
    echo
    echo "Останавливаю ROBO LUDOMANES..."
    for pid in "$BOT_PID" "$TUNNEL_PID" "$API_PID"; do
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    echo "Остановка завершена."
}

trap cleanup EXIT INT TERM

if [ ! -f .env ]; then
    echo "Ошибка: файл .env не найден."
    exit 1
fi

BOT_TOKEN=$(sed -n 's/^BOT_TOKEN=//p' .env | tail -n 1)
ADMIN_ID=$(sed -n 's/^ADMIN_ID=//p' .env | tail -n 1)

if [ -z "$BOT_TOKEN" ] || [[ "$BOT_TOKEN" != *:* ]]; then
    echo "Ошибка: проверь BOT_TOKEN в .env"
    exit 1
fi

if ! [[ "$ADMIN_ID" =~ ^[0-9]+$ ]] || [ "$ADMIN_ID" = "0" ]; then
    echo "Ошибка: проверь ADMIN_ID в .env"
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "Ошибка: виртуальное окружение не найдено."
    exit 1
fi

if ! command -v cloudflared >/dev/null 2>&1; then
    echo "Установи cloudflared: pkg install cloudflared"
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "Установи curl: pkg install curl"
    exit 1
fi

echo "[1/4] Запускаю локальный API..."

.venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    > "$LOG_DIR/api.log" 2>&1 &

API_PID=$!

READY=0
for i in $(seq 1 30); do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        echo "API завершился с ошибкой:"
        cat "$LOG_DIR/api.log"
        exit 1
    fi

    if curl -fsS http://127.0.0.1:8000/ >/dev/null 2>&1; then
        READY=1
        break
    fi

    sleep 1
done

if [ "$READY" -ne 1 ]; then
    echo "API не ответил за 30 секунд:"
    cat "$LOG_DIR/api.log"
    exit 1
fi

echo "[2/4] Создаю HTTPS-туннель..."

cloudflared tunnel \
    --url http://127.0.0.1:8000 \
    --no-autoupdate \
    > "$LOG_DIR/cloudflared.log" 2>&1 &

TUNNEL_PID=$!

PUBLIC_URL=""

for i in $(seq 1 40); do
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        echo "Cloudflare Tunnel завершился с ошибкой:"
        cat "$LOG_DIR/cloudflared.log"
        exit 1
    fi

    PUBLIC_URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' \
        "$LOG_DIR/cloudflared.log" | head -n 1 || true)

    if [ -n "$PUBLIC_URL" ]; then
        break
    fi

    sleep 1
done

if [ -z "$PUBLIC_URL" ]; then
    echo "Не удалось получить HTTPS-ссылку."
    cat "$LOG_DIR/cloudflared.log"
    exit 1
fi

echo "[3/4] Обновляю WEBAPP_URL..."

if grep -q '^WEBAPP_URL=' .env; then
    sed -i "s|^WEBAPP_URL=.*|WEBAPP_URL=$PUBLIC_URL|" .env
else
    echo "WEBAPP_URL=$PUBLIC_URL" >> .env
fi

echo "Mini App: $PUBLIC_URL"

echo "[4/4] Запускаю Telegram-бота..."

.venv/bin/python bot.py > "$LOG_DIR/bot.log" 2>&1 &
BOT_PID=$!

sleep 3

if ! kill -0 "$BOT_PID" 2>/dev/null; then
    echo "Бот не запустился. Последние сообщения:"
    tail -n 60 "$LOG_DIR/bot.log"
    exit 1
fi

echo
echo "======================================"
echo " ROBO LUDOMANES ЗАПУЩЕН"
echo " API: http://127.0.0.1:8000"
echo " MINI APP: $PUBLIC_URL"
echo "======================================"
echo "Для остановки нажми CTRL+C."
echo

wait "$BOT_PID"

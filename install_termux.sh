#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
pkg update -y
pkg install python nano clang libffi openssl -y
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env; fi
echo
echo "Установка завершена. Заполните .env: BOT_TOKEN, ADMIN_ID, WEBAPP_URL."
echo "Затем в двух сессиях Termux запустите: ./start_api.sh и ./start_bot.sh"

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
from fastapi import HTTPException, Header
from app.config import get_settings


def validate_init_data(init_data: str, bot_token: str, max_age: int) -> dict:
    """Validate Telegram WebApp initData according to Telegram's HMAC algorithm."""
    if not init_data or len(init_data) > 8192:
        raise HTTPException(status_code=401, detail="Нет данных авторизации Telegram")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError("duplicate keys")
        received_hash = data.pop("hash", None)
        if not received_hash:
            raise ValueError("hash missing")
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            raise ValueError("signature mismatch")
        auth_date = int(data.get("auth_date", "0"))
        now = int(time.time())
        if auth_date <= 0 or auth_date > now + 60 or now - auth_date > max_age:
            raise ValueError("initData expired")
        user = json.loads(data.get("user", "{}"))
        if not isinstance(user, dict) or not isinstance(user.get("id"), int):
            raise ValueError("user missing")
        return {"user": user, "auth_date": auth_date, "start_param": data.get("start_param", "")}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Недействительная или устаревшая авторизация Telegram") from exc


async def telegram_user(x_telegram_init_data: str | None = Header(default=None)) -> dict:
    settings = get_settings()
    if not settings.is_configured:
        raise HTTPException(status_code=503, detail="Сначала настройте BOT_TOKEN и ADMIN_ID в .env")
    return validate_init_data(x_telegram_init_data or "", settings.bot_token, settings.init_data_max_age_seconds)

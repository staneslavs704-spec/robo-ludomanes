import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from aiogram.types import LabeledPrice
from app.config import get_settings
from app.db import init_db, get_session
from app.models import User, Spin, NftClaim, NftInventory, Payment, TaskClaim
from app.security import telegram_user
from app.game import ensure_user, execute_spin
from app.schemas import SpinRequest, ClaimRequest, TaskClaimRequest

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("robo_ludomanes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.bot = Bot(settings.bot_token) if settings.is_configured else None
    app.state.bot_username = ""
    if app.state.bot:
        try:
            app.state.bot_username = (await app.state.bot.get_me()).username or ""
        except Exception:
            log.exception("Could not resolve bot username during startup")
    yield
    if app.state.bot:
        await app.state.bot.session.close()


app = FastAPI(title="ROBO LUDOMANES API", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="templates"), name="static")


@app.get("/", response_class=HTMLResponse)
async def home():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "robo-ludomanes"}


@app.get("/ready")
async def ready(session: AsyncSession = Depends(get_session)):
    await session.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}


async def current_user(
    request: Request,
    auth: dict = Depends(telegram_user),
    session: AsyncSession = Depends(get_session),
) -> User:
    tg_user = auth["user"]
    user = await ensure_user(session, tg_user, auth.get("start_param", ""))
    if user.is_blocked:
        raise HTTPException(status_code=403, detail="Аккаунт ограничен")
    request.state.telegram_user = tg_user
    return user


@app.get("/api/me")
async def me(request: Request, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    claims = (await session.execute(
        select(NftClaim, NftInventory)
        .join(NftInventory, NftInventory.id == NftClaim.nft_id)
        .where(NftClaim.user_id == user.id)
        .order_by(NftClaim.created_at.desc()).limit(20)
    )).all()
    recent = (await session.scalars(
        select(Spin).where(Spin.user_id == user.id).order_by(Spin.created_at.desc()).limit(10)
    )).all()
    bot_username = getattr(request.app.state, "bot_username", "")
    return {
        "user": {
            "id": user.id, "first_name": user.first_name, "username": user.username,
            "referrals_count": user.referrals_count, "free_spins": user.free_spins,
            "paid_spins": user.paid_spins, "quest_points": user.quest_points,
        },
        "referral_link": f"https://t.me/{bot_username}?start=ref_{user.id}" if bot_username else "",
        "stars_per_spin": settings.stars_per_spin,
        "paid_wheel_enabled": settings.paid_wheel_enabled,
        "quest_spin_cost": settings.quest_spin_cost,
        "nft_claims": [
            {"id": claim.id, "title": nft.title, "status": claim.status, "gift_url": nft.gift_url}
            for claim, nft in claims
        ],
        "history": [
            {"id": s.id, "wheel_type": s.wheel_type, "prize_type": s.prize_type,
             "title": s.prize_title, "points_awarded": s.points_awarded,
             "created_at": s.created_at.isoformat()}
            for s in recent
        ],
    }


@app.post("/api/spin")
async def spin(payload: SpinRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    try:
        result = await execute_spin(session, user.id, payload.wheel_type, payload.request_id)
        return {"ok": True, "result": result}
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/tasks")
async def tasks(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    claimed = set((await session.scalars(select(TaskClaim.task_key).where(TaskClaim.user_id == user.id))).all())
    items = []
    if settings.required_channel:
        items.append({
            "key": "subscribe_channel", "title": "Подписаться на канал",
            "description": "Подпишитесь на официальный канал проекта и подтвердите подписку.",
            "reward": settings.points_for_channel, "url": settings.channel_url,
            "claimed": "subscribe_channel" in claimed,
        })
    items.append({
        "key": "invite_friends", "title": "Пригласить друзей",
        "description": f"За каждого нового участника по вашей ссылке начисляется {settings.points_per_referral} баллов автоматически.",
        "reward": settings.points_per_referral, "url": "", "claimed": False,
    })
    return {"items": items, "quest_points": user.quest_points, "quest_spin_cost": settings.quest_spin_cost}


@app.post("/api/tasks/claim")
async def claim_task(payload: TaskClaimRequest, request: Request,
                     user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if payload.task_key != "subscribe_channel" or not settings.required_channel:
        raise HTTPException(status_code=400, detail="Это задание сейчас недоступно")
    existing = await session.scalar(select(TaskClaim).where(
        TaskClaim.user_id == user.id, TaskClaim.task_key == payload.task_key
    ))
    if existing:
        raise HTTPException(status_code=409, detail="Награда за это задание уже получена")
    bot: Bot | None = request.app.state.bot
    if not bot:
        raise HTTPException(status_code=503, detail="Бот не настроен")
    try:
        member = await bot.get_chat_member(settings.required_channel, user.id)
        is_member = member.status in {"member", "administrator", "creator"} or (member.status == "restricted" and getattr(member, "is_member", False))
        if not is_member:
            raise HTTPException(status_code=400, detail="Сначала подпишитесь на канал")
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Channel membership verification failed")
        raise HTTPException(status_code=502, detail="Не удалось проверить подписку. Проверьте права бота в канале.") from exc
    locked_user = await session.scalar(select(User).where(User.id == user.id).with_for_update())
    # Unique constraint protects against concurrent duplicate claims.
    session.add(TaskClaim(user_id=user.id, task_key=payload.task_key, points_awarded=settings.points_for_channel))
    locked_user.quest_points += settings.points_for_channel
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Задание уже засчитано") from exc
    return {"ok": True, "points_awarded": settings.points_for_channel}


@app.post("/api/stars/invoice")
async def create_stars_invoice(request: Request, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if not settings.paid_wheel_enabled:
        raise HTTPException(status_code=403, detail="Платная рулетка отключена до проверки правил проведения розыгрыша")
    bot: Bot | None = request.app.state.bot
    if not bot:
        raise HTTPException(status_code=503, detail="Бот не настроен")
    payload = f"spin:{user.id}:{uuid.uuid4().hex}"
    payment = Payment(user_id=user.id, payload=payload, amount=settings.stars_per_spin, currency="XTR", status="pending")
    session.add(payment)
    await session.commit()
    try:
        link = await bot.create_invoice_link(
            title="Прокрутка ROBO LUDOMANES",
            description="Одна прокрутка платной рулетки",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Одна прокрутка", amount=settings.stars_per_spin)],
        )
        return {"ok": True, "invoice_url": link, "stars": settings.stars_per_spin}
    except Exception as exc:
        payment.status = "failed"
        await session.commit()
        log.exception("Could not create Telegram Stars invoice")
        raise HTTPException(status_code=502, detail="Telegram не смог создать счёт. Попробуйте позже.") from exc


@app.post("/api/claim")
async def request_nft_claim(payload: ClaimRequest, request: Request,
                            user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    claim = await session.scalar(select(NftClaim).where(
        NftClaim.id == payload.claim_id, NftClaim.user_id == user.id
    ).with_for_update())
    if not claim:
        raise HTTPException(status_code=404, detail="Выигрыш не найден")
    if claim.status == "requested":
        return {"ok": True, "message": "Заявка уже отправлена администратору"}
    if claim.status != "pending":
        raise HTTPException(status_code=409, detail="Этот подарок уже обработан")
    nft = await session.get(NftInventory, claim.nft_id)
    claim.status = "requested"
    claim.requested_at = datetime.now(timezone.utc)
    await session.commit()

    bot: Bot | None = request.app.state.bot
    if bot and settings.admin_id:
        try:
            await bot.send_message(
                settings.admin_id,
                f"🎁 <b>Новая заявка на NFT-подарок</b>\n"
                f"Пользователь: <code>{user.id}</code> (@{user.username or 'без username'})\n"
                f"Подарок: <b>{nft.title if nft else 'не найден'}</b>\n"
                f"ID заявки: <code>{claim.id}</code>\n"
                f"Ссылка: {nft.gift_url if nft else '—'}",
                parse_mode="HTML",
            )
        except Exception:
            log.exception("Could not notify admin about NFT claim")
    return {"ok": True, "message": "Заявка отправлена администратору"}


@app.get("/api/history")
async def history(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Spin).where(Spin.user_id == user.id).order_by(Spin.created_at.desc()).limit(50))).all()
    return {"items": [{"id": r.id, "wheel_type": r.wheel_type, "title": r.prize_title,
                       "prize_type": r.prize_type, "created_at": r.created_at.isoformat()} for r in rows]}

import asyncio
import logging
from html import escape
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, PreCheckoutQuery, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import User, Payment, NftInventory, NftClaim, Spin
from app.game import ensure_user
from datetime import datetime, timezone

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("robo_bot")
router = Router()


@router.message(CommandStart())
async def start(message: Message, bot: Bot):
    args = message.text.split(maxsplit=1)
    start_param = args[1].strip() if len(args) > 1 else ""
    async with SessionLocal() as session:
        user = await ensure_user(session, {
            "id": message.from_user.id,
            "username": message.from_user.username,
            "first_name": message.from_user.first_name,
            "language_code": message.from_user.language_code,
        }, start_param)
    keyboard = None
    if settings.webapp_url.startswith("https://") and "example.com" not in settings.webapp_url:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚀 Открыть ROBO LUDOMANES", web_app=WebAppInfo(url=settings.webapp_url))
        ]])
    await message.answer(
        f"Привет, <b>{escape(message.from_user.first_name or 'игрок')}!</b>\n\n"
        "Добро пожаловать в ROBO LUDOMANES. Открой мини-приложение, чтобы посмотреть баланс, задания и рулетки.\n\n"
        f"Твои рефералы: <b>{user.referrals_count}</b>\n"
        f"Бесплатные прокрутки: <b>{user.free_spins}</b>",
        reply_markup=keyboard, parse_mode="HTML"
    )


@router.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "<b>ROBO LUDOMANES — помощь</b>\n"
        "/start — открыть проект\n"
        "/paysupport — поддержка по оплатам\n\n"
        "За каждого нового участника, который впервые запускает бота по твоей ссылке, начисляются баллы. "
        "За каждые несколько приглашённых выдаётся бесплатная прокрутка.",
        parse_mode="HTML"
    )


@router.message(Command("paysupport"))
async def pay_support(message: Message, bot: Bot):
    details = message.text.partition(" ")[2].strip()
    await message.answer("Обращение принято. Администратор сможет проверить платёж по вашему Telegram ID. Приложите чек или укажите дату и сумму оплаты.")
    if settings.admin_id:
        try:
            await bot.send_message(settings.admin_id,
                f"🆘 <b>Обращение по оплате</b>\nПользователь: <code>{message.from_user.id}</code>\n"
                f"Username: @{escape(message.from_user.username or 'нет')}\nСообщение: {escape((details or 'Без описания')[:1000])}",
                parse_mode="HTML")
        except Exception:
            log.exception("Could not forward paysupport message")


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, bot: Bot):
    async with SessionLocal() as session:
        payment = await session.scalar(select(Payment).where(Payment.payload == query.invoice_payload))
        valid = bool(settings.paid_wheel_enabled and payment and payment.status == "pending" and payment.user_id == query.from_user.id
                     and payment.amount == query.total_amount and query.currency == "XTR")
    await bot.answer_pre_checkout_query(
        query.id, ok=valid,
        error_message=None if valid else "Счёт недействителен или уже обработан. Создайте новый счёт."
    )


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    sp = message.successful_payment
    async with SessionLocal() as session:
        payment = await session.scalar(select(Payment).where(Payment.payload == sp.invoice_payload).with_for_update())
        if not payment:
            log.error("Payment payload not found: %s", sp.invoice_payload)
            await message.answer("Платёж получен, но заказ не найден. Напишите /paysupport.")
            return
        if payment.status != "pending":
            await message.answer("Этот платёж уже обработан или возвращён. Повторного зачисления не будет.")
            return
        if payment.user_id != message.from_user.id or payment.amount != sp.total_amount or sp.currency != "XTR":
            log.error("Payment data mismatch for payment %s", payment.id)
            await message.answer("Не удалось автоматически сопоставить платёж. Напишите /paysupport.")
            return
        user = await session.scalar(select(User).where(User.id == payment.user_id).with_for_update())
        if not user:
            await message.answer("Профиль не найден. Напишите /paysupport.")
            return
        payment.status = "paid"
        payment.charge_id = sp.telegram_payment_charge_id
        payment.paid_at = datetime.now(timezone.utc)
        user.paid_spins += 1
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            log.exception("Duplicate Stars charge detected")
            await message.answer("Платёж уже обрабатывается. При необходимости напишите /paysupport.")
            return
    await message.answer("✅ Оплата подтверждена! Одна платная прокрутка добавлена. Открой мини-приложение и обнови баланс.")


@router.message(Command("payments"))
async def list_payments(message: Message):
    if message.from_user.id != settings.admin_id:
        return
    async with SessionLocal() as session:
        rows = (await session.scalars(select(Payment).order_by(Payment.created_at.desc()).limit(15))).all()
    if not rows:
        await message.answer("Платежей пока нет.")
        return
    lines = [f"#{p.id} · user {p.user_id} · {p.amount} {p.currency} · {p.status}" for p in rows]
    await message.answer("<b>Последние платежи</b>\n" + "\n".join(lines), parse_mode="HTML")


@router.message(Command("refund"))
async def refund_payment(message: Message, bot: Bot):
    if message.from_user.id != settings.admin_id:
        return
    raw = message.text.partition(" ")[2].strip()
    if not raw.isdigit():
        await message.answer("Формат: /refund ID_ПЛАТЕЖА (ID из /payments)")
        return
    async with SessionLocal() as session:
        hint = await session.get(Payment, int(raw))
        if not hint:
            await message.answer("Платёж не найден.")
            return
        # Lock order matches paid-spin flow: user first, then payment.
        user = await session.scalar(select(User).where(User.id == hint.user_id).with_for_update())
        payment = await session.scalar(select(Payment).where(Payment.id == int(raw)).with_for_update())
        if not payment or payment.status != "paid" or not payment.charge_id:
            await message.answer("Оплаченный платёж не найден или уже обработан.")
            return
        if payment.consumed_at is not None:
            await message.answer("Эта прокрутка уже использована. Автоматический возврат заблокирован, чтобы не возвращать оплату за уже выданную услугу.")
            return
        try:
            await bot.refund_star_payment(user_id=payment.user_id, telegram_payment_charge_id=payment.charge_id)
        except Exception:
            log.exception("Telegram Stars refund failed")
            await message.answer("Telegram не подтвердил возврат. Проверьте лог и статус платежа.")
            return
        payment.status = "refunded"
        if user and user.paid_spins > 0:
            user.paid_spins -= 1
        await session.commit()
    await message.answer(f"✅ Возврат по платежу #{raw} отправлен в Telegram.")


@router.message(Command("stats"))
async def stats(message: Message):
    if message.from_user.id != settings.admin_id:
        return
    async with SessionLocal() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        spins = await session.scalar(select(func.count()).select_from(Spin))
        claims = await session.scalar(select(func.count()).select_from(NftClaim).where(NftClaim.status != "fulfilled"))
        gifts = await session.scalar(select(func.count()).select_from(NftInventory).where(NftInventory.status == "available"))
    await message.answer(f"📊 <b>Статистика</b>\nПользователей: {users}\nПрокруток: {spins}\nНезакрытых заявок: {claims}\nСвободных NFT: {gifts}", parse_mode="HTML")


@router.message(Command("addgift"))
async def add_gift(message: Message):
    if message.from_user.id != settings.admin_id:
        return
    raw = message.text.partition(" ")[2]
    parts = [x.strip() for x in raw.split("|", 2)]
    if len(parts) < 2 or not parts[0] or not parts[1].startswith("https://"):
        await message.answer("Формат:\n/addgift Название подарка | https://ссылка-на-подарок | https://ссылка-на-картинку (необязательно)")
        return
    image_url = parts[2] if len(parts) > 2 and parts[2].startswith("https://") else None
    async with SessionLocal() as session:
        gift = NftInventory(title=parts[0][:160], gift_url=parts[1], image_url=image_url)
        session.add(gift)
        await session.commit()
        await session.refresh(gift)
    await message.answer(f"✅ Подарок добавлен в пул. ID: <code>{gift.id}</code>", parse_mode="HTML")


@router.message(Command("giftstock"))
async def gift_stock(message: Message):
    if message.from_user.id != settings.admin_id:
        return
    async with SessionLocal() as session:
        rows = (await session.scalars(select(NftInventory).order_by(NftInventory.id.desc()).limit(30))).all()
    if not rows:
        await message.answer("Склад пуст. Добавить подарок: /addgift Название | https://ссылка")
        return
    text = "<b>Склад подарков</b>\n" + "\n".join(f"#{g.id} — {escape(g.title)} ({g.status})" for g in rows)
    await message.answer(text, parse_mode="HTML")


@router.message(Command("fulfill"))
async def fulfill_claim(message: Message):
    if message.from_user.id != settings.admin_id:
        return
    raw = message.text.partition(" ")[2].strip()
    if not raw.isdigit():
        await message.answer("Формат: /fulfill ID_ЗАЯВКИ")
        return
    async with SessionLocal() as session:
        claim = await session.get(NftClaim, int(raw))
        if not claim:
            await message.answer("Заявка не найдена")
            return
        claim.status = "fulfilled"
        nft = await session.get(NftInventory, claim.nft_id)
        gift_title = nft.title if nft else "NFT-подарок"
        user_id = claim.user_id
        if nft:
            nft.status = "fulfilled"
        await session.commit()
    try:
        await message.bot.send_message(user_id, f"🎁 Твоя заявка #{raw} обработана. Подарок: {escape(gift_title)}. Администратор завершил выдачу.", parse_mode="HTML")
    except Exception:
        log.exception("Could not notify user about fulfilled NFT claim")
    await message.answer(f"✅ Заявка #{raw} отмечена как выполненная.")


async def main():
    if not settings.is_configured:
        raise RuntimeError("Заполните BOT_TOKEN и ADMIN_ID в .env")
    await init_db()
    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    log.info("ROBO LUDOMANES bot started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

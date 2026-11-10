import secrets
from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.models import User, Spin, NftInventory, NftClaim, Payment
from app.config import get_settings

settings = get_settings()


async def ensure_user(session: AsyncSession, tg_user: dict, start_param: str = "") -> User:
    user_id = int(tg_user["id"])
    user = await session.get(User, user_id)
    if user:
        user.username = tg_user.get("username")
        user.first_name = tg_user.get("first_name") or user.first_name
        user.language_code = tg_user.get("language_code")
        await session.commit()
        return user

    referrer_id = None
    if start_param.startswith("ref_"):
        try:
            candidate = int(start_param[4:])
            if candidate != user_id and await session.get(User, candidate):
                referrer_id = candidate
        except (TypeError, ValueError):
            pass
    user = User(
        id=user_id,
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name") or "Игрок",
        language_code=tg_user.get("language_code"),
        referred_by=referrer_id,
    )
    session.add(user)
    try:
        if referrer_id:
            referrer = await session.get(User, referrer_id, with_for_update=True)
            if referrer:
                referrer.referrals_count += 1
                referrer.quest_points += settings.points_per_referral
                eligible = referrer.referrals_count // max(1, settings.referrals_per_free_spin)
                bonus = eligible - referrer.free_spins_granted
                if bonus > 0:
                    referrer.free_spins += bonus
                    referrer.free_spins_granted += bonus
        await session.commit()
    except IntegrityError:
        await session.rollback()
        user = await session.get(User, user_id)
        if user:
            return user
        raise
    return user


async def execute_spin(session: AsyncSession, user_id: int, wheel_type: str, request_id: str) -> dict:
    # Idempotency: retrying the same request returns the original result without another debit.
    existing = await session.scalar(select(Spin).where(Spin.user_id == user_id, Spin.request_id == request_id))
    if existing:
        claim_id = None
        if existing.nft_id:
            claim = await session.scalar(select(NftClaim).where(NftClaim.spin_id == existing.id))
            claim_id = claim.id if claim else None
        return _spin_result(existing, claim_id, replay=True)

    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user or user.is_blocked:
        raise ValueError("Профиль недоступен")
    # A second check after taking the user-row lock serializes duplicate concurrent requests.
    existing = await session.scalar(select(Spin).where(Spin.user_id == user_id, Spin.request_id == request_id))
    if existing:
        claim_id = None
        if existing.nft_id:
            claim = await session.scalar(select(NftClaim).where(NftClaim.spin_id == existing.id))
            claim_id = claim.id if claim else None
        return _spin_result(existing, claim_id, replay=True)
    payment_credit = None
    if wheel_type == "free":
        if user.free_spins < 1:
            raise ValueError("Нет бесплатных прокруток. Пригласите друзей.")
        user.free_spins -= 1
    elif wheel_type == "paid":
        if not settings.paid_wheel_enabled:
            raise ValueError("Платная рулетка временно отключена")
        if user.paid_spins < 1:
            raise ValueError("Сначала приобретите платную прокрутку за Telegram Stars.")
        payment_credit = await session.scalar(
            select(Payment).where(
                Payment.user_id == user_id, Payment.status == "paid", Payment.consumed_at.is_(None)
            ).order_by(Payment.created_at).limit(1).with_for_update()
        )
        if not payment_credit:
            raise ValueError("Не удалось сопоставить купленную попытку с платежом. Обратитесь в поддержку.")
        payment_credit.consumed_at = datetime.now(timezone.utc)
        user.paid_spins -= 1
    elif wheel_type == "quest":
        if user.quest_points < settings.quest_spin_cost:
            raise ValueError(f"Для этой рулетки нужно {settings.quest_spin_cost} баллов.")
        user.quest_points -= settings.quest_spin_cost
    else:
        raise ValueError("Неизвестная рулетка")

    # Original game balance: 56% empty, 40% points, 4% NFT (only if stock exists).
    nft_count = await session.scalar(select(func.count(NftInventory.id)).where(NftInventory.status == "available"))
    outcomes = [("empty", "В этот раз без приза", 56),
                ("points", "10 баллов", 20),
                ("points", "25 баллов", 15),
                ("points", "50 баллов", 5)]
    if nft_count:
        outcomes.append(("nft", "NFT-подарок", 4))
    prize_type, title, weight = secrets.SystemRandom().choices(outcomes, weights=[x[2] for x in outcomes], k=1)[0]
    points = 0
    nft_id = None
    if prize_type == "points":
        points = int(title.split()[0])
        user.quest_points += points
    elif prize_type == "nft":
        # Lock only the selected inventory row, not the whole stock, to reduce contention.
        offset = secrets.randbelow(max(1, nft_count))
        nft = await session.scalar(
            select(NftInventory).where(NftInventory.status == "available")
            .order_by(NftInventory.id).offset(offset).limit(1).with_for_update(skip_locked=True)
        )
        if not nft:
            nft = await session.scalar(
                select(NftInventory).where(NftInventory.status == "available")
                .order_by(NftInventory.id).limit(1).with_for_update(skip_locked=True)
            )
        if nft:
            nft.status = "reserved"
            nft_id = nft.id
            title = nft.title
        else:
            # All stock may have been reserved by concurrent winners.
            prize_type, title, points = "empty", "В этот раз без приза", 0

    spin = Spin(user_id=user_id, request_id=request_id, wheel_type=wheel_type,
                prize_type=prize_type, prize_title=title, points_awarded=points, nft_id=nft_id,
                payment_id=payment_credit.id if payment_credit else None)
    session.add(spin)
    await session.flush()
    claim_id = None
    if nft_id:
        claim = NftClaim(user_id=user_id, spin_id=spin.id, nft_id=nft_id)
        session.add(claim)
        await session.flush()
        claim_id = claim.id
    await session.commit()
    return _spin_result(spin, claim_id, replay=False)


def _spin_result(spin: Spin, claim_id: int | None, replay: bool) -> dict:
    return {
        "spin_id": spin.id,
        "wheel_type": spin.wheel_type,
        "prize_type": spin.prize_type,
        "title": spin.prize_title,
        "points_awarded": spin.points_awarded,
        "claim_id": claim_id,
        "replayed": replay,
    }

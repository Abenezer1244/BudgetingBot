from datetime import date, datetime, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from .db import Txn, Budget, Freeze, WeeklyCap

def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"

async def spent_by(session: AsyncSession, month: str, category: str, parent: str|None):
    q = await session.execute(
        select(func.sum(Txn.amount)).where(
            Txn.type=="Expense", Txn.month==month, Txn.category==category, Txn.parent==parent
        )
    )
    return float(q.scalar() or 0.0)

async def budget_left(session: AsyncSession, month: str):
    res = {}
    q = await session.execute(select(Budget).where(Budget.month==month))
    for b in q.scalars().all():
        spent = await spent_by(session, month, b.category, b.parent)
        res[(b.category, b.parent or "")] = (b.limit_amount, spent, b.limit_amount - spent)
    return res

async def add_or_update_budget(session: AsyncSession, month: str, category: str, parent: str|None, limit: float):
    q = await session.execute(select(Budget).where(Budget.month==month, Budget.category==category, Budget.parent==parent))
    b = q.scalars().first()
    if not b:
        b = Budget(month=month, category=category, parent=parent, limit_amount=limit)
        session.add(b)
    else:
        b.limit_amount = limit
    await session.commit()
    return b

# Weekly caps helpers
def week_range(d: date):
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return start, end

async def weekly_spent(session: AsyncSession, d: date, category: str, parent: str|None):
    start, end = week_range(d)
    q = await session.execute(
        select(func.sum(Txn.amount)).where(
            Txn.type=="Expense",
            Txn.category==category,
            Txn.parent==parent,
            Txn.occurred_at >= start,
            Txn.occurred_at <= end
        )
    )
    return float(q.scalar() or 0.0)

async def set_weekly_cap(session: AsyncSession, category: str, parent: str|None, cap: float):
    q = await session.execute(select(WeeklyCap).where(WeeklyCap.category==category, WeeklyCap.parent==parent))
    w = q.scalars().first()
    if not w:
        w = WeeklyCap(category=category, parent=parent, cap_amount=cap)
        session.add(w)
    else:
        w.cap_amount = cap
    await session.commit()
    return w

async def get_weekly_caps(session: AsyncSession):
    q = await session.execute(select(WeeklyCap))
    return q.scalars().all()

async def get_weekly_cap(session: AsyncSession, category: str, parent: str|None):
    q = await session.execute(select(WeeklyCap).where(WeeklyCap.category==category, WeeklyCap.parent==parent))
    return q.scalars().first()

async def is_frozen(session: AsyncSession, category: str, parent: str|None) -> bool:
    q = await session.execute(select(Freeze).where(Freeze.category==category, Freeze.parent==parent, Freeze.active==True))
    return q.scalars().first() is not None

async def set_freeze(session: AsyncSession, category: str, parent: str|None, active: bool):
    q = await session.execute(select(Freeze).where(Freeze.category==category, Freeze.parent==parent))
    f = q.scalars().first()
    if not f:
        f = Freeze(category=category, parent=parent, active=active)
        session.add(f)
    else:
        f.active = active
    await session.commit()
    return f

def burn_rate_warning(today: date, month_limit: float, spent: float) -> str:
    if month_limit <= 0:
        return ""
    next_month = today.replace(day=28) + timedelta(days=4)
    last_day = (next_month - timedelta(days=next_month.day)).day
    pace_allowed = month_limit * (today.day / last_day)
    if spent > pace_allowed * 1.1:
        return " ⏳ You’re spending faster than pace for this envelope."
    if spent >= month_limit * 0.8:
        return " ⚠️ 80% of monthly budget used."
    return ""

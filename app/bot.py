import os, asyncio, csv, io, textwrap, datetime as dt, logging, json, re
from datetime import datetime, date, timedelta, time
from typing import Optional
from sqlalchemy import text
BOT_LOCK_KEY = int(os.getenv("BOT_LOCK_KEY", "728431"))

import sentry_sdk
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
)

from .db import init_db, SessionLocal, User, Txn, Budget
from .parser import parse_message
from .budget import (
    month_of, budget_left, add_or_update_budget, is_frozen, set_freeze, spent_by,
    burn_rate_warning, set_weekly_cap, weekly_spent, get_weekly_caps, get_weekly_cap, week_range
)
from .utils import current_month, money, to_excel_bytes
from . import sheets_sync
from .reports import build_weekly_pdf
from .emailer import send_email_with_pdf

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DEFAULT_TZ = os.getenv("TZ", "UTC")
DEFAULT_CURRENCY = os.getenv("CURRENCY", "USD")
DAILY_REMINDER_HOUR = int(os.getenv("DAILY_REMINDER_HOUR", "21"))
WEEKLY_DIGEST_DOW = int(os.getenv("WEEKLY_DIGEST_DOW", "6"))  # 0=Mon ... 6=Sun
WEEKLY_DIGEST_HOUR = int(os.getenv("WEEKLY_DIGEST_HOUR", "19"))
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
REPORT_EMAIL_TO = os.getenv("REPORT_EMAIL_TO", "").strip()

# Optional: alias map to shorten typing, e.g.
# ALIAS_MAP='{"g":"Groceries","f.d":"Food;sub=DiningOut","tr":"Transport"}'
try:
    ALIAS_MAP = json.loads(os.getenv("ALIAS_MAP", "{}"))
except Exception:
    ALIAS_MAP = {}

if SENTRY_DSN:
    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.0)

LOG = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
async def ensure_user(tg_id: int, name: str | None, chat_id: int | None = None):
    """Create or update the user record and return a simple object with fields."""
    async with SessionLocal() as s:
        q = await s.execute(User.__table__.select().where(User.tg_id == tg_id))
        r = q.mappings().first()
        if r is None:
            u = User(
                tg_id=tg_id,
                name=name,
                tz=DEFAULT_TZ,
                currency=DEFAULT_CURRENCY,
                daily_reminders=False,
                last_chat_id=chat_id,
            )
            s.add(u)
            await s.commit()
            return u
        else:
            await s.execute(
                User.__table__
                .update()
                .where(User.tg_id == tg_id)
                .values(last_chat_id=chat_id or r["last_chat_id"])
            )
            await s.commit()
            q2 = await s.execute(User.__table__.select().where(User.tg_id == tg_id))
            r2 = q2.mappings().first()

            class U: ...
            u = U()
            u.id = r2["id"]
            u.tg_id = r2["tg_id"]
            u.name = r2["name"]
            u.tz = r2["tz"]
            u.currency = r2["currency"]
            u.daily_reminders = r2["daily_reminders"]
            u.last_chat_id = r2["last_chat_id"]
            return u

async def reply_md(update: Update, text: str):
    await update.effective_chat.send_message(text, parse_mode="Markdown")

# ------------------------------------------------------------------------------
# Shorthand normalizer
# ------------------------------------------------------------------------------
# Lets you type:
#   12 burrito #Food/DiningOut     (or #Food:DiningOut or #Food>DiningOut)
#   12 burrito #g                  (if ALIAS_MAP has {"g": "Groceries"})
#   12 burrito #f.d                (if ALIAS_MAP has {"f.d": "Food;sub=DiningOut"})
#   12 burrito                     (no #... -> reuses last category you used)
#   +200 tutoring                  (no #... -> defaults to #OtherIncome unless you have a last income category)
async def apply_shorthand(raw_text: str, user_id: int) -> str:
    t = (raw_text or "").strip()
    is_income = t.lstrip().startswith("+")

    def _alias_and_sep(token: str) -> str:
        key = token.lower()
        mapped = ALIAS_MAP.get(key)
        token2 = mapped if mapped else token  # may be "Food" or "Food;sub=DiningOut"
        if ";sub=" not in token2:
            for sep in ("/", ":", ">"):
                if sep in token2:
                    cat, sub = token2.split(sep, 1)
                    token2 = f"{cat};sub={sub}"
                    break
        return token2

    def _repl(m: re.Match) -> str:
        body = m.group(1)
        out = _alias_and_sep(body)
        return "#" + out

    # 1) expand aliases & convert separators to ;sub=
    t2 = re.sub(r"#([^\s]+)", _repl, t)

    # 2) if still no category tag, reuse last one or pick default
    if "#" not in t2:
        async with SessionLocal() as s:
            q = await s.execute(
                Txn.__table__
                .select()
                .where(Txn.user_tg_id == user_id)
                .order_by(Txn.id.desc())
                .limit(1)
            )
            r = q.mappings().first()
        if r:
            cat = r["category"]; sub = r["parent"]
        else:
            if is_income:
                cat, sub = "OtherIncome", None
            else:
                cat, sub = "Uncategorized", None
        t2 += f" #{cat}" + (f";sub={sub}" if sub else "")

    # 3) ensure OtherIncome default if income without specific category originally
    if is_income and "#OtherIncome" not in t2 and "#" not in t:
        t2 += " #OtherIncome"

    return t2

# ------------------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "*Budget Bot — Quick Reference*\n\n"
        "*Fast logging*\n"
        "• Expense: `12 coffee #Food;sub=DiningOut`\n"
        "• Income: `+200 tutoring #OtherIncome`\n"
        "• Split evenly: `40 groceries #Food #Household`\n\n"
        "*Shorter typing*\n"
        "• Slash/colon/arrow: `12 burrito #Food/DiningOut` (also `#Food:DiningOut` or `#Food>DiningOut`)\n"
        "• Aliases (`ALIAS_MAP` env): `12 burrito #g` → `Groceries`, `12 burger #f.d` → `Food;sub=DiningOut`\n"
        "• Omit category to reuse last one: `12 burrito`\n"
        "• Income without category: `+200 tutoring` → `#OtherIncome`\n\n"
        "*Budgets & caps*\n"
        "• Set monthly: `/setbudget Food 300` or `/setbudget Food ;sub=DiningOut 120`\n"
        "• Monthly left: `/left`\n"
        "• Set weekly cap: `/setweekly Food 60`\n"
        "• Weekly left: `/weeklyleft`\n\n"
        "*Freezes*\n"
        "• On/off: `/freeze add Food;sub=DiningOut` / `/freeze off Food;sub=DiningOut`\n\n"
        "*History & edits*\n"
        "• Last 10: `/history` (tap Delete)\n"
        "• Undo last: `/undo`\n"
        "• Edit: `/edit <id> [amount=..] [note=\"...\"] [#Category] [;sub=Sub]`\n\n"
        "*Sheets & reports*\n"
        "• Status: `/sheets_status`\n"
        "• Bootstrap Sheet: `/bootstrap_sheet BudgetBot Sheet` (then set `GOOGLE_SHEET_ID`)\n"
        "• Weekly PDF now: `/report_pdf`\n"
        "• Export CSV: `/export [YYYY-MM-DD YYYY-MM-DD]`\n"
        "• Export Excel: `/export_to_excel`\n"
    )
    await reply_md(update, txt)

async def acquire_single_instance_lock() -> bool:
    """Use a Postgres advisory lock so only one bot process runs."""
    async with SessionLocal() as s:
        res = await s.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": BOT_LOCK_KEY})
        got = res.scalar()
        return bool(got)



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update.effective_user.id, update.effective_user.full_name, update.effective_chat.id)
    text = (
        "Welcome to *Budget Bot* v3.2 — Auto Sheets + Weekly PDF 📈\n\n"
        "Type `/help` any time for the quick reference.\n\n"
        "Fast log examples:\n"
        "• `12 coffee #Food/DiningOut`\n"
        "• `+200 tutoring` (auto `#OtherIncome`)\n"
        "• `12 burrito` (reuses last category)\n"
    )
    await reply_md(update, text)

async def sheets_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = sheets_sync.ping_status()
    await reply_md(update, status)

async def bootstrap_sheet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = " ".join(context.args) if context.args else None
    try:
        sid = sheets_sync.bootstrap_sheet(title)
        await reply_md(update, f"✅ Sheet ready. ID: `{sid}`. Set `GOOGLE_SHEET_ID={sid}` in your env (if not already).")
    except Exception as e:
        await reply_md(update, f"⚠️ Bootstrap failed: {e}")

async def setbudget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await reply_md(update, "Usage: `/setbudget Food 300` or `/setbudget Food ;sub=DiningOut 120`")
        return
    text = " ".join(context.args)
    parts = text.rsplit(" ", 1)
    if len(parts) != 2:
        await reply_md(update, "Please end with the amount, e.g., `Food 300`")
        return
    cat_part, amt_part = parts
    amt = float(amt_part)
    parent = None
    if ";sub=" in cat_part or ";g=" in cat_part:
        if ";sub=" in cat_part:
            cat, parent = [p.strip() for p in cat_part.split(";sub=")]
        else:
            cat, parent = [p.strip() for p in cat_part.split(";g=")]
    else:
        cat = cat_part.strip()
    async with SessionLocal() as s:
        month = current_month()
        await add_or_update_budget(s, month, cat, parent, amt)
    try:
        sheets_sync.upsert_budget(month, cat, parent, amt, group_guess="")
    except Exception as e:
        LOG.exception("Budget sync failed: %s", e)
    await reply_md(update, f"Budget set for *{cat}*{(' › '+parent) if parent else ''}: `{amt:,.2f}` in {current_month()}")

async def left_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        month = current_month()
        res = await budget_left(s, month)
        if not res:
            await reply_md(update, "No budgets set. Use `/setbudget <Category> [;sub=Sub] <Amount>`")
            return
        lines = [f"*Left ({month})*"]
        for (cat, parent), (limit, spent, left) in sorted(res.items(), key=lambda kv: kv[1][2]):
            label = f"{cat}" + (f" › {parent}" if parent else "")
            warn = burn_rate_warning(dt.date.today(), limit, spent) if limit > 0 else ""
            lines.append(f"- {label}: {limit - spent:,.2f}{warn}")
        await reply_md(update, "\n".join(lines))

async def weeklyleft_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = dt.date.today()
    start, end = week_range(today)
    async with SessionLocal() as s:
        caps = await get_weekly_caps(s)
        if not caps:
            await reply_md(update, "No weekly caps set. Use `/setweekly <Category> [;sub=Sub] <Amount>`")
            return
        lines = [f"*Weekly Left* ({start} → {end})"]
        for c in caps:
            spent = await weekly_spent(s, today, c.category, c.parent)
            left = c.cap_amount - spent
            label = f"{c.category}" + (f" › {c.parent}" if c.parent else "")
            warn = " 🔴 cap hit" if left <= 0 else (" ⚠️ 80%+" if spent >= 0.8 * c.cap_amount and c.cap_amount > 0 else "")
            lines.append(f"- {label}: {left:,.2f}{warn}")
        await reply_md(update, "\n".join(lines))

async def setweekly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await reply_md(update, "Usage: `/setweekly Food 60` or `/setweekly Food ;sub=DiningOut 30`")
        return
    text = " ".join(context.args)
    parts = text.rsplit(" ", 1)
    if len(parts) != 2:
        await reply_md(update, "Please end with the amount, e.g., `Food 60`")
        return
    cat_part, amt_part = parts
    cap = float(amt_part)
    parent = None
    if ";sub=" in cat_part or ";g=" in cat_part:
        if ";sub=" in cat_part:
            cat, parent = [p.strip() for p in cat_part.split(";sub=")]
        else:
            cat, parent = [p.strip() for p in cat_part.split(";g=")]
    else:
        cat = cat_part.strip()
    async with SessionLocal() as s:
        await set_weekly_cap(s, cat, parent, cap)
    try:
        sheets_sync.upsert_weeklycap(cat, parent, cap)
    except Exception as e:
        LOG.exception("WeeklyCap sync failed: %s", e)
    await reply_md(update, f"Weekly cap set for *{cat}*{(' › '+parent) if parent else ''}: `{cap:,.2f}`")

# ------------------------------------------------------------------------------
# Freeze commands synced to Sheets
# ------------------------------------------------------------------------------
async def freeze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await reply_md(update, "Usage: `/freeze add Food;sub=DiningOut` | `/freeze off Food;sub=DiningOut` | `/freeze list`")
        return
    sub = context.args[0].lower()
    if sub == "list":
        await reply_md(update, "Use your Sheets 'Freezes' tab as source of truth. (Listing via DB not implemented here to keep it simple.)")
        return
    # parse cat;sub=
    cat_part = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    parent = None
    if ";sub=" in cat_part or ";g=" in cat_part:
        if ";sub=" in cat_part:
            cat, parent = [p.strip() for p in cat_part.split(";sub=")]
        else:
            cat, parent = [p.strip() for p in cat_part.split(";g=")]
    else:
        cat = cat_part.strip()
    active = True if sub == "add" else False
    async with SessionLocal() as s:
        await set_freeze(s, cat, parent, active)
    try:
        sheets_sync.upsert_freeze(cat, parent, active)
    except Exception as e:
        LOG.exception("Freeze sync failed: %s", e)
    await reply_md(update, f"Freeze {'ON' if active else 'OFF'} for *{cat}*{(' › '+parent) if parent else ''}")

# ------------------------------------------------------------------------------
# Export
# ------------------------------------------------------------------------------
async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start, end = None, None
    if len(context.args) == 2:
        start, end = context.args[0], context.args[1]
    else:
        today = dt.date.today()
        start = f"{today.year:04d}-{today.month:02d}-01"
        end = today.isoformat()
    async with SessionLocal() as s:
        q = await s.execute(Txn.__table__.select().where(Txn.user_tg_id == update.effective_user.id))
        rows = [dict(r) for r in q.mappings().all()]
    rows = [r for r in rows if start <= str(r["occurred_at"]) <= end]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["Date", "Month", "Type", "Amount", "Currency", "Category", "Sub-Category", "Note"])
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "Date": r["occurred_at"],
            "Month": r["month"],
            "Type": r["type"],
            "Amount": r["amount"],
            "Currency": r["currency"],
            "Category": r["category"],
            "Sub-Category": r["parent"] or "",
            "Note": r["note"] or "",
        })
    await update.effective_chat.send_document(document=output.getvalue().encode("utf-8"), filename="transactions.csv")

async def export_excel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        q = await s.execute(Txn.__table__.select().where(Txn.user_tg_id == update.effective_user.id))
        rows = [dict(r) for r in q.mappings().all()]
    rows = [{
        "Date": r["occurred_at"].isoformat() if hasattr(r["occurred_at"], "isoformat") else str(r["occurred_at"]),
        "Month": r["month"],
        "Type": r["type"],
        "Amount": r["amount"],
        "Currency": r["currency"],
        "Category": r["category"],
        "Sub-Category": r["parent"] or "",
        "Note": r["note"] or ""
    } for r in rows]
    xbytes = to_excel_bytes(rows)
    await update.effective_chat.send_document(document=xbytes, filename="transactions.xlsx")

# ------------------------------------------------------------------------------
# History / Undo / Edit
# ------------------------------------------------------------------------------
async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        q = await s.execute(
            Txn.__table__.select()
            .where(Txn.user_tg_id == update.effective_user.id)
            .order_by(Txn.id.desc())
            .limit(10)
        )
        rows = [dict(r) for r in q.mappings().all()]
    if not rows:
        await reply_md(update, "No transactions yet.")
        return
    lines = ["*Last 10*"]
    buttons = []
    for r in rows:
        label = (
            f"#{r['id']} {r['occurred_at']} {r['type']} {r['amount']:,.2f} {r['category']}"
            + (f" › {r['parent']}" if r['parent'] else "")
            + (f" — _{r['note'] or ''}_" if r['note'] else "")
        )
        lines.append(label)
        buttons.append([InlineKeyboardButton(f"Delete #{r['id']}", callback_data=f"DEL:{r['id']}")])
    await update.effective_chat.send_message("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with SessionLocal() as s:
        q = await s.execute(
            Txn.__table__.select()
            .where(Txn.user_tg_id == update.effective_user.id)
            .order_by(Txn.id.desc())
            .limit(1)
        )
        r = q.mappings().first()
        if not r:
            await reply_md(update, "Nothing to undo.")
            return
        tid = r["id"]
        await s.execute(Txn.__table__.delete().where(Txn.id == tid))
        await s.commit()
    await reply_md(update, f"Undid transaction #{tid} ✅")

async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await reply_md(update, 'Usage: `/edit <id> [amount=..] [note="..."] [#Category] [;sub=Sub]`')
        return
    tid = int(context.args[0])
    rest = " ".join(context.args[1:])
    m_amt = re.search(r"amount=([0-9]+(?:\.[0-9]{1,2})?)", rest)
    m_note = re.search(r'note="([^"]*)"', rest)
    try:
        parsed = parse_message("0 " + rest)
        cat, sub = parsed["categories"][0]
    except Exception:
        cat = None; sub = None
    async with SessionLocal() as s:
        updates = {}
        if m_amt: updates["amount"] = float(m_amt.group(1))
        if m_note: updates["note"] = m_note.group(1)
        if cat: updates["category"] = cat
        if sub is not None: updates["parent"] = sub
        if not updates:
            await reply_md(update, "No changes parsed.")
            return
        await s.execute(
            Txn.__table__
            .update()
            .where(Txn.id == tid, Txn.user_tg_id == update.effective_user.id)
            .values(**updates)
        )
        await s.commit()
    await reply_md(update, f"Updated transaction #{tid} ✅")

# ------------------------------------------------------------------------------
# Logging handler with auto Sheets sync
# ------------------------------------------------------------------------------
async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE, forced_text: Optional[str] = None, bypass_caps: bool = False):
    raw = forced_text if forced_text is not None else (update.message.text or "").strip()
    # Apply shorthand/aliases/default category
    text = await apply_shorthand(raw, update.effective_user.id)

    try:
        parsed = parse_message(text)
    except Exception as e:
        await reply_md(update, f"⚠️ {e}")
        return

    cats = parsed["categories"]
    n = len(cats)
    amounts = [parsed["amount"] / n] * n
    msgs = []
    today = parsed["date"]
    month = f"{today.year:04d}-{today.month:02d}"
    rows_for_sheet = []

    async with SessionLocal() as s:
        await ensure_user(update.effective_user.id, update.effective_user.full_name, update.effective_chat.id)

        # Weekly caps (soft)
        if not bypass_caps and parsed["type"] == "Expense":
            for idx, (cat, sub) in enumerate(cats):
                cap = await get_weekly_cap(s, cat, sub)
                if cap and cap.cap_amount > 0:
                    spent = await weekly_spent(s, today, cat, sub)
                    new_total = spent + amounts[idx]
                    if new_total >= cap.cap_amount:
                        await reply_md(update, f"🔒 Weekly cap for *{cat}*{(' › '+sub) if sub else ''} will be exceeded. Use `/override {raw}` to log anyway.")
                        return
                    elif new_total >= 0.8 * cap.cap_amount:
                        msgs.append(f"⚠️ Weekly 80% reached for *{cat}*{(' › '+sub) if sub else ''}.")

        # Insert and queue
        for idx, (cat, sub) in enumerate(cats):
            t = Txn(
                user_tg_id=update.effective_user.id,
                occurred_at=today,
                month=month,
                type=parsed["type"],
                amount=float(amounts[idx]),
                currency=os.getenv("CURRENCY", "USD"),
                category=cat,
                parent=sub,
                note=parsed["note"],
            )
            s.add(t)
            rows_for_sheet.append({
                "Date": today.isoformat(),
                "Month": month,
                "Type": parsed["type"],
                "Amount": float(amounts[idx]),
                "Currency": os.getenv("CURRENCY", "USD"),
                "Category": cat,
                "Sub-Category": sub or "",
                "Note": parsed["note"] or ""
            })
        await s.commit()

        # Envelope warnings
        for idx, (cat, sub) in enumerate(cats):
            if parsed["type"] != "Expense":
                continue
            q = await s.execute(Budget.__table__.select().where(Budget.month == month, Budget.category == cat, Budget.parent == sub))
            r = q.mappings().first()
            warn = ""
            if r and r["limit_amount"] > 0:
                spent = await spent_by(s, month, cat, sub)
                ratio = spent / r["limit_amount"] if r["limit_amount"] else 0
                if ratio >= 1.0:
                    warn = " 🔴 *Budget hit!* Consider a short freeze."
                elif ratio >= 0.8:
                    warn = " ⚠️ *80% reached.*"
                warn2 = burn_rate_warning(today, r["limit_amount"], spent)
            else:
                warn2 = ""
            label = f"{cat}" + (f" › {sub}" if sub else "")
            msgs.append(f"Logged `{amounts[idx]:,.2f}` {parsed['type']} — *{label}*  _{parsed['note'] or ''}_\n{warn}{warn2}")

    # Sheets sync (best-effort)
    try:
        sheets_sync.append_transactions(rows_for_sheet)
    except Exception as e:
        LOG.exception("Sheets append failed: %s", e)

    await reply_md(update, "\n".join(msgs))

async def override_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await reply_md(update, "Usage: `/override <original message>`")
        return
    msg = " ".join(context.args)
    await handle_free_text(update, context, msg, bypass_caps=True)

# ------------------------------------------------------------------------------
# PDF report
# ------------------------------------------------------------------------------
async def report_pdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    month = current_month()
    pdf_bytes = await build_weekly_pdf(month)
    await update.effective_chat.send_document(document=pdf_bytes, filename=f"weekly_report_{month}.pdf")
    if REPORT_EMAIL_TO and os.getenv("SENDGRID_API_KEY", "").strip():
        try:
            send_email_with_pdf(REPORT_EMAIL_TO, f"BudgetBot Weekly Report — {month}", "<p>Attached is your weekly report.</p>", pdf_bytes, filename=f"weekly_report_{month}.pdf")
        except Exception as e:
            LOG.exception("Email send failed: %s", e)

# ------------------------------------------------------------------------------
# Callback handler
# ------------------------------------------------------------------------------
async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    if data.startswith("DEL:"):
        tid = int(data.split(":")[1])
        async with SessionLocal() as s:
            await s.execute(Txn.__table__.delete().where(Txn.id == tid, Txn.user_tg_id == update.effective_user.id))
            await s.commit()
        await query.edit_message_text(f"Deleted transaction #{tid} ✅")

# ------------------------------------------------------------------------------
# Reminders & weekly PDF
# ------------------------------------------------------------------------------
async def daily_checkin(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    await context.bot.send_message(
        chat_id,
        "Daily check-in: log anything? `12 coffee #Food` or `+200 tutoring #OtherIncome`",
        parse_mode="Markdown",
    )

async def weekly_pdf_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    month = current_month()
    pdf_bytes = await build_weekly_pdf(month)
    await context.bot.send_document(
        chat_id=chat_id,
        document=pdf_bytes,
        filename=f"weekly_report_{month}.pdf",
        caption="Weekly report",
    )
    if REPORT_EMAIL_TO and os.getenv("SENDGRID_API_KEY", "").strip():
        try:
            send_email_with_pdf(
                REPORT_EMAIL_TO,
                f"BudgetBot Weekly Report — {month}",
                "<p>Attached is your weekly report.</p>",
                pdf_bytes,
                filename=f"weekly_report_{month}.pdf",
            )
        except Exception as e:
            LOG.exception("Email send failed: %s", e)

async def restore_jobs(app):
    """Schedule daily check-ins and weekly PDFs for users who enabled reminders."""
    async with SessionLocal() as s:
        q = await s.execute(User.__table__.select().where(User.daily_reminders == True))
        for r in q.mappings().all():
            chat_id = r.get("last_chat_id")
            if not chat_id:
                continue
            # Daily reminder
            app.job_queue.run_daily(
                daily_checkin,
                time=time(hour=DAILY_REMINDER_HOUR, minute=0),
                chat_id=chat_id,
            )
            # Weekly PDF on configured DOW
            app.job_queue.run_daily(
                weekly_pdf_job,
                time=time(hour=WEEKLY_DIGEST_HOUR, minute=0),
                days=(WEEKLY_DIGEST_DOW,),
                chat_id=chat_id,
            )

# ------------------------------------------------------------------------------
# Post-init hook: clear webhook & restore jobs
# ------------------------------------------------------------------------------
async def after_init(app):
    # Clear any old webhook & drop queued updates to avoid conflicts with polling
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        LOG.info("Webhook deleted & pending updates dropped.")
    except Exception as e:
        LOG.warning("delete_webhook failed: %s", e)
    # Re-schedule jobs inside PTB's loop
    await restore_jobs(app)

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
def main():
    if not TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in environment.")

    # Ensure DB schema exists
    asyncio.run(init_db())

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(after_init)   # important: run inside PTB loop
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sheets_status", sheets_status_cmd))
    app.add_handler(CommandHandler("bootstrap_sheet", bootstrap_sheet_cmd))
    app.add_handler(CommandHandler("setbudget", setbudget_cmd))
    app.add_handler(CommandHandler("left", left_cmd))
    app.add_handler(CommandHandler("weeklyleft", weeklyleft_cmd))
    app.add_handler(CommandHandler("setweekly", setweekly_cmd))
    app.add_handler(CommandHandler("freeze", freeze_cmd))
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CommandHandler("export_to_excel", export_excel_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("undo", undo_cmd))
    app.add_handler(CommandHandler("edit", edit_cmd))
    app.add_handler(CommandHandler("override", override_cmd))
    app.add_handler(CommandHandler("report_pdf", report_pdf_cmd))

    # Callback buttons
    app.add_handler(CallbackQueryHandler(cb_handler))

    # Free-text logging
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))

    # Py 3.13: make sure a loop exists (defensive)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app.run_polling()

    asyncio.run(init_db())

    # Ensure only one instance runs
    if not asyncio.run(acquire_single_instance_lock()):
        LOG.error("Another BudgetBot instance already holds the DB lock; exiting.")
        return



if __name__ == "__main__":
    main()

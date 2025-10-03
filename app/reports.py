import io
from datetime import date
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from reportlab.lib import colors
from sqlalchemy import select, func
from .db import SessionLocal, Txn, Budget
from .budget import spent_by

async def build_weekly_pdf(month: str) -> bytes:
    """
    Simple 1-page weekly snapshot: totals + top categories + envelope status for current month.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    y = height - 1*inch
    c.setFont("Helvetica-Bold", 16)
    c.drawString(1*inch, y, f"BudgetBot — Weekly Report ({month})")
    y -= 0.4*inch

    async with SessionLocal() as s:
        # Totals
        q_inc = await s.execute(select(func.sum(Txn.amount)).where(Txn.type=="Income", Txn.month==month))
        total_income = float(q_inc.scalar() or 0.0)
        q_exp = await s.execute(select(func.sum(Txn.amount)).where(Txn.type=="Expense", Txn.month==month))
        total_exp = float(q_exp.scalar() or 0.0)
        net = total_income - total_exp

        c.setFont("Helvetica", 12)
        c.drawString(1*inch, y, f"Income: ${total_income:,.2f}   Expense: ${total_exp:,.2f}   Net: ${net:,.2f}")
        y -= 0.3*inch

        # Envelope status table header
        c.setFont("Helvetica-Bold", 12)
        c.drawString(1*inch, y, "Envelope Status (top 10 by spend)")
        y -= 0.25*inch
        c.setFont("Helvetica", 10)
        c.drawString(1*inch, y, "Category")
        c.drawRightString(5.0*inch, y, "Plan")
        c.drawRightString(6.0*inch, y, "Spent")
        c.drawRightString(7.0*inch, y, "Left")
        y -= 0.18*inch

        # Compute per-budget lines
        q_bud = await s.execute(Budget.__table__.select().where(Budget.month==month))
        rows = []
        for r in q_bud.mappings().all():
            plan = float(r["limit_amount"] or 0.0)
            cat = r["category"]; sub = r["parent"]
            spent = await spent_by(s, month, cat, sub)
            left = plan - spent
            label = f"{cat}" + (f" › {sub}" if sub else "")
            rows.append((label, plan, spent, left))
        rows.sort(key=lambda x: x[2], reverse=True)
        for label, plan, spent, left in rows[:10]:
            if y < 1*inch:
                c.showPage(); y = height - 1*inch
            c.drawString(1*inch, y, label[:40])
            c.drawRightString(5.0*inch, y, f"${plan:,.0f}")
            c.drawRightString(6.0*inch, y, f"${spent:,.0f}")
            c.drawRightString(7.0*inch, y, f"${left:,.0f}")
            y -= 0.16*inch

    c.showPage()
    c.save()
    return buf.getvalue()

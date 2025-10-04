import io
from datetime import datetime
from dateutil import tz

def now_local(tz_name: str):
    tzinfo = tz.gettz(tz_name)
    return datetime.now(tzinfo)

def current_month():
    n = datetime.utcnow()
    return f"{n.year:04d}-{n.month:02d}"

def parse_month(s: str|None):
    if not s:
        return current_month()
    s = s.strip()
    if len(s)==7 and s[4]=='-':
        return s
    return current_month()

def money(n: float, currency: str="USD"):
    return f"{n:,.2f} {currency}"

def to_excel_bytes(rows):
    # rows: list of dicts with keys:
    # Date, Month, Type, Amount, Currency, Category, Sub-Category, Note
    import xlsxwriter
    out = io.BytesIO()
    wb = xlsxwriter.Workbook(out, {'in_memory': True})
    ws = wb.add_worksheet("Transactions")

    headers = ["Date","Month","Type","Amount","Currency","Category","Sub-Category","Note"]
    for c, h in enumerate(headers):
        ws.write(0, c, h)

    money_fmt = wb.add_format({'num_format': '#,##0.00'})
    date_fmt = wb.add_format({'num_format': 'yyyy-mm-dd'})

    for r_idx, r in enumerate(rows, start=1):
        # write date (string OK for Sheets import)
        ws.write(r_idx, 0, r.get("Date",""))
        ws.write(r_idx, 1, r.get("Month",""))
        ws.write(r_idx, 2, r.get("Type",""))
        amt = float(r.get("Amount", 0.0) or 0.0)
        ws.write_number(r_idx, 3, amt, money_fmt)
        ws.write(r_idx, 4, r.get("Currency","USD"))
        ws.write(r_idx, 5, r.get("Category",""))
        ws.write(r_idx, 6, r.get("Sub-Category",""))
        ws.write(r_idx, 7, r.get("Note",""))

    # nice column widths
    widths = [12, 8, 10, 12, 8, 18, 18, 30]
    for c, w in enumerate(widths):
        ws.set_column(c, c, w)

    wb.close()
    return out.getvalue()

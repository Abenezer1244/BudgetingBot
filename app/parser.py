import re
from datetime import datetime, timedelta, date
from typing import Dict, List

AMOUNT_RE = re.compile(r"([$])?\s*([-+]?\d+(?:[\.,]\d{1,2})?)")
HASH_RE = re.compile(r"#([A-Za-z][\w\-/ ]*)")
SUB_RE = re.compile(r"(?:;sub=|;g=)([^\s]+)")
ON_RE  = re.compile(r"\bon=(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
YESTERDAY_RE = re.compile(r"\byesterday\b", re.IGNORECASE)

def _split_categories(t: str) -> List[str]:
    return [p.strip() for p in t.split("+")]

def parse_message(text: str) -> Dict:
    t = text.strip()

    if t.startswith("+"):
        t = t[1:].strip()
        type_ = "Income"
    elif t.startswith("-"):
        t = t[1:].strip()
        type_ = "Expense"
    else:
        type_ = "Expense"

    m = AMOUNT_RE.search(t)
    if not m:
        raise ValueError("No amount found. Try like: 12 coffee #Food")
    amount = float(m.group(2).replace(",", ""))

    d = datetime.utcnow().date()
    if YESTERDAY_RE.search(t):
        d = d - timedelta(days=1)
    mon = ON_RE.search(t)
    if mon:
        d = datetime.strptime(mon.group(1), "%Y-%m-%d").date()

    cat_parts = _split_categories(t)
    categories = []
    for cp in cat_parts:
        mhash = HASH_RE.search(cp)
        sub = SUB_RE.search(cp)
        cat = mhash.group(1).strip() if mhash else None
        subcat = sub.group(1).strip() if sub else None
        if cat:
            categories.append((cat, subcat))

    if not categories:
        categories = [("OtherIncome" if type_=="Income" else "Uncategorized", None)]

    note = HASH_RE.sub("", t)
    note = SUB_RE.sub("", note)
    note = ON_RE.sub("", note)
    note = YESTERDAY_RE.sub("", note)
    note = re.sub(AMOUNT_RE, "", note, count=1).strip()

    return {"type": type_, "amount": amount, "note": note, "categories": categories, "date": d}

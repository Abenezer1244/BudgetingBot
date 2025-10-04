import os, json, logging
from typing import List, Dict, Optional
import gspread
from google.oauth2.service_account import Credentials
_LOG = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

def _service():
    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not creds_json:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON")
    # in sheets_sync.py, inside _service()
    try:
        info = json.loads(creds_json)
    except json.JSONDecodeError:
        # if the JSON was pasted with escaped \n in the private_key
        info = json.loads(creds_json.replace("\\n", "\n"))

    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc

def get_client():
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    if not sheet_id:
        raise RuntimeError("Missing GOOGLE_SHEET_ID")
    gc = _service()
    sh = gc.open_by_key(sheet_id)
    return sh

def ensure_worksheets(sh):
    needed = ["Transactions", "Budgets", "WeeklyCaps", "Freezes"]
    for name in needed:
        try:
            sh.worksheet(name)
        except gspread.WorksheetNotFound:
            sh.add_worksheet(title=name, rows=1000, cols=12)

def init_headers(ws, headers: List[str]):
    try:
        cur = ws.row_values(1)
        if not cur or cur[:len(headers)] != headers:
            ws.resize(1); ws.update("A1:{}1".format(chr(64+len(headers))), [headers])
    except Exception:
        ws.resize(1); ws.update("A1:{}1".format(chr(64+len(headers))), [headers])

def append_transactions(rows: List[Dict]):
    try:
        sh = get_client()
        ensure_worksheets(sh)
        ws = sh.worksheet("Transactions")
        hdr = ["Date","Month","Type","Amount","Currency","Category","Sub-Category","Note"]
        init_headers(ws, hdr)
        values = [[
            r.get("Date",""),
            r.get("Month",""),
            r.get("Type",""),
            float(r.get("Amount",0)),
            r.get("Currency","USD"),
            r.get("Category",""),
            r.get("Sub-Category",""),
            r.get("Note",""),
        ] for r in rows]
        ws.append_rows(values, value_input_option="USER_ENTERED")
    except Exception as e:
        _LOG.exception("Sheets append failed: %s", e)

def upsert_budget(month: str, category: str, parent: Optional[str], limit: float, group_guess: Optional[str]=None):
    try:
        sh = get_client()
        ensure_worksheets(sh)
        ws = sh.worksheet("Budgets")
        hdr = ["Month","Group","Category","Sub-Category","LimitAmount"]
        init_headers(ws, hdr)
        rows = ws.get_all_values()[1:]
        target_idx = None
        for idx, row in enumerate(rows, start=2):
            if len(row) < 5: continue
            if row[0]==month and row[2]==category and row[3]==(parent or ""):
                target_idx = idx; break
        new_row = [month, group_guess or "", category, parent or "", float(limit)]
        if target_idx:
            ws.update(f"A{target_idx}:E{target_idx}", [new_row])
        else:
            ws.append_row(new_row, value_input_option="USER_ENTERED")
    except Exception as e:
        _LOG.exception("Sheets budget upsert failed: %s", e)

def upsert_weeklycap(category: str, parent: Optional[str], cap: float):
    try:
        sh = get_client()
        ensure_worksheets(sh)
        ws = sh.worksheet("WeeklyCaps")
        hdr = ["Category","Sub-Category","CapAmount"]
        init_headers(ws, hdr)
        rows = ws.get_all_values()[1:]
        target_idx = None
        for idx, row in enumerate(rows, start=2):
            if len(row) < 3: continue
            if row[0]==category and row[1]==(parent or ""):
                target_idx = idx; break
        new_row = [category, parent or "", float(cap)]
        if target_idx:
            ws.update(f"A{target_idx}:C{target_idx}", [new_row])
        else:
            ws.append_row(new_row, value_input_option="USER_ENTERED")
    except Exception as e:
        _LOG.exception("Sheets weeklycap upsert failed: %s", e)

def upsert_freeze(category: str, parent: Optional[str], active: bool):
    try:
        sh = get_client()
        ensure_worksheets(sh)
        ws = sh.worksheet("Freezes")
        hdr = ["Category","Sub-Category","Active"]
        init_headers(ws, hdr)
        rows = ws.get_all_values()[1:]
        target_idx = None
        for idx, row in enumerate(rows, start=2):
            if len(row) < 3: continue
            if row[0]==category and row[1]==(parent or ""):
                target_idx = idx; break
        new_row = [category, parent or "", "TRUE" if active else "FALSE"]
        if target_idx:
            ws.update(f"A{target_idx}:C{target_idx}", [new_row])
        else:
            ws.append_row(new_row, value_input_option="USER_ENTERED")
    except Exception as e:
        _LOG.exception("Sheets freeze upsert failed: %s", e)

def bootstrap_sheet(title: Optional[str]=None) -> str:
    gc = _service()
    share_with = os.getenv("SHARE_WITH_EMAIL", "").strip()
    sh = None
    if os.getenv("GOOGLE_SHEET_ID","").strip():
        # init existing sheet
        sh = get_client()
    else:
        sh = gc.create(title or "BudgetBot Sheet")
    ensure_worksheets(sh)
    # headers for all tabs
    init_headers(sh.worksheet("Transactions"), ["Date","Month","Type","Amount","Currency","Category","Sub-Category","Note"])
    init_headers(sh.worksheet("Budgets"), ["Month","Group","Category","Sub-Category","LimitAmount"])
    init_headers(sh.worksheet("WeeklyCaps"), ["Category","Sub-Category","CapAmount"])
    init_headers(sh.worksheet("Freezes"), ["Category","Sub-Category","Active"])
    if share_with:
        try:
            sh.share(share_with, perm_type="user", role="writer", notify=True)
        except Exception as e:
            _LOG.exception("Share failed: %s", e)
    return sh.id

def ping_status() -> str:
    try:
        sh = get_client()
        ensure_worksheets(sh)
        return "✅ Connected to Google Sheets."
    except Exception as e:
        return f"⚠️ Not connected: {e}"

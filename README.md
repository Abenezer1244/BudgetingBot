# Telegram Budgeting Bot — v3

**New in v3 (your requested upgrades):**
1) **Weekly caps** + soft freeze override  
   - `/setweekly Food 60` sets weekly cap for Food (or `Food ;sub=DiningOut 30`).  
   - Bot warns at 80% and blocks when exceeding cap unless you use `/override <original message>`.
   - `/weeklyleft` shows remaining against weekly caps.

2) **Undo/Edit with inline buttons**  
   - `/undo` deletes your most recent transaction.  
   - `/history` lists the last 10 with **Delete** buttons.  
   - `/edit <id> amount=11.50 note="new note" #Category ;sub=Sub` edits any combo of fields.

3) **Goals & Sinking funds + month-end sweep**  
   - `/goal add "Emergency Fund" 1000 200` (name, target, monthly contribution).  
   - `/goal list`, `/goal contribute "Emergency Fund" 50`  
   - **Month-end sweep**: bot pings on the last day to sweep leftover envelopes to a selected goal (confirm with one tap), or run anytime via `/sweep`.

Everything is deployable to **Render** via `render.yaml`. Works with Postgres or SQLite.

## Local Dev
```
pip install -r requirements.txt
cp .env.example .env
python -m app.bot
```

## Quick UX
- Log fast: `12 coffee #Food` or `+200 tutoring #OtherIncome`
- Set budget: `/setbudget Food 300`
- Set weekly cap: `/setweekly Food 60`
- Freeze a category (manual): `/freeze add Food;sub=DiningOut`
- Show what's left: `/left` (monthly) / `/weeklyleft` (weekly)
- Reports: `/report`, What-if: `/whatif Food -20%`
- Templates: `/template add lunch 12 #Food;sub=DiningOut` → `/lunch`
- Edit: `/history` → Delete inline, or `/edit <id> ...`
- Goals: `/goal add`, `/goal list`, `/goal contribute`, `/sweep`

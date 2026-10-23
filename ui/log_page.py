"""Applications log: every application in one editable table, with CSV export."""
from __future__ import annotations

import csv
import io
from datetime import date

import streamlit as st

from cvdrafter import memory
from ui.common import setup

setup()
st.title("Applications log")
st.caption("Every posting you paste on the Apply page is logged here automatically, with company, role, location, pay "
           " and deadline taken from the posting. Edit any cell to correct it.")

# ---------------------------------------------------------------- table

apps = memory.list_all()
if not apps:
    if st.button("Paste your first posting", type="primary"):
        st.switch_page("ui/chat.py")
    st.stop()

COLS = ["id", "company", "role", "status", "sent_at", "follow_up_at", "location", "work_mode", "employment_type",
        "salary", "deadline", "job_ref", "url", "company_confidence", "created_at"]
EDITABLE = [c for c in COLS if c not in ("id", "created_at", "company_confidence")]
DATES = ("sent_at", "follow_up_at")


def to_date(v):
    try:
        return date.fromisoformat(v[:10]) if v else None
    except ValueError:
        return None


f1, f2 = st.columns([2, 3])
query = f1.text_input("Search", placeholder="company, role, location…").lower()
statuses = f2.multiselect("Status", memory.STATUSES, default=[s for s in memory.STATUSES if s != "draft"])
shown = [a for a in apps if a["status"] in (statuses or memory.STATUSES)
         and query in " ".join(str(a.get(c) or "") for c in COLS).lower()]
rows = [{c: (to_date(a.get(c)) if c in DATES else a.get(c) or "") for c in COLS} for a in shown]
for r in rows:
    r["created_at"] = r["created_at"][:10]
    r["company_confidence"] = {"high": "Confirmed", "medium": "Review", "low": "Review"}.get(r["company_confidence"], "")

edited = st.data_editor(
    rows, key="log_table", hide_index=True, width="stretch", disabled=["id", "created_at", "company_confidence"],
    column_config={
        "id": st.column_config.NumberColumn("#", width="small"),
        "status": st.column_config.SelectboxColumn("status", options=memory.STATUSES, required=True),
        "sent_at": st.column_config.DateColumn("applied on"),
        "follow_up_at": st.column_config.DateColumn("follow up"),
        "url": st.column_config.LinkColumn("url"),
        "company_confidence": st.column_config.TextColumn("company ok?", width="small",
                                                          help="How sure the app is about the company name."),
        "created_at": st.column_config.TextColumn("logged"),
    },
)

changes = []
for old, new in zip(rows, edited):
    diff = {c: new[c] for c in EDITABLE if new[c] != old[c]}
    if diff:
        changes.append((old["id"], diff))
c1, c2, _ = st.columns([1, 1, 3])
if c1.button(f"Save {len(changes)} change(s)", type="primary", disabled=not changes):
    for app_id, diff in changes:
        fields = {c: (v.isoformat() if hasattr(v, "isoformat") else v) for c, v in diff.items()}
        if "company" in fields:
            fields["company_confidence"] = "high"  # you confirmed it
        memory.update(app_id, **fields)
    st.toast("Saved")
    st.rerun()

buf = io.StringIO()
writer = csv.DictWriter(buf, fieldnames=COLS)
writer.writeheader()
writer.writerows({c: a.get(c) or "" for c in COLS} for a in shown)
c2.download_button("Export CSV", buf.getvalue(), file_name=f"applications_{date.today().isoformat()}.csv", mime="text/csv")

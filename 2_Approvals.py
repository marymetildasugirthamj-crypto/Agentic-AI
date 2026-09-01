"""
Manager Approvals portal (Phase 4 + 5).

The hierarchy/business owner opens this page, sees pending savings tickets (the
in-portal notification is the pending count), and Approves / Rejects. On approval,
guarded execution runs — DRY-RUN by default; ticking "execute for real" performs
the actual (reversible where possible) AWS action.
"""

import os
import sys

import streamlit as st
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
load_dotenv(os.path.join(_ROOT, ".env"))

import approvals  # noqa: E402

st.set_page_config(page_title="Approvals", page_icon="✅", layout="wide")

st.title("✅ Manager Approvals")
st.caption("Cost-saving actions raised by engineers wait here for business-owner approval. "
           "Nothing runs on the account until you approve — and every decision is logged.")

# ── role + notification ──
with st.sidebar:
    role = st.radio("You are", ["Manager", "Engineer"], index=0,
                    help="Simulates the approval hierarchy. In production this is SSO + RBAC.")
    n = approvals.pending_count()
    st.metric("🔔 Pending approvals", n)

pending = approvals.list_tickets("PENDING")
st.subheader(f"Pending requests ({len(pending)})")

if not pending:
    st.info("No pending requests. Engineers raise them from the Dashboard → *Raise approval requests*.")
else:
    if role != "Manager":
        st.warning("Switch role to **Manager** (sidebar) to approve or reject.")
    execute_live = st.toggle(
        "Execute approved actions for real", value=False,
        help="Off = dry-run (safe, recommended for demos). On = perform the real AWS action.")
    if execute_live:
        st.warning("⚠️ Live execution is ON — approving will perform the real AWS action "
                   "(e.g., release an Elastic IP, stop an instance).")

    for tk in pending:
        with st.container(border=True):
            top, act = st.columns([4, 1])
            top.markdown(f"**{tk['id']} · {tk['category']}**  \n"
                         f"Resource: `{tk['resource']}` · region `{tk['region']}`  \n"
                         f"Requested by **{tk['requested_by']}** at {tk['created_at']}")
            act.metric("Impact", f"${tk['monthly_savings']:.2f}/mo")
            st.markdown(f"- **Action:** `{tk['action']}`\n"
                        f"- **Justification:** {tk['justification']}\n"
                        f"- **Rollback:** {tk['rollback']}")
            b1, b2, _ = st.columns([1, 1, 4])
            approve = b1.button("✅ Approve", key=f"ap_{tk['id']}", type="primary",
                                disabled=(role != "Manager"), use_container_width=True)
            reject = b2.button("🚫 Reject", key=f"rj_{tk['id']}",
                               disabled=(role != "Manager"), use_container_width=True)
            if approve:
                res = approvals.decide(tk["id"], approver="manager", approved=True,
                                       execute_live=execute_live)
                st.success(f"Approved. Execution: {res.get('execution')}")
                st.rerun()
            if reject:
                approvals.decide(tk["id"], approver="manager", approved=False)
                st.rerun()

# ── history / audit ──
st.divider()
st.subheader("Decision history")
history = [t for t in approvals.list_tickets() if t["status"] != "PENDING"]
if not history:
    st.caption("No decisions yet.")
else:
    icon = {"EXECUTED": "✅", "APPROVED": "✅", "REJECTED": "🚫", "FAILED": "⚠️"}
    for t in reversed(history):
        st.markdown(
            f"{icon.get(t['status'], '•')} **{t['id']}** · {t['status']} · `{t['resource']}` "
            f"· ${t['monthly_savings']:.2f}/mo · by {t.get('approver') or '—'} "
            f"at {t.get('decided_at') or '—'}"
            + (f"  \n<span style='color:#8a97ad'>{t['execution']}</span>" if t.get("execution") else ""),
            unsafe_allow_html=True)
    approved_savings = sum(t["monthly_savings"] for t in history if t["status"] in ("EXECUTED", "APPROVED"))
    st.success(f"Approved savings so far: **${approved_savings:.2f}/mo** (~${approved_savings*12:.0f}/yr).")

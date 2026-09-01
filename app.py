"""
Chat-FinOps Agent — Dashboard (home page).

Phases 1–3, all read-only:
  1. Cost visibility  — spend, trend, forecast vs budget
  2. Cost analytics   — service breakdown, month-over-month movers, spike reason
  3. Optimization     — idle/unused resources with estimated $/mo savings (live account)

The natural-language chat (LangChain) is the second page in the sidebar.
"""

import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import finops_tools as F
import approvals

st.set_page_config(page_title="Chat-FinOps Agent", page_icon="💰", layout="wide")
ss = st.session_state
real = F._use_real()

st.title("💰 Chat-FinOps Agent")
st.caption("Continuously analyze AWS cost, surface waste with dollar-value savings, and stay "
           "audit-ready — all read-only. Ask questions on the **Chat** page in the sidebar.")
st.markdown(f"**Mode:** {'🟢 REAL AWS (boto3)' if real else '🧪 MOCK data'} · "
            "Optimization runs live against the account; cost dashboard uses Cost Explorer when ready, "
            "otherwise clearly-labeled sample data.")

# ── 1 · COST VISIBILITY ──────────────────────────────────────────────────────
st.header("1 · Cost visibility")
fc = F.forecast_month_end()
acc = F.cost_by_account()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Forecast (month-end)", f"${fc['forecast']:,.0f}", f"{fc['forecast'] - fc['budget']:+,.0f} vs budget")
c2.metric("Month-to-date", f"${fc['month_to_date']:,.0f}")
c3.metric("Budget", f"${fc['budget']:,.0f}")
c4.metric("Accounts tracked", len(acc["accounts"]))
if fc["forecast"] > fc["budget"]:
    st.error(f"⚠️ Forecast ${fc['forecast']:,.0f} exceeds the ${fc['budget']:,.0f} budget.")
else:
    st.success(f"On track — forecast ${fc['forecast']:,.0f} is under the ${fc['budget']:,.0f} budget.")

tr = F.cost_trend()
st.line_chart(pd.DataFrame({"Monthly spend ($)": tr["values"]}, index=tr["labels"]))
st.caption(f"Spend per account ({acc['source']}): " +
           " · ".join(f"{k} ${v:,.0f}" for k, v in acc["accounts"].items()))

# ── 2 · COST ANALYTICS ───────────────────────────────────────────────────────
st.header("2 · Cost analytics")
sb = F.service_breakdown()
left, right = st.columns([3, 2])
with left:
    st.subheader("Cost by service")
    df = pd.DataFrame(sb["services"], columns=["Service", "Cost ($)"]).set_index("Service")
    st.bar_chart(df)
    st.caption(f"Source: {sb['source']} · last full month total ${sb['total']:,.2f}")
with right:
    st.subheader("Month-over-month movers")
    for r in F.month_over_month():
        arrow = "🔺" if r["change_pct"] > 0 else ("🔻" if r["change_pct"] < 0 else "▪️")
        color = "#e3342f" if r["change_pct"] > 15 else "inherit"
        st.markdown(f"{arrow} **{r['service']}** "
                    f"<span style='color:{color}'>{r['change_pct']:+d}% (${r['delta']:+,})</span>  \n"
                    f"<span style='color:#8a97ad;font-size:13px'>{r['note']}</span>",
                    unsafe_allow_html=True)
    top = max(F.month_over_month(), key=lambda r: r["change_pct"])
    st.info(f"Spike root cause: **{top['service']}** {top['change_pct']:+d}% — {top['note']}.")

# ── 3 · OPTIMIZATION & SAVINGS ───────────────────────────────────────────────
st.header("3 · Optimization & savings")
st.caption("Read-only scan: idle/low-use EC2, unattached EBS, unassociated Elastic IPs, "
           "old snapshots, and stale S3 — each with an estimated monthly saving.")
if st.button("🔍 Scan for savings", type="primary") or "finops_rep" in ss:
    if "finops_rep" not in ss or st.session_state.get("_rescan"):
        pass
    with st.spinner("Scanning the account…"):
        ss.finops_rep = F.optimization_report()
    rep = ss.finops_rep
    m1, m2, m3 = st.columns(3)
    m1.metric("Potential savings", f"${rep['total_monthly_savings']:,.2f}/mo")
    m2.metric("Annualized", f"${rep['annual_savings']:,.0f}/yr")
    m3.metric("Findings", sum(1 for i in rep["items"] if i.get("monthly_savings")))

    rows = [{"Category": i["category"], "Resource": i["resource"], "Region": i.get("region", "-"),
             "Detail": i["detail"], "Est. savings ($/mo)": round(i.get("monthly_savings", 0) or 0, 2)}
            for i in rep["items"] if i.get("monthly_savings")]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.success(f"Found ${rep['total_monthly_savings']:,.2f}/mo of potential savings "
                   f"(~${rep['annual_savings']:,.0f}/yr).")

        st.subheader("Raise approval requests")
        st.caption("Send a finding to the manager portal as a ticket — no action runs until it's approved.")
        for it in [i for i in rep["items"] if i.get("monthly_savings")]:
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**{it['category']}** · `{it['resource']}` — ${it['monthly_savings']:.2f}/mo")
            if approvals.already_requested(it["resource"]):
                c2.caption("✔ requested")
            elif c2.button("Request", key=f"req_{it['resource']}"):
                approvals.create_ticket(it, requested_by="engineer")
                st.toast(f"Request raised for {it['resource']} → Manager portal")
                st.rerun()

        n = approvals.pending_count()
        if n:
            st.info(f"🔔 **{n} request(s) pending** in the Manager **Approvals** portal "
                    "(open it from the sidebar).")
    else:
        st.info("No clear waste found by the current detectors. 🎉")
else:
    st.info("Click **Scan for savings** to audit the account.")

"""
AWS ChatOps Agent — Streamlit UI.

Run:
    streamlit run app.py

The agent answers AWS questions and can operate EC2 — but every STATE-CHANGING
action pauses for a human "Approve / Deny" click. That approval gate is the whole
point: an AI agent may reason about your cloud, but it must never mutate it silently.

The UI streams the agent's answer in with a typing effect and shows a live
"Thinking…" indicator while it reasons and runs tools.
"""

import os
import re
import time
import datetime as dt

import streamlit as st

from agent import call_model, execute_tool, describe_call, SYSTEM, has_key, MODEL
from aws_tools import RISK

st.set_page_config(page_title="AWS ChatOps Agent", page_icon="☁️", layout="centered")

USER, BOT = "🧑‍💻", "🤖"

# ── a little polish: tighter column, gentle fade-in for each message ──
st.markdown(
    """
    <style>
      .block-container{max-width:820px;padding-top:2rem}
      [data-testid="stChatMessage"]{animation:fadein .30s ease both;margin-bottom:.25rem}
      @keyframes fadein{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
      [data-testid="stChatMessageContent"] p{line-height:1.55}
    </style>
    """,
    unsafe_allow_html=True,
)

ss = st.session_state
if "agent_msgs" not in ss:
    ss.agent_msgs = [{"role": "system", "content": SYSTEM}]  # what the model sees
    ss.chat = []        # what the human sees: list of {role, text}
    ss.pending = None   # a destructive tool call awaiting approval
    ss.audit = []       # append-only record of every tool execution


# ── helpers ──────────────────────────────────────────────────────────────────
def _audit(call, result, decision):
    ss.audit.append({
        "time": dt.datetime.now().strftime("%H:%M:%S"),
        "action": describe_call(call),
        "risk": RISK.get(call["name"], "read_only"),
        "decision": decision,
        "result": result.splitlines()[0] if result else "",
    })


def typewriter(text, delay=0.018):
    """Yield a piece of text at a time so st.write_stream animates it like typing."""
    for token in re.findall(r"\s*\S+", text or "…"):
        yield token
        time.sleep(delay)


def render_static(m):
    role = "user" if m["role"] == "user" else "assistant"
    with st.chat_message(role, avatar=USER if role == "user" else BOT):
        st.markdown(m["text"])


def run_turns():
    """Continue the agent from ss.agent_msgs, rendering LIVE. Streams the final
    answer with a typing effect; stops (sets ss.pending) if a destructive action
    needs approval. Read-only tools run automatically."""
    for _ in range(8):  # hard step cap — the loop can never run away
        with st.spinner("Thinking…"):
            content, calls, assistant = call_model(ss.agent_msgs)

        if not calls:                                       # final answer → stream it
            ss.agent_msgs.append(assistant)
            with st.chat_message("assistant", avatar=BOT):
                streamed = st.write_stream(typewriter(content or "…"))
            ss.chat.append({"role": "assistant", "text": streamed if isinstance(streamed, str) else (content or "…")})
            return

        ss.agent_msgs.append(assistant)                     # assistant asked for tools
        pending = []
        for c in calls:
            if RISK.get(c["name"]) == "destructive":
                pending.append(c)                           # hold for approval
            else:                                           # read-only → run now
                with st.spinner(f"Running {describe_call(c)}…"):
                    out = execute_tool(c["name"], c["args"])
                ss.agent_msgs.append({"role": "tool", "tool_call_id": c["id"], "content": out})
                txt = f"🔧 `{describe_call(c)}`\n\n```\n{out}\n```"
                ss.chat.append({"role": "tool", "text": txt})
                with st.chat_message("assistant", avatar=BOT):
                    st.markdown(txt)
                _audit(c, out, "auto (read-only)")

        if pending:                                         # pause for a human
            ss.pending = {"calls": pending}
            return

    msg = "⚠️ Reached the step limit — stopping (loop is bounded by design)."
    ss.chat.append({"role": "tool", "text": msg})
    with st.chat_message("assistant", avatar=BOT):
        st.markdown(msg)


# ── sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("☁️ AWS ChatOps")
    real = os.getenv("USE_REAL_AWS", "false").lower() in ("1", "true", "yes")
    st.markdown(f"**Cloud:** {'🟢 REAL AWS (boto3)' if real else '🧪 MOCK (safe demo data)'}")
    st.markdown(f"**Brain:** {'🟢 gpt-4o (' + MODEL + ')' if has_key() else '🟡 offline fallback (no key)'}")
    st.caption("Read-only tools run automatically. Restart/stop/start pause for your approval.")

    st.divider()
    st.subheader("Try asking")
    for ex in ["How many running EC2 instances are in us-east-1?",
               "List my S3 buckets and flag any public ones",
               "Show CloudWatch alarms in ALARM state",
               "Restart the staging worker"]:
        st.markdown(f"- {ex}")

    st.divider()
    st.subheader("🧾 Audit log")
    if not ss.audit:
        st.caption("No actions yet.")
    for a in reversed(ss.audit[-12:]):
        icon = {"approved": "✅", "denied": "🚫"}.get(a["decision"], "🔧")
        st.markdown(f"{icon} `{a['time']}` **{a['action']}** — {a['decision']}")
    if st.button("Reset conversation"):
        for k in ("agent_msgs", "chat", "pending", "audit"):
            ss.pop(k, None)
        st.rerun()

# ── main ─────────────────────────────────────────────────────────────────────
st.title("AWS ChatOps Agent")
st.caption("Operate your AWS account from chat — with a human approval gate on every change.")

# committed history
for m in ss.chat:
    render_static(m)

# approval gate (rendered in a placeholder so the buttons clear cleanly on a decision)
if ss.pending:
    calls = ss.pending["calls"]
    gate = st.empty()
    with gate.container():
        with st.chat_message("assistant", avatar=BOT):
            st.warning("Approval required before this state-changing action runs:")
            for c in calls:
                st.code(describe_call(c), language="python")
            col_a, col_b = st.columns(2)
            approve = col_a.button("✅ Approve & run", use_container_width=True, type="primary")
            deny = col_b.button("🚫 Deny", use_container_width=True)
    if approve or deny:
        gate.empty()
        ss.pending = None
        for c in calls:
            if approve:
                with st.spinner(f"Running {describe_call(c)}…"):
                    out = execute_tool(c["name"], c["args"])
                txt = f"✅ **APPROVED** · `{describe_call(c)}`\n\n```\n{out}\n```"
                _audit(c, out, "approved")
            else:
                out = "Denied by operator — no action was taken."
                txt = f"🚫 **DENIED** · `{describe_call(c)}`"
                _audit(c, out, "denied")
            ss.agent_msgs.append({"role": "tool", "tool_call_id": c["id"], "content": out})
            ss.chat.append({"role": "tool", "text": txt})
            with st.chat_message("assistant", avatar=BOT):
                st.markdown(txt)
        run_turns()                       # continue → stream the confirmation/answer
        if ss.pending:                    # another approval needed → show it cleanly
            st.rerun()

# input
prompt = st.chat_input("Ask about your AWS…", disabled=bool(ss.pending))
if prompt:
    ss.chat.append({"role": "user", "text": prompt})
    ss.agent_msgs.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=USER):
        st.markdown(prompt)
    run_turns()
    if ss.pending:                        # a destructive action needs approval
        st.rerun()

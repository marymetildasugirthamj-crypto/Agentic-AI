"""
Chat-FinOps — natural-language chat page (LangChain agent, gpt-4o).

Answers cost, trend, root-cause and savings questions using the finops tools.
Streams the answer in with a typing effect.
"""

import os
import re
import sys
import time

import streamlit as st
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
load_dotenv(os.path.join(_ROOT, ".env"))

from finops_agent import ask_finops  # noqa: E402

st.set_page_config(page_title="FinOps Chat", page_icon="💬", layout="centered")
BOT, USER = "💰", "🧑‍💻"

st.title("💬 Ask the FinOps agent")
st.caption("Powered by LangChain + gpt-4o over the cost & optimization tools.")

ss = st.session_state
if "finchat" not in ss:
    ss.finchat = []

with st.sidebar:
    st.subheader("Try asking")
    for ex in ["Show this month's cost and forecast.",
               "Why did the bill go up this month?",
               "Where can we save money?",
               "List idle resources with savings."]:
        st.markdown(f"- {ex}")


def typewriter(text, delay=0.016):
    for tok in re.findall(r"\s*\S+", text or "…"):
        yield tok
        time.sleep(delay)


for m in ss.finchat:
    with st.chat_message(m["role"], avatar=USER if m["role"] == "user" else BOT):
        st.markdown(m["text"])

if prompt := st.chat_input("Ask about cost, trends, or savings…"):
    ss.finchat.append({"role": "user", "text": prompt})
    with st.chat_message("user", avatar=USER):
        st.markdown(prompt)
    with st.chat_message("assistant", avatar=BOT):
        with st.spinner("Analyzing…"):
            answer = ask_finops(prompt)
        streamed = st.write_stream(typewriter(answer))
    ss.finchat.append({"role": "assistant", "text": streamed if isinstance(streamed, str) else answer})

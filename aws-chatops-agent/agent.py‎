"""
AWS ChatOps Agent — the BRAIN.

This module is UI-agnostic: `call_model()` runs one turn of the gpt-4o tool-calling
loop and returns a normalized result the Streamlit app can drive. It works with a
real OpenAI key (LIVE) and also has a small OFFLINE fallback so the app demos with
no key at all.

The app owns the loop + the human approval gate; this file just:
  • asks the model what to do next (call_model)
  • executes a tool the model chose (execute_tool)
  • formats a tool call for display (describe_call)
"""

import os
import json
from pathlib import Path

from aws_tools import TOOLS, REGISTRY, RISK, _MOCK_EC2, DEFAULT_REGION

# Load OPENAI_API_KEY etc. from THIS folder (.env or env — either rename works).
try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    load_dotenv(_here / ".env")
    load_dotenv(_here / "env")
except ImportError:
    pass

MODEL = os.getenv("MODEL", "gpt-4o")

SYSTEM = (
    "You are an AWS ChatOps agent. Engineers talk to you in a chat box to inspect and "
    "operate their AWS account. Use the READ-ONLY tools to answer questions about EC2, "
    "S3, and CloudWatch. For any STATE-CHANGING action (restart/stop/start an instance) "
    "call the matching tool — a human approval gate will confirm before it actually runs. "
    "Always act on an instance id; if the user names an instance (e.g. 'the staging worker') "
    "first call list_ec2_instances to find its id. Be concise and report exactly what you did "
    "or found. Never claim an action succeeded unless a tool result confirms it. "
    f"The default AWS region is {DEFAULT_REGION} unless the user names another."
)


def has_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


# ── run / describe a tool ────────────────────────────────────────────────────
def execute_tool(name: str, args: dict) -> str:
    fn = REGISTRY.get(name)
    if fn is None:
        return f"ERROR: unknown tool '{name}'"
    try:
        return fn(**args)
    except Exception as e:                     # tool errors become observations
        return f"ERROR running {name}: {e}"


def describe_call(call: dict) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in call["args"].items())
    return f"{call['name']}({args})"


# ── one model turn ───────────────────────────────────────────────────────────
def call_model(messages: list):
    """Return (content:str|None, calls:list[{id,name,args}], assistant_msg:dict)."""
    if has_key():
        return _live(messages)
    return _offline(messages)


def _live(messages):
    from openai import OpenAI
    client = OpenAI()
    msg = client.chat.completions.create(
        model=MODEL, messages=messages, tools=TOOLS,
        tool_choice="auto", temperature=0,
    ).choices[0].message

    assistant = {"role": "assistant", "content": msg.content}
    calls = []
    if msg.tool_calls:
        assistant["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
        calls = [{"id": tc.id, "name": tc.function.name,
                  "args": json.loads(tc.function.arguments or "{}")}
                 for tc in msg.tool_calls]
    return msg.content, calls, assistant


# ── OFFLINE fallback brain (no key) — keyword routing over one tool per turn ──
_OFFLINE_IDS = {"i": 0}


def _resolve_name(text: str, region: str = "us-east-1") -> str:
    """Find an instance id by a name hinted in the user's text (default staging-worker)."""
    t = text.lower()
    for inst in _MOCK_EC2.get(region, []):
        if inst["name"].lower() in t or inst["name"].split("-")[0] in t:
            return inst["id"]
    return "i-0c9d8e7f6a5b4c3d2"  # staging-worker


def _mk_call(name, args):
    _OFFLINE_IDS["i"] += 1
    cid = f"call_offline_{_OFFLINE_IDS['i']}"
    assistant = {"role": "assistant", "content": None,
                 "tool_calls": [{"id": cid, "type": "function",
                                 "function": {"name": name, "arguments": json.dumps(args)}}]}
    return None, [{"id": cid, "name": name, "args": args}], assistant


def _offline(messages):
    last = messages[-1]

    # If a tool just ran, summarize its result as the final answer.
    if last["role"] == "tool":
        return f"(offline demo brain) Here is the result:\n\n{last['content']}", [], \
               {"role": "assistant", "content": last["content"]}

    text = last.get("content", "") or ""
    t = text.lower()

    if any(w in t for w in ("restart", "reboot")):
        return _mk_call("restart_ec2_instance", {"instance_id": _resolve_name(t)})
    if "stop" in t or "shut" in t:
        return _mk_call("stop_ec2_instance", {"instance_id": _resolve_name(t)})
    if "start" in t and "instance" in t or ("start the" in t):
        return _mk_call("start_ec2_instance", {"instance_id": _resolve_name(t)})
    if any(w in t for w in ("s3", "bucket")):
        return _mk_call("list_s3_buckets", {})
    if any(w in t for w in ("alarm", "cloudwatch")):
        return _mk_call("get_cloudwatch_alarms", {})
    if any(w in t for w in ("ec2", "instance", "running", "how many", "list", "servers")):
        args = {"region": "us-east-1"}
        if "running" in t:
            args["state"] = "running"
        elif "stopped" in t:
            args["state"] = "stopped"
        return _mk_call("list_ec2_instances", args)

    help_msg = ("(offline demo brain — set OPENAI_API_KEY for the real gpt-4o agent) "
                "Try: 'how many running EC2 in us-east-1?', 'list S3 buckets', "
                "'show alarms in ALARM state', or 'restart the staging worker'.")
    return help_msg, [], {"role": "assistant", "content": help_msg}

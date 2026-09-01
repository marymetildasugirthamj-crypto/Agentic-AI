"""
Chat-FinOps Agent — the LangChain brain.

Exposes the cost + optimization tools to a LangChain 1.x tool-calling agent (gpt-4o)
so users can ask natural-language questions:
  "Show this month's cost", "Why did cost go up?", "Where can we save money?"
"""

import os
from pathlib import Path

from langchain_core.tools import tool

import finops_tools as F

# load the same .env the dashboard uses (this folder)
try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    load_dotenv(_here / ".env")
    load_dotenv(_here / "env")
except ImportError:
    pass


@tool
def get_cost_summary() -> str:
    """Current AWS spend: total for the last full month, the top services, the 6-month
    trend, and the end-of-month forecast vs budget. Use for 'what is our cost / spend'."""
    sb = F.service_breakdown()
    fc = F.forecast_month_end()
    tr = F.cost_trend()
    top = "; ".join(f"{n} ${v:,.0f}" for n, v in sb["services"][:5])
    return (f"Source: {sb['source']}. Last full month total ${sb['total']:,.2f}. "
            f"Top services: {top}. "
            f"Month-to-date ${fc['month_to_date']:,.0f}, forecast ${fc['forecast']:,.0f} "
            f"(budget ${fc['budget']:,.0f}). Trend {tr['labels'][0]}→{tr['labels'][-1]}: "
            f"{tr['values']}.")


@tool
def get_cost_changes() -> str:
    """Biggest month-over-month cost movers by service, with the reason for each change.
    Use for 'why did the bill go up/down' or 'what caused the spike'."""
    rows = F.month_over_month()
    return "Month-over-month movers: " + "; ".join(
        f"{r['service']} {r['change_pct']:+d}% (${r['delta']:+,}) — {r['note']}" for r in rows)


@tool
def get_savings_opportunities(cpu_threshold: float = 10.0) -> str:
    """Scan the account for waste (idle EC2, unattached EBS, unassociated Elastic IPs,
    old snapshots, stale S3) and return each finding with an estimated monthly saving.
    Use for 'where can we save', 'find idle resources', 'optimization report'."""
    return F.format_optimization(F.optimization_report(cpu_threshold))


TOOLS = [get_cost_summary, get_cost_changes, get_savings_opportunities]

_SYSTEM = (
    "You are a senior AWS FinOps analyst. Answer questions about cost, trends, the drivers "
    "of change, and concrete savings using ONLY the tools. Always ground answers in the "
    "numbers the tools return, quote dollar figures, and be concise. When asked why cost "
    "changed, use the cost-changes tool and name the service responsible. When asked about "
    "savings, list the top opportunities and the total monthly saving."
)


def ask_finops(question: str) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        return "Set OPENAI_API_KEY to use the FinOps agent. (The dashboard works without it.)"
    from langchain.agents import create_agent
    agent = create_agent("openai:" + os.getenv("MODEL", "gpt-4o"), TOOLS, system_prompt=_SYSTEM)
    out = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return out["messages"][-1].content

# ☁️ AWS ChatOps Agent (Streamlit)

A real-world **agentic AI** demo: operate your AWS account from a chat box. Ask plain-English
questions ("how many running EC2 in us-east-1?") and request actions ("restart the staging
worker") — the agent picks the right AWS tools, and **every state-changing action pauses for a
human Approve / Deny click**.

Built with the same pattern as the course labs: **gpt-4o** brain + **tool calling** + `boto3`
tools + a **human approval gate**.

## 1. What this is about

- **Agentic AI over AWS.** The LLM doesn't touch AWS directly — it *chooses tools*, and your
  code runs them with `boto3`. This is the safe, auditable way to give an agent real power.
- **Guardrails are the product.** Read-only tools (list EC2 / S3 / alarms) run automatically;
  destructive tools (restart/stop/start) are **proposed and held** until a human approves —
  live, on screen. That approval button is the demo's "wow" moment.
- **Runs anywhere.** Ships in **MOCK mode** (canned AWS data) so you can demo with zero risk and
  no AWS account; flip one flag to hit **real AWS**.

## 2. What it does (per file)

| File | What it is |
|------|------------|
| `aws_tools.py` | The AWS tools (EC2 / S3 / CloudWatch) — each works in **mock** or **real boto3** mode. Also exports the OpenAI tool schemas (`TOOLS`), the `REGISTRY`, and the `RISK` map (read-only vs destructive). |
| `agent.py` | The gpt-4o tool-calling brain (`call_model`) + an **offline fallback** brain so it runs with no key. UI-agnostic. |
| `app.py` | The **Streamlit** chat UI: renders the conversation + tool calls, runs the loop, and shows the **Approve / Deny** gate. Keeps an audit log. |
| `requirements.txt` | Everything to install. |
| `env.sample` | Rename to `.env` and paste your `OPENAI_API_KEY`. |

## 3. Prerequisites — what to install first

Python **3.10+**, then (inside this folder):

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp env.sample .env        # then open .env and paste your OPENAI_API_KEY
```

Packages installed: `streamlit`, `openai`, `boto3`, `python-dotenv`.

## 4. How to run

```bash
streamlit run app.py
```

Your browser opens at `http://localhost:8501`. Type in the chat box, or click one of the
example prompts in the sidebar.

- **No API key?** The app still runs with an offline fallback brain that handles the headline
  demo queries — so nothing blocks you in class.
- **With your key** the real gpt-4o agent decides which tools to call.

## 5. Mock vs. real AWS

- **MOCK (default, `USE_REAL_AWS=false`)** — deterministic fake inventory (a few EC2 instances, S3
  buckets incl. one public, CloudWatch alarms). Safe for any stage; stop/start actually change the
  mock state so restart/stop demos feel live.
- **REAL (`USE_REAL_AWS=true`)** — uses `boto3` against your account via the standard AWS credential
  chain (env vars / `~/.aws/credentials` / IAM role). Start **read-only** (list tools) with a
  least-privilege IAM user before demoing restart/stop.

### Go live in 4 steps
1. **Create an IAM user** (Console → IAM → Users → *Create user* → **Attach policies → Create inline policy** → paste [`iam-policy.json`](iam-policy.json)) and generate an **access key**.
2. **Put credentials + region in `.env`:**
   ```
   USE_REAL_AWS=true
   AWS_ACCESS_KEY_ID=AKIA...
   AWS_SECRET_ACCESS_KEY=...
   AWS_DEFAULT_REGION=ap-south-1      # ← your account's region
   ```
   (Or run `aws configure` instead of putting keys in `.env` — then just set `USE_REAL_AWS=true`.)
3. **Pre-flight check** — confirms creds + permissions, changes nothing:
   ```bash
   python test_connection.py
   ```
4. **Run live:** `streamlit run app.py`. The app now targets your real account and region; the
   sidebar shows **🟢 REAL AWS**. Restart/stop/start still require your **Approve** click.

> ⚠️ Live restart/stop/start really act on your instances. Demo them on a throwaway/sandbox instance.

## 6. Suggested demo script (≈4 min)

1. **"How many running EC2 instances are in us-east-1?"** → agent calls `list_ec2_instances` → clean list.
2. **"List my S3 buckets and flag public ones."** → it surfaces the ⚠ public bucket.
3. **"Show alarms in ALARM state."** → the HighCPU alarm on `web-prod-2`.
4. **"Restart the staging worker."** → agent looks up the id, then **proposes the reboot and waits** →
   you click **✅ Approve** → it runs and confirms. (Optionally click **🚫 Deny** first to show the gate blocks it.)
5. Point at the **sidebar audit log** — every action, who approved it, timestamped.

## 7. Note

Files are `python -m py_compile` clean. The agent loop is bounded by a hard step cap, and no
state-changing AWS call can execute without an explicit human approval in the UI.

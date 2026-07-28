"""
AWS ChatOps Agent — the TOOL layer.

Every function here is a "tool" the gpt-4o agent can call. Each one works in TWO
modes, chosen by the USE_REAL_AWS env var:

  • USE_REAL_AWS=false (default) → MOCK mode: deterministic canned AWS data, so the
    demo runs anywhere with zero risk and no AWS account.
  • USE_REAL_AWS=true             → LIVE mode: real boto3 calls against your account
    (uses the standard AWS credential chain — env vars / ~/.aws / IAM role).

The bottom of the file exposes three things the agent needs:
  TOOLS     — OpenAI tool schemas (what we pass as tools=[...])
  REGISTRY  — name -> python function (how we execute a tool the model asked for)
  RISK      — name -> "read_only" | "destructive" (drives the human approval gate)
"""

import os


def _use_real() -> bool:
    return os.getenv("USE_REAL_AWS", "false").strip().lower() in ("1", "true", "yes")


# In LIVE mode, target YOUR account's region by default (falls back to us-east-1).
DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION") or "us-east-1"


# ─────────────────────────────────────────────────────────────────────────────
# MOCK cloud state (mutable, so start/stop actually change what you see next).
# ─────────────────────────────────────────────────────────────────────────────
_MOCK_EC2 = {
    "us-east-1": [
        {"id": "i-0a1b2c3d4e5f6a7b8", "name": "web-prod-1",     "type": "t3.medium", "state": "running", "az": "us-east-1a"},
        {"id": "i-0a1b2c3d4e5f6a7b9", "name": "web-prod-2",     "type": "t3.medium", "state": "running", "az": "us-east-1b"},
        {"id": "i-0c9d8e7f6a5b4c3d2", "name": "staging-worker", "type": "t3.small",  "state": "running", "az": "us-east-1a"},
        {"id": "i-0f1e2d3c4b5a69788", "name": "batch-2019",     "type": "t2.large",  "state": "stopped", "az": "us-east-1c"},
    ],
    "us-west-2": [
        {"id": "i-0aa11bb22cc33dd44", "name": "analytics-1",    "type": "m5.large",  "state": "running", "az": "us-west-2a"},
    ],
}
_MOCK_S3 = [
    {"name": "acme-prod-assets",   "public": False},
    {"name": "acme-app-logs",      "public": False},
    {"name": "acme-backups-2021",  "public": True},   # <-- oops, public
]
_MOCK_ALARMS = [
    {"name": "HighCPU-web-prod-2",     "state": "ALARM", "metric": "CPUUtilization 93% > 80%"},
    {"name": "5xx-errors-checkout",    "state": "OK",    "metric": "HTTPCode_5XX 0.1% < 1%"},
    {"name": "DiskSpace-staging",      "state": "OK",    "metric": "disk_used 61% < 85%"},
]


def _mock_find(region, instance_id):
    for i in _MOCK_EC2.get(region, []):
        if i["id"] == instance_id or i["name"] == instance_id:
            return i
    return None


# ─────────────────────────────────────────────────────────────────────────────
# READ-ONLY tools
# ─────────────────────────────────────────────────────────────────────────────
def list_ec2_instances(region: str = None, state: str = None) -> str:
    """List EC2 instances in a region, optionally filtered by state (running/stopped)."""
    region = region or DEFAULT_REGION
    if _use_real():
        import boto3
        ec2 = boto3.client("ec2", region_name=region)
        filters = [{"Name": "instance-state-name", "Values": [state]}] if state else []
        resp = ec2.describe_instances(Filters=filters)
        rows = []
        for r in resp["Reservations"]:
            for i in r["Instances"]:
                name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "-")
                rows.append({"id": i["InstanceId"], "name": name,
                             "type": i["InstanceType"], "state": i["State"]["Name"],
                             "az": i["Placement"]["AvailabilityZone"]})
    else:
        rows = [i for i in _MOCK_EC2.get(region, []) if state is None or i["state"] == state]

    if not rows:
        return f"No {state or ''} EC2 instances found in {region}."
    lines = [f"{len(rows)} instance(s) in {region}" + (f" (state={state})" if state else "") + ":"]
    for i in rows:
        lines.append(f"  {i['id']}  {i['name']:<16} {i['type']:<10} {i['state']:<9} {i['az']}")
    return "\n".join(lines)


def _s3_is_public(s3, name: str) -> bool:
    """Best-effort public check (returns False if we lack permission to tell)."""
    try:
        pab = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
        if all(pab.get(k) for k in ("BlockPublicAcls", "IgnorePublicAcls",
                                    "BlockPublicPolicy", "RestrictPublicBuckets")):
            return False   # fully locked down
    except Exception:
        pass
    try:
        return s3.get_bucket_policy_status(Bucket=name)["PolicyStatus"]["IsPublic"]
    except Exception:
        return False


def list_s3_buckets() -> str:
    """List S3 buckets and flag any that are publicly accessible."""
    if _use_real():
        import boto3
        s3 = boto3.client("s3")
        names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
        lines = [f"{len(names)} bucket(s):"]
        for n in names:
            lines.append(f"  {n}{'  ⚠ PUBLIC' if _s3_is_public(s3, n) else ''}")
        return "\n".join(lines)
    lines = [f"{len(_MOCK_S3)} bucket(s):"]
    for b in _MOCK_S3:
        flag = "  ⚠ PUBLIC" if b["public"] else ""
        lines.append(f"  {b['name']}{flag}")
    return "\n".join(lines)


def get_cloudwatch_alarms(state: str = None) -> str:
    """List CloudWatch alarms, optionally filtered by state (ALARM/OK/INSUFFICIENT_DATA)."""
    if _use_real():
        import boto3
        cw = boto3.client("cloudwatch")
        kwargs = {"StateValue": state} if state else {}
        alarms = cw.describe_alarms(**kwargs).get("MetricAlarms", [])
        rows = [{"name": a["AlarmName"], "state": a["StateValue"], "metric": a.get("MetricName", "")} for a in alarms]
    else:
        rows = [a for a in _MOCK_ALARMS if state is None or a["state"] == state]
    if not rows:
        return f"No alarms{f' in state {state}' if state else ''}."
    return "\n".join([f"{len(rows)} alarm(s):"] +
                     [f"  [{a['state']:<5}] {a['name']}  ({a['metric']})" for a in rows])


# ─────────────────────────────────────────────────────────────────────────────
# STATE-CHANGING tools  (these are gated behind human approval in the UI)
# ─────────────────────────────────────────────────────────────────────────────
def restart_ec2_instance(instance_id: str, region: str = None) -> str:
    """Reboot an EC2 instance by id."""
    region = region or DEFAULT_REGION
    if _use_real():
        import boto3
        boto3.client("ec2", region_name=region).reboot_instances(InstanceIds=[instance_id])
        return f"Reboot requested for {instance_id} in {region}."
    i = _mock_find(region, instance_id)
    if not i:
        return f"Instance {instance_id} not found in {region}."
    return f"[mock] Rebooting {i['id']} ({i['name']}). State stays 'running' after reboot."


def stop_ec2_instance(instance_id: str, region: str = None) -> str:
    """Stop an EC2 instance by id."""
    region = region or DEFAULT_REGION
    if _use_real():
        import boto3
        boto3.client("ec2", region_name=region).stop_instances(InstanceIds=[instance_id])
        return f"Stop requested for {instance_id} in {region}."
    i = _mock_find(region, instance_id)
    if not i:
        return f"Instance {instance_id} not found in {region}."
    i["state"] = "stopped"
    return f"[mock] Stopped {i['id']} ({i['name']}). State is now 'stopped'."


def start_ec2_instance(instance_id: str, region: str = None) -> str:
    """Start a stopped EC2 instance by id."""
    region = region or DEFAULT_REGION
    if _use_real():
        import boto3
        boto3.client("ec2", region_name=region).start_instances(InstanceIds=[instance_id])
        return f"Start requested for {instance_id} in {region}."
    i = _mock_find(region, instance_id)
    if not i:
        return f"Instance {instance_id} not found in {region}."
    i["state"] = "running"
    return f"[mock] Started {i['id']} ({i['name']}). State is now 'running'."


# ─────────────────────────────────────────────────────────────────────────────
# What we hand to the model + how we run / classify each tool.
# ─────────────────────────────────────────────────────────────────────────────
def _tool(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}

_REGION = {"type": "string", "description": f"AWS region. Defaults to {DEFAULT_REGION} if omitted.", "default": DEFAULT_REGION}
_IID = {"type": "string", "description": "EC2 instance id, e.g. 'i-0abc...'. Look it up first if the user gives a name."}

TOOLS = [
    _tool("list_ec2_instances",
          "List EC2 instances in a region. Use this first to find an instance's id when the user names it.",
          {"region": _REGION, "state": {"type": "string", "enum": ["running", "stopped"], "description": "optional state filter"}}, []),
    _tool("list_s3_buckets", "List S3 buckets and flag any that are public.", {}, []),
    _tool("get_cloudwatch_alarms", "List CloudWatch alarms, optionally by state.",
          {"state": {"type": "string", "enum": ["ALARM", "OK", "INSUFFICIENT_DATA"]}}, []),
    _tool("restart_ec2_instance", "Reboot an EC2 instance by id. STATE-CHANGING — requires approval.",
          {"instance_id": _IID, "region": _REGION}, ["instance_id"]),
    _tool("stop_ec2_instance", "Stop an EC2 instance by id. STATE-CHANGING — requires approval.",
          {"instance_id": _IID, "region": _REGION}, ["instance_id"]),
    _tool("start_ec2_instance", "Start a stopped EC2 instance by id. STATE-CHANGING — requires approval.",
          {"instance_id": _IID, "region": _REGION}, ["instance_id"]),
]

REGISTRY = {
    "list_ec2_instances": list_ec2_instances,
    "list_s3_buckets": list_s3_buckets,
    "get_cloudwatch_alarms": get_cloudwatch_alarms,
    "restart_ec2_instance": restart_ec2_instance,
    "stop_ec2_instance": stop_ec2_instance,
    "start_ec2_instance": start_ec2_instance,
}

# The heart of the guardrail: which tools may run freely vs need a human "Approve".
RISK = {
    "list_ec2_instances": "read_only",
    "list_s3_buckets": "read_only",
    "get_cloudwatch_alarms": "read_only",
    "restart_ec2_instance": "destructive",
    "stop_ec2_instance": "destructive",
    "start_ec2_instance": "destructive",
}

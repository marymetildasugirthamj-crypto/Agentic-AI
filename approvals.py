"""
Approval tickets + guarded execution (Phase 4 + 5).

Flow (no email — everything lives in the portal):
  engineer raises a savings finding  ->  a PENDING ticket is created
  manager opens the Approvals page   ->  sees it (notification = pending count)
  manager Approves / Rejects         ->  on approve, guarded execution runs
  every step is recorded             ->  audit history

Tickets are stored in approvals.json next to this file (a stand-in for DynamoDB).
Execution is DRY-RUN by default; it only touches the real account when the manager
ticks "execute for real" (which requires EXECUTE_LIVE to be allowed).
"""

import os
import json
import datetime as dt
from pathlib import Path

STORE = Path(__file__).resolve().parent / "approvals.json"

# category (from the optimization report) -> machine action + guidance
_ACTION = {
    "Unassociated Elastic IPs": ("release_eip",
        "The Elastic IP is not associated with any resource, so it is billed while idle.",
        "Allocate a new Elastic IP if one is later needed (the address will differ)."),
    "Idle / low-use EC2": ("stop_ec2",
        "Instance CPU is well below the threshold; stopping halts compute charges (EBS is retained).",
        "Start the instance again — data on attached EBS volumes is preserved."),
    "Unattached EBS volumes": ("delete_volume",
        "The volume is unattached ('available') and still incurs storage cost.",
        "Not reversible — restore from a snapshot if one exists. Review before approving."),
    "Old EBS snapshots": ("delete_snapshot",
        "The snapshot is older than the retention window.",
        "Not reversible once deleted — confirm it is not a needed backup."),
    "Stale S3 buckets": ("s3_lifecycle",
        "The bucket has not been updated recently; move to Glacier or expire old objects.",
        "Lifecycle changes are configuration-only and reversible."),
}


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load():
    if STORE.exists():
        try:
            return json.loads(STORE.read_text())
        except Exception:
            return []
    return []


def _save(tickets):
    STORE.write_text(json.dumps(tickets, indent=2))


def list_tickets(status: str = None):
    t = _load()
    return [x for x in t if status is None or x["status"] == status]


def pending_count() -> int:
    return len(list_tickets("PENDING"))


def _parse_id(resource: str) -> str:
    return resource.split(" (")[0].strip()


def create_ticket(finding: dict, requested_by: str = "engineer") -> dict:
    """Turn an optimization finding into a PENDING approval ticket."""
    tickets = _load()
    action, why, rollback = _ACTION.get(finding.get("category", ""),
                                        ("manual", "Manual review.", "N/A"))
    tid = f"TCK-{len(tickets) + 1:04d}"
    ticket = {
        "id": tid,
        "created_at": _now(),
        "requested_by": requested_by,
        "category": finding.get("category"),
        "resource": finding.get("resource"),
        "resource_id": _parse_id(finding.get("resource", "")),
        "region": finding.get("region", "us-east-1"),
        "action": action,
        "monthly_savings": round(finding.get("monthly_savings", 0) or 0, 2),
        "justification": why,
        "rollback": rollback,
        "status": "PENDING",
        "approver": None,
        "decided_at": None,
        "execution": None,
    }
    tickets.append(ticket)
    _save(tickets)
    return ticket


def already_requested(resource: str) -> bool:
    return any(t["resource"] == resource and t["status"] in ("PENDING", "APPROVED", "EXECUTED")
               for t in _load())


def decide(ticket_id: str, approver: str, approved: bool, execute_live: bool = False) -> dict:
    """Manager decision. On approve, run guarded execution."""
    tickets = _load()
    tk = next((t for t in tickets if t["id"] == ticket_id), None)
    if not tk:
        return {"error": "ticket not found"}
    tk["approver"] = approver
    tk["decided_at"] = _now()
    if not approved:
        tk["status"] = "REJECTED"
        _save(tickets)
        return tk
    tk["status"] = "APPROVED"
    tk["execution"] = _execute(tk, dry_run=not execute_live)
    tk["status"] = "FAILED" if tk["execution"].startswith("ERROR") else "EXECUTED"
    _save(tickets)
    return tk


# ── guarded execution (Phase 5) ──────────────────────────────────────────────
def _execute(tk: dict, dry_run: bool = True) -> str:
    action, rid, region = tk["action"], tk["resource_id"], tk.get("region", "us-east-1")
    live = os.getenv("USE_REAL_AWS", "false").lower() in ("1", "true", "yes")

    if action == "s3_lifecycle":
        return f"Manual step queued: apply lifecycle/Glacier to bucket '{rid}' (not auto-executed)."
    if dry_run or not live:
        verb = {"release_eip": "release Elastic IP", "stop_ec2": "stop instance",
                "delete_volume": "delete volume", "delete_snapshot": "delete snapshot"}.get(action, action)
        return f"DRY-RUN: would {verb} {rid} in {region}. (Enable 'execute for real' to perform it.)"
    try:
        import boto3
        ec2 = boto3.client("ec2", region_name=region)
        if action == "release_eip":
            ec2.release_address(AllocationId=rid); return f"Released Elastic IP {rid}."
        if action == "stop_ec2":
            ec2.stop_instances(InstanceIds=[rid]); return f"Stop requested for instance {rid}."
        if action == "delete_volume":
            ec2.delete_volume(VolumeId=rid); return f"Deleted volume {rid}."
        if action == "delete_snapshot":
            ec2.delete_snapshot(SnapshotId=rid); return f"Deleted snapshot {rid}."
        return f"No executor for action '{action}'."
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
        return f"ERROR executing {action} on {rid}: {code}"


def reset_store():
    if STORE.exists():
        STORE.unlink()

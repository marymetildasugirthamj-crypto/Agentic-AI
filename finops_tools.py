"""
Chat-FinOps Agent — the tool layer (cost + optimization).

HYBRID by design:
  • Cost visibility/analytics (Cost Explorer) tries the real CE API and falls back to
    realistic MOCK data when CE isn't enabled yet (it needs ~24h after you turn it on).
  • Optimization detectors (idle EC2, unused EBS/EIP, old snapshots, stale S3) run
    against the REAL account with boto3 — each degrades gracefully to a note if a
    permission is missing.

Everything here is READ-ONLY. No stop/resize/delete — this build is insight + savings.
Switch behaviour with USE_REAL_AWS=true (real) or false (all mock).
"""

import os
import datetime as dt

# ── rough on-demand prices (USD) for savings estimates — clearly approximate ──
EC2_MONTHLY = {
    "t2.micro": 8.5, "t2.small": 17, "t2.medium": 34, "t2.large": 67,
    "t3.micro": 7.5, "t3.small": 15, "t3.medium": 30, "t3.large": 60, "t3.xlarge": 120,
    "m5.large": 70, "m5.xlarge": 140, "c5.large": 62, "c5.xlarge": 124, "r5.large": 92,
}
EBS_GB_MONTH = 0.08        # gp3
SNAP_GB_MONTH = 0.05
EIP_MONTH = 3.60           # unassociated Elastic IP
S3_STALE_GB_MONTH = 0.023  # Standard storage; moving stale data to Glacier saves most of this

REGIONS = ["us-east-1", "ap-south-1"]


def _use_real() -> bool:
    return os.getenv("USE_REAL_AWS", "false").strip().lower() in ("1", "true", "yes")


def _regions():
    extra = os.getenv("AWS_DEFAULT_REGION")
    rs = list(REGIONS) + ([extra] if extra else [])
    return list(dict.fromkeys(rs))  # dedupe, keep order


def ec2_monthly(instance_type: str) -> float:
    return EC2_MONTHLY.get(instance_type, 50.0)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1–2 · COST VISIBILITY & ANALYTICS  (real Cost Explorer → mock fallback)
# ═══════════════════════════════════════════════════════════════════════════
def _ce_service_breakdown_real():
    import boto3
    ce = boto3.client("ce")
    end = dt.date.today().replace(day=1)
    start = (end - dt.timedelta(days=1)).replace(day=1)  # previous full month
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="MONTHLY", Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    groups = resp["ResultsByTime"][0]["Groups"]
    rows = [(g["Keys"][0], float(g["Metrics"]["UnblendedCost"]["Amount"])) for g in groups]
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows


# Mock is labelled clearly in the UI as sample data.
_MOCK_SERVICES = [
    ("Amazon RDS", 1840.20), ("Amazon EC2", 1520.75), ("Amazon S3", 410.60),
    ("AWS Lambda", 188.30), ("Amazon EKS", 176.00), ("Data Transfer", 132.10),
    ("Amazon ECR", 44.25), ("CloudWatch", 39.80), ("Other", 92.40),
]
_MOCK_ACCOUNTS = {
    "8364-…-8669 · production": 3218.40,
    "1122-…-3344 · staging": 892.15,
    "5566-…-7788 · data-platform": 1324.15,
}


def cost_by_account():
    """Estimated current-month bill per account. (Mock unless CE + Organizations wired.)"""
    return {"source": "mock", "accounts": _MOCK_ACCOUNTS,
            "total": round(sum(_MOCK_ACCOUNTS.values()), 2)}


def service_breakdown():
    """Cost per AWS service for the last full month. Real CE → mock fallback."""
    if _use_real():
        try:
            rows = _ce_service_breakdown_real()
            if rows:
                return {"source": "real", "services": rows, "total": round(sum(v for _, v in rows), 2)}
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
            return {"source": f"mock (Cost Explorer not ready: {code})",
                    "services": _MOCK_SERVICES, "total": round(sum(v for _, v in _MOCK_SERVICES), 2)}
    return {"source": "mock", "services": _MOCK_SERVICES,
            "total": round(sum(v for _, v in _MOCK_SERVICES), 2)}


def cost_trend(months: int = 6):
    """Monthly total spend for the last N months (mock trend for the demo)."""
    base = [3980, 4120, 4310, 4180, 4460, 4444]
    today = dt.date.today()
    labels = []
    for i in range(months - 1, -1, -1):
        m = (today.replace(day=1) - dt.timedelta(days=1))
        y, mo = today.year, today.month - i
        while mo <= 0:
            mo += 12; y -= 1
        labels.append(dt.date(y, mo, 1).strftime("%b %Y"))
    series = base[-months:]
    return {"labels": labels, "values": series}


def forecast_month_end():
    """Simple end-of-month forecast from month-to-date run rate (mock)."""
    today = dt.date.today()
    days_in_month = 30
    mtd = 2960.0
    run_rate = mtd / max(today.day, 1)
    return {"month_to_date": round(mtd, 2), "forecast": round(run_rate * days_in_month, 2),
            "budget": 5000.0}


def month_over_month():
    """Biggest month-over-month movers by service (mock, with a spike to explain)."""
    return [
        {"service": "Amazon RDS", "change_pct": +38, "delta": +505, "note": "new read replica + storage growth"},
        {"service": "Amazon EC2", "change_pct": +6, "delta": +86, "note": "one m5.xlarge added"},
        {"service": "Amazon S3", "change_pct": -4, "delta": -17, "note": "lifecycle expired old logs"},
        {"service": "AWS Lambda", "change_pct": +2, "delta": +4, "note": "steady"},
    ]


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 · OPTIMIZATION ENGINE  (REAL account, read-only; graceful fallback)
# ═══════════════════════════════════════════════════════════════════════════
def find_idle_ec2(cpu_threshold: float = 10.0, days: int = 14):
    """Running EC2 whose average CPU over `days` is below the threshold → idle/low-use."""
    if not _use_real():
        return [{"resource": "i-0mock11 (batch-runner)", "region": "us-east-1", "type": "t3.large",
                 "detail": "avg CPU 2.1% over 14d", "monthly_savings": ec2_monthly("t3.large")}]
    import boto3
    findings = []
    for region in _regions():
        try:
            ec2 = boto3.client("ec2", region_name=region)
            cw = boto3.client("cloudwatch", region_name=region)
            resp = ec2.describe_instances(Filters=[{"Name": "instance-state-name", "Values": ["running"]}])
            for r in resp["Reservations"]:
                for i in r["Instances"]:
                    iid, itype = i["InstanceId"], i["InstanceType"]
                    name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), iid)
                    stats = cw.get_metric_statistics(
                        Namespace="AWS/EC2", MetricName="CPUUtilization",
                        Dimensions=[{"Name": "InstanceId", "Value": iid}],
                        StartTime=dt.datetime.utcnow() - dt.timedelta(days=days),
                        EndTime=dt.datetime.utcnow(), Period=86400, Statistics=["Average"])
                    pts = stats.get("Datapoints", [])
                    if not pts:
                        continue
                    avg = sum(p["Average"] for p in pts) / len(pts)
                    if avg < cpu_threshold:
                        findings.append({"resource": f"{iid} ({name})", "region": region, "type": itype,
                                         "detail": f"avg CPU {avg:.1f}% over {days}d",
                                         "monthly_savings": ec2_monthly(itype)})
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
            findings.append({"resource": f"(region {region})", "region": region, "type": "-",
                             "detail": f"skipped: {code}", "monthly_savings": 0.0})
    return findings


def find_unused_ebs():
    """EBS volumes in the 'available' state (not attached to anything)."""
    if not _use_real():
        return [{"resource": "vol-0mock (100 GiB gp3)", "region": "us-east-1",
                 "detail": "available (unattached)", "monthly_savings": round(100 * EBS_GB_MONTH, 2)}]
    import boto3
    findings = []
    for region in _regions():
        try:
            ec2 = boto3.client("ec2", region_name=region)
            for v in ec2.describe_volumes(Filters=[{"Name": "status", "Values": ["available"]}])["Volumes"]:
                gb = v["Size"]
                findings.append({"resource": f"{v['VolumeId']} ({gb} GiB {v.get('VolumeType','')})",
                                 "region": region, "detail": "available (unattached)",
                                 "monthly_savings": round(gb * EBS_GB_MONTH, 2)})
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
            findings.append({"resource": f"(region {region})", "region": region,
                             "detail": f"skipped: {code}", "monthly_savings": 0.0})
    return findings


def find_unused_eips():
    """Elastic IPs that are allocated but not associated (billed while idle)."""
    if not _use_real():
        return [{"resource": "eipalloc-0mock (52.1.2.3)", "region": "us-east-1",
                 "detail": "not associated", "monthly_savings": EIP_MONTH}]
    import boto3
    findings = []
    for region in _regions():
        try:
            ec2 = boto3.client("ec2", region_name=region)
            for a in ec2.describe_addresses()["Addresses"]:
                if not a.get("AssociationId"):
                    findings.append({"resource": f"{a.get('AllocationId','?')} ({a.get('PublicIp','')})",
                                     "region": region, "detail": "not associated",
                                     "monthly_savings": EIP_MONTH})
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
            findings.append({"resource": f"(region {region})", "region": region,
                             "detail": f"skipped: {code}", "monthly_savings": 0.0})
    return findings


def find_old_snapshots(days: int = 90):
    """Owner-owned EBS snapshots older than `days` — cleanup / archival candidates."""
    if not _use_real():
        return [{"resource": "snap-0mock (40 GiB)", "region": "us-east-1",
                 "detail": "312 days old", "monthly_savings": round(40 * SNAP_GB_MONTH, 2)}]
    import boto3
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    findings = []
    for region in _regions():
        try:
            ec2 = boto3.client("ec2", region_name=region)
            snaps = ec2.describe_snapshots(OwnerIds=["self"]).get("Snapshots", [])
            for s in snaps:
                if s["StartTime"] < cutoff:
                    gb = s.get("VolumeSize", 8)
                    age = (dt.datetime.now(dt.timezone.utc) - s["StartTime"]).days
                    findings.append({"resource": f"{s['SnapshotId']} ({gb} GiB)", "region": region,
                                     "detail": f"{age} days old", "monthly_savings": round(gb * SNAP_GB_MONTH, 2)})
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
            findings.append({"resource": f"(region {region})", "region": region,
                             "detail": f"skipped: {code}", "monthly_savings": 0.0})
    return findings


def find_stale_s3(days: int = 30):
    """S3 buckets with no object updated in `days` — lifecycle/Glacier candidates.
    Reuses the freshness idea; estimates savings from bucket size (CloudWatch)."""
    if not _use_real():
        return [{"resource": "textile-images-dev", "region": "-", "detail": "no update in 267d (12 GB)",
                 "monthly_savings": round(12 * S3_STALE_GB_MONTH, 2)}]
    import boto3
    findings = []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    now = dt.datetime.now(dt.timezone.utc)
    try:
        base = boto3.client("s3")
        cw = boto3.client("cloudwatch", region_name="us-east-1")
        for b in base.list_buckets().get("Buckets", []):
            name = b["Name"]
            try:
                loc = base.get_bucket_location(Bucket=name).get("LocationConstraint") or "us-east-1"
                region = "eu-west-1" if loc == "EU" else loc
                cli = boto3.client("s3", region_name=region)
                latest, recent = None, False
                for page in cli.get_paginator("list_objects_v2").paginate(Bucket=name, PaginationConfig={"MaxItems": 3000}):
                    for o in page.get("Contents", []):
                        if latest is None or o["LastModified"] > latest:
                            latest = o["LastModified"]
                        if o["LastModified"] >= cutoff:
                            recent = True; break
                    if recent:
                        break
                if latest is None or recent:
                    continue  # empty handled elsewhere / fresh bucket -> skip
                age = (now - latest).days
                gb = _bucket_size_gb(cw, name)
                sav = round(gb * S3_STALE_GB_MONTH, 2) if gb else 0.0
                size_txt = f", {gb:.1f} GB" if gb else ""
                findings.append({"resource": name, "region": region,
                                 "detail": f"no update in {age}d{size_txt}", "monthly_savings": sav})
            except Exception:
                continue
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
        findings.append({"resource": "(S3)", "region": "-", "detail": f"skipped: {code}", "monthly_savings": 0.0})
    return findings


def _bucket_size_gb(cw, name):
    try:
        r = cw.get_metric_statistics(
            Namespace="AWS/S3", MetricName="BucketSizeBytes",
            Dimensions=[{"Name": "BucketName", "Value": name},
                        {"Name": "StorageType", "Value": "StandardStorage"}],
            StartTime=dt.datetime.utcnow() - dt.timedelta(days=3),
            EndTime=dt.datetime.utcnow(), Period=86400, Statistics=["Average"])
        pts = r.get("Datapoints", [])
        return (max(p["Average"] for p in pts) / 1e9) if pts else 0.0
    except Exception:
        return 0.0


def optimization_report(cpu_threshold: float = 10.0):
    """Run every detector and return a flat, categorized report with total savings."""
    cats = [
        ("Idle / low-use EC2", find_idle_ec2(cpu_threshold)),
        ("Unattached EBS volumes", find_unused_ebs()),
        ("Unassociated Elastic IPs", find_unused_eips()),
        ("Old EBS snapshots", find_old_snapshots()),
        ("Stale S3 buckets", find_stale_s3()),
    ]
    items, total = [], 0.0
    for cat, rows in cats:
        for r in rows:
            r = {**r, "category": cat}
            items.append(r)
            total += r.get("monthly_savings", 0.0) or 0.0
    return {"items": items, "total_monthly_savings": round(total, 2),
            "annual_savings": round(total * 12, 2)}


def format_optimization(rep: dict) -> str:
    """Text summary — this is what the LangChain tool returns to the model."""
    lines = [f"Potential savings: ${rep['total_monthly_savings']:,}/mo (~${rep['annual_savings']:,}/yr)."]
    for it in rep["items"]:
        if it.get("monthly_savings"):
            lines.append(f"  [{it['category']}] {it['resource']} — {it['detail']} → ${it['monthly_savings']:.2f}/mo")
    if len(lines) == 1:
        lines.append("  No clear waste found by the current detectors.")
    return "\n".join(lines)

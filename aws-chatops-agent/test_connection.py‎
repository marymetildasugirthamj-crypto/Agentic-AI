"""
Pre-flight check for LIVE AWS mode.

Run this BEFORE `streamlit run app.py` to confirm your credentials + permissions
work. It reads the same .env the app does, calls each read-only tool once, and
prints a clear PASS/FAIL per AWS service. It NEVER changes anything.

    python test_connection.py
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    load_dotenv(_here / ".env")
    load_dotenv(_here / "env")
except ImportError:
    pass


def main() -> None:
    real = os.getenv("USE_REAL_AWS", "false").lower() in ("1", "true", "yes")
    region = os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION") or "us-east-1"
    print("=" * 60)
    print(f"USE_REAL_AWS = {real}   |   region = {region}")
    print("=" * 60)

    if not real:
        print("\n⚠ USE_REAL_AWS is not true — the app will use MOCK data.")
        print("  Set USE_REAL_AWS=true in .env to hit your real account, then re-run.")
        return

    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError

    # 0) Who am I?
    try:
        ident = boto3.client("sts").get_caller_identity()
        print(f"\n✅ Credentials OK — {ident['Arn']}")
        print(f"   Account: {ident['Account']}")
    except (ClientError, NoCredentialsError) as e:
        print(f"\n❌ Credentials FAILED — {e}")
        print("   Fix: put keys in .env (AWS_ACCESS_KEY_ID/SECRET) or run `aws configure`.")
        return

    checks = [
        ("EC2  (ec2:DescribeInstances)",
         lambda: boto3.client("ec2", region_name=region).describe_instances(MaxResults=5)),
        ("S3   (s3:ListAllMyBuckets)",
         lambda: boto3.client("s3").list_buckets()),
        ("CloudWatch (cloudwatch:DescribeAlarms)",
         lambda: boto3.client("cloudwatch", region_name=region).describe_alarms(MaxRecords=5)),
    ]
    print()
    ok = True
    for label, fn in checks:
        try:
            fn()
            print(f"✅ {label}")
        except Exception as e:
            ok = False
            code = getattr(getattr(e, "response", {}), "get", lambda *_: "")("Error", {})
            print(f"❌ {label} — {type(e).__name__}: {e}")

    print("\n" + ("🎉 All read-only checks passed — you can run `streamlit run app.py` live."
                  if ok else
                  "⚠ Some checks failed — add the missing IAM permissions (see iam-policy.json)."))
    print("Note: restart/stop/start need ec2:RebootInstances/StopInstances/StartInstances,")
    print("      and are only attempted after you click Approve in the UI.")


if __name__ == "__main__":
    main()

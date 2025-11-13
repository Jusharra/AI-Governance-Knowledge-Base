import json, hashlib, os, time, pathlib, boto3
from urllib.parse import quote
from botocore.exceptions import ClientError
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=False)


AUDIT_PATH = os.getenv("AUDIT_LOG_PATH","audits/audit_log.jsonl")
pathlib.Path(AUDIT_PATH).parent.mkdir(parents=True, exist_ok=True)

EVB = boto3.client("events", region_name=os.getenv("AWS_REGION","us-east-1"))
S3 = boto3.client("s3", region_name=os.getenv("AWS_REGION","us-east-1"))

def _last_hash():
    try:
        with open(AUDIT_PATH,"r",encoding="utf-8") as f:
            last = None
            for line in f: last = json.loads(line)
            return last["entry_hash"] if last else "GENESIS"
    except FileNotFoundError:
        return "GENESIS"

def log_event(event: dict):
    event["ts"] = int(time.time())
    event["prev_hash"] = _last_hash()
    payload = json.dumps(event, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256((event["prev_hash"]+payload).encode("utf-8")).hexdigest()
    event["entry_hash"] = h
    with open(AUDIT_PATH,"a",encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False)+"\n")

    # Emit EventBridge event
    try:
        EVB.put_events(Entries=[{
            "Source":"ai.gov.kb",
            "DetailType": os.getenv("EVENTBRIDGE_DETAIL_TYPE","AIGovAudit"),
            "Detail": json.dumps({
                "entry_hash": h,
                "inj": event.get("inj",{}),
                "confidence": event.get("confidence"),
                "model": event.get("model_used"),
                "frameworks": list({r.get('framework') for r in event.get("retrieved",[]) if r.get('framework')})
            }),
            "EventBusName": os.getenv("EVENTBUS_NAME","default")
        }])
    except ClientError as e:
        # non-fatal
        pass

    return h

def snapshot_to_s3():
    bucket = os.getenv("AUDIT_S3_BUCKET")
    if not bucket: return None
    key = f"audit_logs/audit_{int(time.time())}.jsonl"
    with open(AUDIT_PATH,"rb") as f:
        S3.put_object(Bucket=bucket, Key=key, Body=f, ContentType="application/jsonl")
    return f"s3://{bucket}/{key}"

def presign_url(s3_key: str, expires_seconds: int = 3600) -> str | None:
    """Return a presigned GET URL for an S3 object key, or None if misconfigured."""
    if not _BUCKET or not s3_key:
        return None
    try:
        return _S3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": _BUCKET, "Key": s3_key},
            ExpiresIn=expires_seconds,
        )
    except Exception:
        return None

def presign_many(keys: list[str], expires_seconds: int = 3600) -> list[dict]:
    """Batch presign; returns [{'key': ..., 'url': ...}, ...]"""
    out = []
    for k in keys or []:
        out.append({"key": k, "url": presign_url(k, expires_seconds)})
    return out    

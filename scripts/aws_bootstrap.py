# scripts/aws_bootstrap.py
import os, sys, json, time, uuid, boto3, botocore
from botocore.exceptions import ClientError

# --------------------
# Clients / Globals
# --------------------
REGION = os.getenv("AWS_REGION","us-east-1")
s3     = boto3.client("s3",          region_name=REGION)
sns    = boto3.client("sns",         region_name=REGION)
events = boto3.client("events",      region_name=REGION)
am     = boto3.client("auditmanager",region_name=REGION)
config = boto3.client("config",      region_name=REGION)
sts    = boto3.client("sts",         region_name=REGION)
iam    = boto3.client("iam",         region_name=REGION)

ACCOUNT_ID = sts.get_caller_identity()["Account"]
bucket = os.getenv("AUDIT_S3_BUCKET") or f"ai-gov-audits-{ACCOUNT_ID}-{str(uuid.uuid4())[:8]}"

# --------------------
# IAM role validation
# --------------------
def ensure_role_arn() -> str:
    role_arn = os.getenv("AUDIT_MANAGER_ROLE_ARN")
    if not role_arn:
        raise RuntimeError("Set AUDIT_MANAGER_ROLE_ARN in your environment/.env")
    try:
        role_name = role_arn.split("/")[-1]
        iam.get_role(RoleName=role_name)
    except botocore.exceptions.ClientError as e:
        raise RuntimeError(f"IAM role not found or not accessible: {role_arn} ({e})")
    return role_arn

# --------------------
# S3 / SNS / EventBridge / Config
# --------------------
# --- Bucket naming (deterministic) ---
ACCOUNT_ID = sts.get_caller_identity()["Account"]
DEFAULT_BUCKET = f"ai-gov-audits-{ACCOUNT_ID}-primary"
bucket = os.getenv("AUDIT_S3_BUCKET") or DEFAULT_BUCKET

def ensure_bucket(b):
    # 1) If it already exists, don't create a new one
    try:
        s3.head_bucket(Bucket=b)
        print(f"[S3] Bucket exists: {b}")
        return
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code not in ("404", "NoSuchBucket", "NotFound"):
            # AccessDenied also means it exists (but different owner) – avoid name collisions
            if code in ("403", "AccessDenied"):
                raise RuntimeError(f"[S3] Bucket name '{b}' is taken (AccessDenied). Set AUDIT_S3_BUCKET to a unique name you own.")
            raise

    # 2) Create if missing (respect region rules)
    kwargs = {"Bucket": b}
    if REGION != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": REGION}
    s3.create_bucket(**kwargs)
    print(f"[S3] Created bucket: {b}")

    # 3) (Optional) baseline hardening
    s3.put_public_access_block(
        Bucket=b,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True
        }
    )
    # Server-side encryption by default
    s3.put_bucket_encryption(
        Bucket=b,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        }
    )


def ensure_sns():
    name = "AIGovAuditTopic"
    resp = sns.create_topic(Name=name)
    arn = resp["TopicArn"]
    print(f"[SNS] Topic: {arn}")
    email = os.getenv("SNS_SUBSCRIBER_EMAIL")
    if email:
        sns.subscribe(TopicArn=arn, Protocol="email", Endpoint=email)
        print(f"[SNS] Subscription email sent to {email} (confirm it).")
    return arn

def ensure_eventbridge(topic_arn):
    rule_name = "AIGovAuditRule"
    # EventBridge pattern (simple): match our source and detail-type
    pattern = {
        "source":      ["ai.gov.kb"],
        "detail-type": ["AIGovAudit"]
    }
    events.put_rule(
        Name=rule_name,
        EventPattern=json.dumps(pattern),
        State="ENABLED"
    )
    events.put_targets(
        Rule=rule_name,
        Targets=[{"Id":"sns-target","Arn":topic_arn}]
    )
    arn = events.describe_rule(Name=rule_name)["Arn"]
    print(f"[EVB] Rule {rule_name} -> SNS")
    return arn

def ensure_config():
    # Recorder & delivery
    recorders = config.describe_configuration_recorders().get("ConfigurationRecorders",[])
    if not recorders:
        config.put_configuration_recorder(ConfigurationRecorder={
            "name":"default",
            "roleARN": f"arn:aws:iam::{ACCOUNT_ID}:role/service-role/AWSConfigRole"  # ensure/adjust if needed
        })
        print("[Config] Recorder created (ensure role exists).")
    deliveries = config.describe_delivery_channels().get("DeliveryChannels",[])
    if not deliveries:
        config.put_delivery_channel(DeliveryChannel={"name":"default"})
        print("[Config] Delivery channel created.")
    # Start
    config.start_configuration_recorder(ConfigurationRecorderName="default")
    print("[Config] Recorder started.")

    # Managed rules
    def put_rule(name, identifier, params=None):
        r = {"ConfigRuleName":name,"Source":{"Owner":"AWS","SourceIdentifier":identifier}}
        if params: r["InputParameters"]=json.dumps(params)
        try:
            config.put_config_rule(ConfigRule=r)
            print(f"[Config] Rule upserted: {name} ({identifier})")
        except ClientError as e:
            print(f"[Config] Rule error: {e}")

    put_rule("iam-console-mfa", "IAM_USER_MFA_ENABLED")
    put_rule("root-mfa-enabled","ROOT_ACCOUNT_MFA_ENABLED")

# --------------------
# Audit Manager helpers (manual pagination)
# --------------------
def list_assessments_all():
    items, token = [], None
    while True:
        kwargs = {}
        if token: kwargs["nextToken"] = token
        resp = am.list_assessments(**kwargs)
        items.extend(resp.get("assessmentMetadata", []))
        token = resp.get("nextToken")
        if not token: break
    return items

def list_controls_all(control_type="Custom"):
    items, token = [], None
    while True:
        kwargs = {"controlType": control_type}
        if token: kwargs["nextToken"] = token
        resp = am.list_controls(**kwargs)
        items.extend(resp.get("controlMetadataList", []))
        token = resp.get("nextToken")
        if not token: break
    return items

def list_frameworks_all(framework_type="Custom"):
    items, token = [], None
    while True:
        kwargs = {"frameworkType": framework_type}
        if token: kwargs["nextToken"] = token
        resp = am.list_assessment_frameworks(**kwargs)
        items.extend(resp.get("frameworkMetadataList", []))
        token = resp.get("nextToken")
        if not token: break
    return items

# --------------------
# Audit Manager: prefer existing assessment; else create tiny custom one
# --------------------
def ensure_audit_manager():
    """
    Prefer an existing assessment by ID or Name (fuzzy + whitespace-normalized).
    Falls back to creating a tiny custom framework only if neither is provided/found.
    Returns: (assessment_id, control_id_for_evidence, control_set_id)
    """
    def norm(s: str) -> str:
        return " ".join(s.split()).strip().lower() if isinstance(s, str) else ""

    target_id   = (os.getenv("AUDIT_MANAGER_ASSESSMENT_ID") or "").strip()
    target_name = os.getenv("AUDIT_MANAGER_ASSESSMENT_NAME")

    # Helpful: show caller/region so you immediately spot profile/region drift
    ident = sts.get_caller_identity()
    print(f"[Debug] account={ident['Account']} arn={ident['Arn']} region={REGION}")

    # --- helper: list all assessments (manual pagination) ---
    def _list_assessments_all():
        items, token = [], None
        while True:
            kwargs = {}
            if token: kwargs["nextToken"] = token
            resp = am.list_assessments(**kwargs)
            items.extend(resp.get("assessmentMetadata", []))
            token = resp.get("nextToken")
            if not token: break
        return items

    # --- 1) use ID directly if provided ---
    if target_id:
        metas = _list_assessments_all()
        ids = {m["id"] for m in metas}
        if target_id not in ids:
            print("[AuditManager] Assessments visible to current creds/region:")
            for m in metas:
                print(f"  - {m.get('name')} ({m.get('id')})")
            raise RuntimeError(
                f"Assessment ID not found here: {target_id} (region={REGION}). "
                "Set AWS_PROFILE/AWS_REGION for Python, or attach auditmanager:GetAssessment/ListAssessments."
            )

        a = am.get_assessment(assessmentId=target_id)["assessment"]
        cs = a["framework"]["controlSets"][0]
        control_set_id = cs["id"]
        hint = os.getenv("AUDIT_MANAGER_CONTROL_HINT")  # e.g., "CC6.6"
        chosen = cs["controls"][0]
        if hint:
            for c in cs["controls"]:
                if norm(hint) in norm(c.get("name","")+" "+c.get("id","")):
                    chosen = c; break
        # AFTER (correct keys)
        meta = a.get("metadata", {})
        print(f"[AuditManager] Using assessment by ID: {meta.get('name')} ({meta.get('id')})")
        print(f"[AuditManager] control_set_id={control_set_id} control_id={chosen['id']}")
        return meta.get("id"), chosen["id"], control_set_id


    # --- 2) fuzzy name match if provided ---
    if target_name:
        metas = list_assessments_all()
        nn = norm(target_name)
        # try exact-normalized first, then contains
        chosen_meta = next((m for m in metas if norm(m.get("name")) == nn), None)
        if not chosen_meta:
            chosen_meta = next((m for m in metas if nn in norm(m.get("name"))), None)
        if not chosen_meta:
            # helpful debug dump
            print("[AuditManager] Existing assessments in this region/account:")
            for m in metas:
                print(f"  - {m.get('name')}  ({m.get('id')})")
            raise RuntimeError(f"Audit Manager assessment not found (region={REGION}): {target_name}")

        assessment_id = chosen_meta["id"]
        a = am.get_assessment(assessmentId=assessment_id)["assessment"]
        cs = a["framework"]["controlSets"][0]
        control_set_id = cs["id"]
        hint = os.getenv("AUDIT_MANAGER_CONTROL_HINT")
        chosen = cs["controls"][0]
        if hint:
            for c in cs["controls"]:
                if norm(hint) in norm(c.get("name","")+" "+c.get("id","")):
                    chosen = c; break
        print(f"[AuditManager] Using existing assessment by name: {a['name']} ({assessment_id})")
        print(f"[AuditManager] control_set_id={control_set_id} control_id={chosen['id']}")
        return assessment_id, chosen["id"], control_set_id

    # --- 3) fallback: create minimal custom framework (unchanged from earlier) ---
    def list_controls_all(control_type="Custom"):
        items, token = [], None
        while True:
            kwargs = {"controlType": control_type}
            if token: kwargs["nextToken"] = token
            resp = am.list_controls(**kwargs)
            items.extend(resp.get("controlMetadataList", []))
            token = resp.get("nextToken")
            if not token: break
        return items

    def list_frameworks_all(framework_type="Custom"):
        items, token = [], None
        while True:
            kwargs = {"frameworkType": framework_type}
            if token: kwargs["nextToken"] = token
            resp = am.list_assessment_frameworks(**kwargs)
            items.extend(resp.get("frameworkMetadataList", []))
            token = resp.get("nextToken")
            if not token: break
        return items

    def find_control_by_name(name):
        for meta in list_controls_all("Custom"):
            ctrl = am.get_control(controlId=meta["id"])["control"]
            if ctrl["name"] == name:
                return ctrl
        return None

    def find_framework_by_name(name):
        for fw in list_frameworks_all("Custom"):
            if fw.get("name") == name:
                return fw
        return None

    control_name = "RAG-Audit-Log-Integrity"
    ctrl = find_control_by_name(control_name)
    if ctrl is None:
        ctrl = am.create_control(
            name=control_name,
            description=("Verify tamper-evident audit logging for AI governance RAG: "
                         "hash-chained JSONL, periodic S3 snapshots, EventBridge emission, and "
                         "Audit Manager evidence imports."),
            testingInformation=("Inspect hash chain continuity; verify S3 snapshots; confirm EventBridge events; "
                                "validate evidence imported into assessment."),
            actionPlanTitle="Enable chain-of-custody for AI audit logs",
            actionPlanInstructions="Document the logging design; configure S3 lifecycle; schedule periodic imports.",
            controlMappingSources=[{
                "sourceName":"Tamper-Evident Audit Log",
                "sourceDescription":"Evidence: S3 snapshots, audit_log.jsonl, EventBridge events",
                "sourceType":"MANUAL",
                "sourceSetUpOption":"Procedural_Controls_Mapping"
            }]
        )["control"]
        print(f"[AuditManager] Control created: {ctrl['id']}")
    else:
        print(f"[AuditManager] Control exists: {ctrl['id']}")
    control_id = ctrl["id"]

    framework_name = "AI-Gov-RAG-Framework"
    fw_meta = find_framework_by_name(framework_name)
    if fw_meta is None:
        fw = am.create_assessment_framework(
            name=framework_name,
            controlSets=[{"name":"AI-RAG-Controls","controls":[{"id": control_id}]}]
        )["framework"]
        framework_id = fw["id"]
        print(f"[AuditManager] Framework created: {framework_id}")
    else:
        framework_id = fw_meta["id"]
        print(f"[AuditManager] Framework exists: {framework_id}")

    role_arn = ensure_role_arn()
    a = am.create_assessment(
        name=f"AI-Gov-RAG-Assessment-{int(time.time())}",
        frameworkId=framework_id,
        roles=[{"roleArn": role_arn, "roleType": "PROCESS_OWNER"}]
    )["assessment"]
    cs = a["framework"]["controlSets"][0]
    print(f"[AuditManager] Assessment created: {a['id']}")
    return a["id"], cs["controls"][0]["id"], cs["id"]

# --------------------
# Main
# --------------------
if __name__ == "__main__":
    ensure_bucket(bucket)
    topic_arn = ensure_sns()
    ensure_eventbridge(topic_arn)
    ensure_config()
    assess_id, ctl_id, cset_id = ensure_audit_manager()

    print("\n=== OUTPUT ENV VALUES ===")
    print(f"AUDIT_S3_BUCKET={bucket}")
    print(f"SNS_TOPIC_ARN={topic_arn}")
    print(f"AUDIT_MANAGER_ASSESSMENT_ID={assess_id}")
    print(f"AUDIT_MANAGER_CONTROL_ID={ctl_id}")
    print(f"AUDIT_MANAGER_CONTROL_SET_ID={cset_id}")

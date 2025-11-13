import os, sys, json, boto3
from urllib.parse import urlparse
from botocore.exceptions import ClientError

REGION = os.getenv("AWS_REGION","us-east-1")
am = boto3.client("auditmanager", region_name=REGION)

assessment_id = os.getenv("AUDIT_MANAGER_ASSESSMENT_ID")
control_id = os.getenv("AUDIT_MANAGER_CONTROL_ID")
control_set_id = os.getenv("AUDIT_MANAGER_CONTROL_SET_ID")
s3_uri = os.getenv("EVIDENCE_S3_URI")

if not all([assessment_id, control_id, control_set_id, s3_uri]):
    print("Set AUDIT_MANAGER_ASSESSMENT_ID, AUDIT_MANAGER_CONTROL_ID, AUDIT_MANAGER_CONTROL_SET_ID, and EVIDENCE_S3_URI")
    sys.exit(1)

p = urlparse(s3_uri)
s3_arn = f"arn:aws:s3:::{p.netloc}/{p.path.lstrip('/')}"

try:
    resp = am.batch_import_evidence_to_assessment(
        assessmentId=assessment_id,
        controlSetId=control_set_id,
        controlId=control_id,
        manualEvidence=[{
            "evidenceFileName": os.path.basename(p.path),
            "s3Resources":[{"s3ResourcePath": s3_arn}],
            "description":"Tamper-evident query log snapshot (hash-chained)."
        }]
    )
    print("Imported evidence:", json.dumps(resp, indent=2, default=str))
except ClientError as e:
    print("Audit Manager error:", e)
    sys.exit(2)

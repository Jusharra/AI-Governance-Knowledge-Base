import yaml, os
from pathlib import Path

POL = yaml.safe_load(Path("security/model_governance.yaml").read_text())

def check_model_governance():
    region = os.getenv("AWS_REGION","us-east-1")
    if region not in POL["allow_regions"]:
        return False, f"Region {region} not allowed"
    # add other org-specific checks here
    return True, "OK"

def allowed_models():
    return POL["allow_models"]

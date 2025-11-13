#!/bin/bash
# setup_structure.sh
# Create the folder and file structure for Project 12 - AI Governance Knowledge Base

#!/bin/bash
BASE_DIR="."

echo "🚀 Creating directory structure for $BASE_DIR ..."

# Core directories
mkdir -p $BASE_DIR/{app,data,vectors,scripts,security,audits}

# Touch base files
touch $BASE_DIR/README.md
touch $BASE_DIR/.env.example
touch $BASE_DIR/requirements.txt
touch $BASE_DIR/Makefile

# App files
touch $BASE_DIR/app/{main.py,guardrails.py,retriever.py,governance.py,logger.py,security_eval.py}

# Data files
touch $BASE_DIR/data/{controls_soc2.csv,controls_nist80053.csv,controls_iso42001.csv,policies_internal.csv,evidence_map.json}

# Vectors dir (empty for now)
echo "# Vector data will be generated here by ingest.py" > $BASE_DIR/vectors/README.txt

# Scripts
touch $BASE_DIR/scripts/{ingest.py,seed_internal_policy.py}

# Security configs
touch $BASE_DIR/security/{model_governance.yaml,pii_patterns.yaml,prompt_injection_rules.yaml}

# Audits
touch $BASE_DIR/audits/audit_log.jsonl

echo "✅ Folder and file structure created successfully."

# Optional: tree output (if tree is installed)
if command -v tree &> /dev/null
then
    tree genai-architecture-portfolio
else
    echo "💡 Tip: install 'tree' to visualize structure: sudo apt install tree"
fi

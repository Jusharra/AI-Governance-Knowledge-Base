init:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
ingest:
	. .venv/bin/activate && python scripts/ingest.py
run:
	. .venv/bin/activate && streamlit run app/main.py
auditmanager:GetAssessment
auditmanager:ListAssessments

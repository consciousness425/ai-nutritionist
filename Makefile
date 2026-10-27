# Convenience targets. See README.md.
PY := .venv/bin/python

.PHONY: setup run dev-web test evals

setup:            ## install Python deps and build the web UI
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	cd web && npm install && npm run build

run:              ## API + built UI on http://localhost:8000
	.venv/bin/uvicorn nutribot.server:app --reload

dev-web:          ## Vite dev server on :5173 (needs `make run` in another terminal)
	cd web && npm run dev

test:             ## unit tests, no credentials needed
	$(PY) -m pytest -q

evals:            ## end-to-end scenarios (uses credentials)
	$(PY) evals/run_evals.py

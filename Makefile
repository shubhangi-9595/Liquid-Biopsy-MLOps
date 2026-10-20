# Shortcuts for common tasks. Run `make help` to list them.
PY ?= python
export PYTHONPATH := src
export MLFLOW_DISABLE_AGENT_HINT := 1

LOCAL_DB_URL := postgresql://lb:lbpass@localhost:5432/liquidbiopsy
KIND_CLUSTER := liquid-biopsy

.PHONY: help install validate train test test-db lint format serve serve-db db-up db-down drift \
        mlflow-ui leakage docker-build docker-test kind-up kind-deploy kind-down k8s-render bicep-build

help:  ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-13s %s\n", $$1, $$2}'

# ----------------------------------------------------------------------------- ML
install:  ## Install all dependencies (training, serving, dev)
	$(PY) -m pip install -r requirements-dev.txt && $(PY) -m pip install -e .

validate:  ## Run data validation checks
	$(PY) -m liquid_biopsy.validate

train:  ## Train, evaluate, quality gate, drift reference, MLflow tracking
	$(PY) -m liquid_biopsy.train

leakage:  ## Compare the original (leaky) evaluation with the corrected one
	$(PY) scripts/leakage_demo.py

mlflow-ui:  ## Browse experiment runs on http://localhost:5000
	mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5000

# ----------------------------------------------------------------------------- quality
test:  ## Run tests (SQLite only)
	$(PY) -m pytest

test-db: db-up  ## Run tests on SQLite AND a local Postgres (Docker)
	TEST_DATABASE_URL=postgresql://lb:lbpass@localhost:5432/lbtest $(PY) -m pytest

lint:  ## Lint and check formatting
	ruff check . && ruff format --check .

format:  ## Auto-format the code
	ruff check . --fix && ruff format .

# ----------------------------------------------------------------------------- run locally
db-up:  ## Start a local Postgres in Docker
	@docker start lb-postgres-local >/dev/null 2>&1 || docker run -d --name lb-postgres-local -p 5432:5432 \
	  -e POSTGRES_USER=lb -e POSTGRES_PASSWORD=lbpass -e POSTGRES_DB=liquidbiopsy postgres:16-alpine
	@until docker exec lb-postgres-local pg_isready -U lb >/dev/null 2>&1; do sleep 1; done
	@docker exec lb-postgres-local psql -U lb -d liquidbiopsy -tc "SELECT 1 FROM pg_database WHERE datname='lbtest'" | grep -q 1 \
	  || docker exec lb-postgres-local createdb -U lb lbtest

db-down:  ## Remove the local Postgres
	docker rm -f lb-postgres-local

serve:  ## Run the API without a database on http://localhost:8000/docs
	$(PY) -m uvicorn app.main:app --reload --port 8000

serve-db: db-up  ## Run the API with the local Postgres (predictions are stored)
	DATABASE_URL=$(LOCAL_DB_URL) $(PY) -m uvicorn app.main:app --reload --port 8000

drift:  ## Run the drift check against the local Postgres
	DATABASE_URL=$(LOCAL_DB_URL) DRIFT_MIN_PREDICTIONS=1 $(PY) -m app.drift

# ----------------------------------------------------------------------------- containers
docker-build:  ## Build the serving image (needs a trained model in models/)
	docker build -t liquid-biopsy-api:dev .

docker-test: docker-build  ## Test the image with Postgres and the drift job, like CI does
	bash scripts/container_test.sh liquid-biopsy-api:dev

kind-up:  ## Create a local Kubernetes cluster (kind)
	kind get clusters | grep -qx $(KIND_CLUSTER) || kind create cluster --name $(KIND_CLUSTER)

kind-deploy: kind-up docker-build  ## Deploy API + Postgres + drift CronJob to the kind cluster
	kind load docker-image liquid-biopsy-api:dev --name $(KIND_CLUSTER)
	kubectl apply -k k8s/overlays/kind
	kubectl -n liquid-biopsy rollout restart deployment/liquid-biopsy-api
	kubectl -n liquid-biopsy rollout status deployment/liquid-biopsy-api --timeout=300s
	@echo "Now: kubectl -n liquid-biopsy port-forward svc/liquid-biopsy-api 8080:80  ->  http://localhost:8080/docs"

kind-down:  ## Delete the kind cluster
	kind delete cluster --name $(KIND_CLUSTER)

# ----------------------------------------------------------------------------- infrastructure
k8s-render:  ## Print the rendered Kubernetes manifests for both overlays
	kubectl kustomize k8s/overlays/kind && echo "---" && kubectl kustomize k8s/overlays/aks

bicep-build:  ## Compile and lint the Bicep templates
	for f in infra/bicep/*.bicep; do az bicep build --file $$f --stdout >/dev/null && az bicep lint --file $$f; done

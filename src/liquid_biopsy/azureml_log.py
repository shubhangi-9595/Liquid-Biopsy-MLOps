"""Log the CI-tested model's metrics, parameters and artifacts to Azure Machine Learning.

Used by the CD pipeline after logging in to Azure. The model itself is registered
in the Azure ML model registry with `az ml model create` (see cd.yml), tagged with
the MLflow run ID printed here, so every registered model links back to its run.

Run:  MLFLOW_TRACKING_URI=$(az ml workspace show ... --query mlflow_tracking_uri -o tsv) \\
      python -m liquid_biopsy.azureml_log --model-dir models
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .tracking import log_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", default="models")
    parser.add_argument("--experiment", default="liquid-biopsy-cancer-classifier")
    args = parser.parse_args(argv)

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        print("MLFLOW_TRACKING_URI is not set")
        return 1

    out_dir = Path(args.model_dir)
    metadata = json.loads((out_dir / "metadata.json").read_text())
    tags = {
        "git_sha": os.environ.get("GITHUB_SHA", "local")[:7],
        "ci_run": os.environ.get("CI_RUN_URL", ""),
        "source": "github-actions-cd",
    }
    # Only metrics/params/artifacts go through MLflow; registration uses `az ml model create`
    run_id = log_run(metadata, out_dir, tracking_uri, args.experiment, model=None, tags=tags, log_model_flavor=False)
    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())

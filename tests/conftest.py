from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

# Tests are hermetic: a developer's own DATABASE_URL must never be touched.
# Postgres tests use TEST_DATABASE_URL instead (set in CI by a Postgres service container).
os.environ.pop("DATABASE_URL", None)
os.environ.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)

from liquid_biopsy.config import load_params, resolve  # noqa: E402
from liquid_biopsy.data import load_dataset  # noqa: E402


@pytest.fixture(scope="session")
def params():
    return load_params()


@pytest.fixture(scope="session")
def dataset(params):
    return load_dataset(
        resolve(params["data"]["path"]),
        params["data"]["healthy_prefixes"],
        params["data"].get("exclude_prefixes"),
    )


@pytest.fixture(scope="session")
def trained_model_dir(tmp_path_factory, params):
    """Run the real training entrypoint with a tiny grid into a temp folder."""
    from liquid_biopsy.train import main

    out = tmp_path_factory.mktemp("models")
    test_params = {**params, "artifacts": {**params["artifacts"], "dir": str(out)}}
    test_params["data"] = {**params["data"], "path": str(resolve(params["data"]["path"]))}
    params_file = out / "params.yaml"
    params_file.write_text(yaml.safe_dump(test_params))

    assert main(["--params", str(params_file), "--fast", "--no-mlflow", "--no-gate"]) == 0
    return out


def _reset_postgres(url: str) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request, tmp_path):
    """An empty database, migrated to the latest schema. Postgres only when TEST_DATABASE_URL is set."""
    from app.db.migrate import run_migrations
    from app.db.session import get_engine, normalize_url

    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'test.db'}"
    else:
        raw = os.environ.get("TEST_DATABASE_URL")
        if not raw:
            pytest.skip("TEST_DATABASE_URL not set")
        url = normalize_url(raw)
        get_engine(url).dispose()
        _reset_postgres(url)
    run_migrations(url)
    yield url
    get_engine(url).dispose()

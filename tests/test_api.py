import pytest
from fastapi.testclient import TestClient

from app import main as api
from liquid_biopsy.data import split_features_target


@pytest.fixture(scope="module")
def client(trained_model_dir):
    with TestClient(api.app) as c:
        api.init_database(None)  # these tests run without a database
        api.load_artifacts(trained_model_dir)  # point the app at the test model
        yield c


@pytest.fixture(scope="module")
def samples(dataset):
    X, y = split_features_target(dataset)
    healthy = X[y == 0].iloc[0].to_dict()
    cancer = X[y == 1].iloc[0].to_dict()
    return healthy, cancer


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True


def test_model_info(client):
    body = client.get("/model-info").json()
    assert body["n_features"] == 90
    assert len(body["required_genes"]) == 90
    assert "roc_auc" in body["test_metrics"]


def test_predict_returns_one_prediction_per_sample(client, samples):
    r = client.post("/predict", json={"samples": list(samples)})
    assert r.status_code == 200
    preds = r.json()["predictions"]
    assert len(preds) == 2
    for p in preds:
        assert 0 <= p["cancer_probability"] <= 1
        assert p["label_name"] in {"healthy", "cancer"}


def test_predict_rejects_missing_genes(client, samples):
    sample = dict(samples[0])
    sample.pop(next(iter(sample)))
    r = client.post("/predict", json={"samples": [sample]})
    assert r.status_code == 422
    assert "missing" in r.json()["detail"]


def test_predict_rejects_negative_counts(client, samples):
    sample = dict(samples[0])
    sample[next(iter(sample))] = -1
    assert client.post("/predict", json={"samples": [sample]}).status_code == 422


def test_predict_rejects_empty_request(client):
    assert client.post("/predict", json={"samples": []}).status_code == 422


def test_predict_reports_ignored_genes(client, samples):
    sample = {**samples[1], "ENSG99999999999": 10}
    r = client.post("/predict", json={"samples": [sample]})
    assert r.status_code == 200
    assert r.json()["ignored_genes"] == ["ENSG99999999999"]


def test_health_reports_degraded_without_model(tmp_path, trained_model_dir):
    with TestClient(api.app) as c:
        api.load_artifacts(tmp_path)  # empty folder, no model
        assert c.get("/health").json()["status"] == "degraded"
        assert c.post("/predict", json={"samples": [{"a": 1}]}).status_code == 503
        api.load_artifacts(trained_model_dir)

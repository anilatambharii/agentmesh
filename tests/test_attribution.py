"""Tests for cost attribution and chargeback."""

import pytest
from agentmesh.attribution.chargebacks import CostAttributor, UsageRecord


@pytest.fixture
def attributor():
    a = CostAttributor()
    a.record(model="claude-haiku-4-5", input_tokens=10_000, output_tokens=1_000,
             cost_usd=0.009, team="data-science", project="fraud-detection")
    a.record(model="claude-sonnet-4-6", input_tokens=5_000, output_tokens=500,
             cost_usd=0.015, team="engineering", project="code-review")
    a.record(model="claude-haiku-4-5", input_tokens=8_000, output_tokens=800,
             cost_usd=0.007, team="data-science", project="churn-prediction")
    return a


def test_record_basic(attributor):
    assert attributor.total_calls == 3
    assert attributor.total_cost_usd == pytest.approx(0.031)


def test_summary_by_team(attributor):
    col = attributor.summary(group_by="team")
    assert len(col.summaries) == 2

    teams = {s.group_key: s for s in col.summaries}
    assert "data-science" in teams
    assert "engineering" in teams

    ds = teams["data-science"]
    assert ds.call_count == 2
    assert ds.total_cost_usd == pytest.approx(0.016)
    assert ds.total_tokens == 19_800


def test_summary_sorted_by_cost(attributor):
    col = attributor.summary(group_by="team")
    # data-science should be first (higher spend)
    assert col.summaries[0].group_key == "data-science"


def test_summary_by_project(attributor):
    col = attributor.summary(group_by="project")
    assert len(col.summaries) == 3


def test_top_spenders(attributor):
    top = attributor.top_spenders(n=1, group_by="team")
    assert len(top) == 1
    assert top[0].group_key == "data-science"


def test_budget_status_under_budget(attributor):
    status = attributor.budget_status({"data-science": 10.0})
    assert status["data-science"]["over_budget"] is False
    assert status["data-science"]["pct_used"] < 1


def test_budget_status_over_budget():
    a = CostAttributor()
    a.record(model="claude-opus-4-8", input_tokens=100_000, output_tokens=10_000,
             cost_usd=1.65, team="research")
    status = a.budget_status({"research": 1.00})  # budget < spend
    assert status["research"]["over_budget"] is True


def test_summary_to_csv(attributor):
    col = attributor.summary(group_by="team")
    csv = col.to_csv()
    assert "data-science" in csv
    assert "engineering" in csv
    assert "total_cost_usd" in csv


def test_summary_to_json(attributor):
    import json
    col = attributor.summary(group_by="team")
    data = json.loads(col.to_json())
    assert len(data) == 2
    assert data[0]["group_key"] in ["data-science", "engineering"]


def test_unique_models_tracked(attributor):
    col = attributor.summary(group_by="team")
    teams = {s.group_key: s for s in col.summaries}
    ds_models = teams["data-science"].unique_models
    assert "claude-haiku-4-5" in ds_models


def test_avg_cost_per_call(attributor):
    col = attributor.summary(group_by="team")
    teams = {s.group_key: s for s in col.summaries}
    assert teams["data-science"].avg_cost_per_call == pytest.approx(0.008, rel=0.1)


def test_total_cost_collection(attributor):
    col = attributor.summary(group_by="team")
    assert col.total_cost_usd() == pytest.approx(0.031)

"""The demo is only usable if the browser can drive it without typing commands."""

from __future__ import annotations

import pytest
from dispatch_web.api import DEMOS, POLICIES, create_app
from fastapi.testclient import TestClient
from mine_sim.scenario import SCENARIOS

HOURS = 1.0


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def shift(client):
    response = client.post(
        "/api/run", json={"scenario": "toy", "policy": "neediest", "hours": HOURS}
    )
    assert response.status_code == 200
    return response.json()


def test_every_demo_button_points_at_something_that_exists(client) -> None:
    """A dead demo card is the one bug a committee would certainly hit."""
    for demo in DEMOS:
        assert demo["scenario"] in SCENARIOS, demo["id"]
        assert demo["policy"] in POLICIES, demo["id"]
        assert demo["hours"] > 0


def test_the_catalogue_is_served(client) -> None:
    body = client.get("/api/demos").json()
    assert [d["id"] for d in body["demos"]] == [d["id"] for d in DEMOS]
    assert set(body["scenarios"]) == set(SCENARIOS)
    assert set(body["policies"]) == set(POLICIES)


def test_the_page_and_its_assets_are_served(client) -> None:
    assert client.get("/").status_code == 200
    for asset in ("app.js", "scene.js", "vendor/three.module.js"):
        assert client.get(f"/static/{asset}").status_code == 200, asset


def test_a_run_carries_everything_the_viewer_needs(shift) -> None:
    required = {
        "nodes",
        "edges",
        "zones",
        "shovels",
        "dumps",
        "trucks",
        "movements",
        "states",
        "shovel_states",
        "events",
        "kpis",
        "plan",
        "blend_targets",
        "values",
        "dump_values",
        "setup",
    }
    assert required <= set(shift)
    # The dashboard prices each load as it is tipped, so it needs a value for
    # every zone and every destination, not just the ones the plan happens to use.
    assert set(shift["values"]) == set(shift["zones"])
    assert set(shift["dump_values"]) == {d["id"] for d in shift["dumps"]}


def test_the_tonnage_adds_up(shift) -> None:
    """Same identity the CLI checks: per-destination must equal the total."""
    kpis = shift["kpis"]
    assert sum(kpis["by_dump"].values()) == pytest.approx(kpis["tonnes_tipped"])
    assert sum(kpis["by_route"].values()) == pytest.approx(kpis["tonnes_tipped"])


def test_the_dashboard_totals_match_the_engine(shift) -> None:
    """The viewer recomputes KPIs from the event stream while it plays; when it
    reaches the end it has to land on the number the engine reported."""
    tipped = sum(e["payload_t"] for e in shift["events"] if e["kind"] == "dump_end")
    assert tipped == pytest.approx(shift["kpis"]["tonnes_tipped"])

    cycles = sum(1 for e in shift["events"] if e["kind"] == "dump_end")
    assert cycles == shift["kpis"]["cycles"]


def test_the_plan_keeps_the_crusher_in_its_blend_window(shift) -> None:
    assert shift["blend_targets"], "the toy mine declares a blend window"
    assert all(blend["in_spec"] for blend in shift["kpis"]["blends"])


def test_the_myopic_baseline_moves_more_rock_for_less_money(client) -> None:
    """The headline of the demo, asserted rather than asserted-by-screenshot.

    Moving rock and making money are not the same thing: the myopic baseline
    keeps its trucks busier and delivers the wrong mix to the wrong places.
    """
    payloads = {
        policy: client.post(
            "/api/run", json={"scenario": "toy", "policy": policy, "hours": 2.0}
        ).json()["kpis"]
        for policy in ("neediest", "earliest")
    }
    assert payloads["earliest"]["tonnes_tipped"] >= payloads["neediest"]["tonnes_tipped"]
    assert payloads["earliest"]["plan_value"] < payloads["neediest"]["plan_value"]


def test_unknown_scenarios_and_policies_are_refused(client) -> None:
    assert client.post("/api/run", json={"scenario": "nope"}).status_code == 404
    assert client.post("/api/run", json={"policy": "nope"}).status_code == 404
    assert client.post("/api/run", json={"hours": 0}).status_code == 422

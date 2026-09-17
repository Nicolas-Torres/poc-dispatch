from __future__ import annotations

import csv
from pathlib import Path

import pytest
from mine_sim.scenario import (
    dump_scenario_spec,
    load_scenario_spec,
    toy_mine,
    toy_mine_with_stockpile,
)
from mine_sim.simulation import Simulation
from pydantic import ValidationError


@pytest.mark.parametrize("suffix", [".yaml", ".json"])
def test_a_scenario_survives_a_round_trip(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"mine{suffix}"
    spec = toy_mine_with_stockpile()

    dump_scenario_spec(spec, path)

    assert load_scenario_spec(path) == spec


def test_a_run_from_a_file_matches_the_built_in_scenario(tmp_path: Path) -> None:
    path = tmp_path / "mine.yaml"
    dump_scenario_spec(toy_mine(), path)

    from_file = Simulation(load_scenario_spec(path).build()).run(until_s=3600.0)
    built_in = Simulation(toy_mine().build()).run(until_s=3600.0)

    assert from_file.tonnes_by_route == built_in.tonnes_by_route
    assert from_file.cycles == built_in.cycles


def test_an_unknown_format_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "mine.toml"
    path.write_text("name = 'toy'", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported scenario format"):
        load_scenario_spec(path)


def test_a_hand_written_mine_goes_through_the_same_validation(tmp_path: Path) -> None:
    # The load zone sits on a node no road reaches.
    path = tmp_path / "mine.yaml"
    path.write_text(
        "name: broken\n"
        "edges: [{source: pit, target: plant, length_m: 1000}]\n"
        "load_zones: [{id: zone, node: nowhere, material: {name: ore}}]\n"
        "shovels: [{id: SH, zone: zone, load_rate_tph: 2000}]\n"
        "dump_zones: [{id: plant, node: plant, accepts_ore: true}]\n"
        "trucks: [{id: T01, payload_t: 200, start_node: plant}]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="unknown node"):
        load_scenario_spec(path)


def test_the_event_log_is_written_with_one_row_per_event(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    simulation = Simulation(toy_mine().build())
    simulation.run(until_s=3600.0)

    simulation.log.to_csv(path)

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == len(simulation.log.events)
    assert rows[0]["kind"] == "assigned"
    # The reason the engine gave travels with the event, so a run can be audited.
    assert rows[0]["detail"]

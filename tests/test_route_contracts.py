import json
from collections import Counter
from pathlib import Path
import pytest
from tests.support.source_contracts import project_root, route_contracts, build_only_aliases

BASE = project_root() / "tests" / "baselines"

@pytest.mark.static
def test_route_contracts_match_step2_baseline():
    expected = json.loads((BASE / "route_contracts.json").read_text(encoding="utf-8"))
    assert route_contracts() == expected

@pytest.mark.static
def test_build_only_aliases_match_baseline():
    expected = json.loads((BASE / "build_only_aliases.json").read_text(encoding="utf-8"))
    assert build_only_aliases() == expected

@pytest.mark.static
def test_no_duplicate_live_path_method_pairs():
    rows = route_contracts()
    counts = Counter((row["path"], row["method"]) for row in rows)
    dupes = {key: count for key, count in counts.items() if count > 1}
    assert dupes == {}, f"Duplicate live route/path-method registrations: {dupes}"

@pytest.mark.static
def test_health_endpoints_are_frozen_in_contract():
    rows = {(r["path"], r["method"], r["endpoint"]) for r in route_contracts()}
    assert ("/health/live", "GET", "health_live") in rows
    assert ("/health/ready", "GET", "health_ready") in rows

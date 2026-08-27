import json
import pytest
from tests.support.source_contracts import project_root, form_contracts

BASE = project_root() / "tests" / "baselines" / "form_contracts.json"

@pytest.mark.static
def test_form_contracts_match_step2_baseline():
    expected = json.loads(BASE.read_text(encoding="utf-8"))
    actual = form_contracts()
    assert actual == expected

@pytest.mark.static
def test_no_form_loses_its_method_or_field_contract():
    forms = form_contracts()
    assert forms, "No forms were discovered."
    assert all(row["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE"} for row in forms)
    assert all(isinstance(row["field_names"], list) for row in forms)

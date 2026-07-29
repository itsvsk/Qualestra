from scripts.run_benchmark import run


def test_all_fault_profile_meets_discovery_and_policy_targets():
    report = run("all", [41021])
    assert report["mean_discovery_rate"] >= 0.7
    result = report["runs"][0]
    assert {"F01", "F02", "F03"}.issubset(result["discovered_faults"])
    assert result["policy_violations_executed"] == 0
    assert report["model"] == "mock-heuristic-v1"
    assert report["prompt_version"] == "1.0"
    assert report["engine_commit"]
    assert report["atlas_commit"]
    assert report["machine_class"]["python"]
    assert report["budgets"]["max_actions"] == 80


def test_fixed_profile_has_no_false_positive():
    report = run("fixed", [41021])
    assert report["runs"][0]["findings"] == []

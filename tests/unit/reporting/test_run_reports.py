from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from sentinelqa.domain.enums import GateOutcome, RunStatus, Severity
from sentinelqa.domain.models import (
    Defect,
    GateDecision,
    GateRuleResult,
    Run,
    RunRequest,
)
from sentinelqa.reporting import (
    compare_run_reports,
    generate_run_report,
    render_comparison_html,
    render_comparison_json,
    render_comparison_markdown,
    render_report_html,
    render_report_json,
    render_report_markdown,
    report_source_fingerprint,
)
from sentinelqa.reporting.defects import developer_handoff
from sentinelqa.reporting.evidence import write_release_bundle

RUN_ID = UUID("00000000-0000-4000-8000-000000000001")
BASELINE_RUN_ID = UUID("00000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 7, 27, 10, 30, tzinfo=UTC)


def make_run(run_id: UUID = RUN_ID, *, status: RunStatus = RunStatus.BLOCKED) -> Run:
    return Run(
        id=run_id,
        request=RunRequest(
            target_id="example-app",
            trigger="pull_request",
            commit_sha="abc123",
        ),
        status=status,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=90.125),
    )


def make_defect(
    defect_id: str,
    *,
    run_id: UUID = RUN_ID,
    severity: Severity = Severity.HIGH,
    signature: str = "ui|/settings|submit|save_failed|settings",
    secret: bool = False,
) -> Defect:
    actual = (
        "Save failed with password=very-private and Bearer abc.def"
        if secret
        else "Save returned an error."
    )
    step = (
        {
            "type": "fill",
            "client_secret": "very-private",
            "nested": {"access_token": "nested-private"},
        }
        if secret
        else {"type": "click", "control_id": "save"}
    )
    return Defect(
        defect_id=UUID(defect_id),
        run_id=run_id,
        title="Settings save fails",
        severity=severity,
        confidence=0.95,
        affected_area="/settings",
        signature=signature,
        persona="owner",
        preconditions=("Authenticated owner session",),
        steps_to_reproduce=(step,),
        expected="Settings are saved.",
        actual=actual,
        verification={"attempts": 2, "matched": 2},
        evidence=(
            {
                "kind": "trace",
                "artifact_id": "sha256:evidence-1",
                "api_key": "very-private",
            },
        ),
        suspected_component="settings-api",
        suggested_investigation="Inspect the settings mutation.",
        created_at=NOW,
    )


def make_gate(
    run_id: UUID = RUN_ID,
    *,
    outcome: GateOutcome = GateOutcome.BLOCK,
    critical: int = 1,
) -> GateDecision:
    matched = critical > 0
    return GateDecision(
        run_id=run_id,
        outcome=outcome,
        rules=(
            GateRuleResult(
                metric="verified_critical_defects",
                operator=">",
                threshold=0,
                actual=critical,
                matched=matched,
                level="block",
            ),
            GateRuleResult(
                metric="coverage",
                operator="<",
                threshold=0.8,
                actual=0.91,
                matched=False,
                level="warn",
            ),
        ),
        metrics={
            "verified_critical_defects": critical,
            "coverage": 0.91,
            "environment_stable": True,
        },
        blockers=("verified_critical_defects",) if matched else (),
        release_confidence=0.87,
        generated_at=NOW + timedelta(seconds=91),
    )


def test_report_is_stable_ordered_and_uses_committed_time() -> None:
    high = make_defect(
        "00000000-0000-4000-8000-000000000011",
        severity=Severity.HIGH,
        signature="high-signature",
    )
    critical = make_defect(
        "00000000-0000-4000-8000-000000000012",
        severity=Severity.CRITICAL,
        signature="critical-signature",
    )

    first = generate_run_report(make_run(), [high, critical], make_gate())
    second = generate_run_report(make_run(), [critical, high], make_gate())

    assert first == second
    assert render_report_json(first) == render_report_json(second)
    assert render_report_markdown(first) == render_report_markdown(second)
    assert render_report_html(first) == render_report_html(second)
    assert first.generated_at == make_gate().generated_at
    assert first.technical_summary.duration_seconds == 90.125
    assert first.technical_summary.severity_counts.critical == 1
    assert first.technical_summary.severity_counts.high == 1
    assert [item.severity for item in first.defects] == ["critical", "high"]
    assert first.evidence_references[0].artifact_id == "sha256:evidence-1"
    assert first.report_id.startswith("sha256:")
    assert report_source_fingerprint(make_run(), [high, critical], make_gate()) == (
        report_source_fingerprint(make_run(), [critical, high], make_gate())
    )
    changed_seed = make_run().model_copy(
        update={"request": make_run().request.model_copy(update={"seed": 99})}
    )
    assert report_source_fingerprint(changed_seed, [high, critical], make_gate()) != (
        report_source_fingerprint(make_run(), [high, critical], make_gate())
    )


def test_report_redacts_nested_secrets_in_every_renderer() -> None:
    defect = make_defect(
        "00000000-0000-4000-8000-000000000013",
        secret=True,
    )
    report = generate_run_report(make_run(), [defect], make_gate())

    assert (
        report.defects[0].steps_to_reproduce[0]["client_secret"] == "[REDACTED]"  # noqa: S105
    )
    assert (
        report.defects[0].steps_to_reproduce[0]["nested"]["access_token"] == "[REDACTED]"  # noqa: S105
    )
    assert report.defects[0].evidence[0]["api_key"] == "[REDACTED]"
    assert report.defects[0].actual == "Save failed with [REDACTED] and [REDACTED]"
    rendered = "\n".join(
        (
            render_report_json(report),
            render_report_markdown(report),
            render_report_html(report),
        )
    )
    assert "very-private" not in rendered
    assert "abc.def" not in rendered


def test_legacy_defect_bundle_and_handoff_are_redacted(tmp_path: Path) -> None:
    defect = make_defect(
        "00000000-0000-4000-8000-000000000021",
        secret=True,
    )
    gate = make_gate().model_copy(
        update={"metrics": {**make_gate().metrics, "diagnostic": "token=gate-private"}}
    )

    written = write_release_bundle(tmp_path, [defect], gate)
    bundle_text = "\n".join(path.read_text(encoding="utf-8") for path in written)
    handoff = developer_handoff([defect], commit_sha="abc123")

    assert "very-private" not in bundle_text
    assert "abc.def" not in bundle_text
    assert "gate-private" not in bundle_text
    assert "very-private" not in str(handoff)
    assert handoff["verified_defects"][0]["steps_to_reproduce"][0]["client_secret"] == (
        "[REDACTED]"  # noqa: S105
    )


def test_markdown_and_html_keep_evidence_and_escape_untrusted_text() -> None:
    defect = make_defect("00000000-0000-4000-8000-000000000014").model_copy(
        update={
            "title": "<script>alert(1)</script>",
            "expected": "<b>saved</b>",
        }
    )
    report = generate_run_report(make_run(), [defect], make_gate())

    markdown = render_report_markdown(report)
    html = render_report_html(report)
    assert "sha256:evidence-1" in markdown
    assert "sha256:evidence-1" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;saved&lt;/b&gt;" in html


def test_report_rejects_inputs_from_other_runs_and_duplicate_signatures() -> None:
    foreign = make_defect(
        "00000000-0000-4000-8000-000000000015",
        run_id=BASELINE_RUN_ID,
    )
    with pytest.raises(ValueError, match="defects do not belong"):
        generate_run_report(make_run(), [foreign], make_gate())
    with pytest.raises(ValueError, match="gate decision"):
        generate_run_report(make_run(), [], make_gate(BASELINE_RUN_ID))

    first = make_defect("00000000-0000-4000-8000-000000000016")
    duplicate = make_defect("00000000-0000-4000-8000-000000000017")
    with pytest.raises(ValueError, match="one verified defect per signature"):
        generate_run_report(make_run(), [first, duplicate], make_gate())

    inconsistent = make_gate().model_copy(update={"outcome": GateOutcome.PASS})
    with pytest.raises(ValueError, match="gate outcome"):
        generate_run_report(make_run(), [first], inconsistent)


def test_report_rejects_negative_run_duration() -> None:
    run = make_run()
    run.ended_at = NOW - timedelta(seconds=1)
    with pytest.raises(ValueError, match="ended_at"):
        generate_run_report(run, [], make_gate())


def test_comparison_is_deterministic_and_tracks_signatures_and_numeric_metrics() -> None:
    baseline_defect = make_defect(
        "00000000-0000-4000-8000-000000000018",
        run_id=BASELINE_RUN_ID,
        signature="resolved",
    )
    baseline = generate_run_report(
        make_run(BASELINE_RUN_ID),
        [baseline_defect],
        make_gate(BASELINE_RUN_ID),
    )
    persistent = make_defect(
        "00000000-0000-4000-8000-000000000019",
        signature="resolved",
    )
    new = make_defect(
        "00000000-0000-4000-8000-000000000020",
        severity=Severity.CRITICAL,
        signature="new",
    )
    current = generate_run_report(make_run(), [new, persistent], make_gate())

    comparison = compare_run_reports(baseline, current)

    assert comparison.verified_defect_delta == 1
    assert comparison.new_defect_signatures == ("new",)
    assert comparison.resolved_defect_signatures == ()
    assert comparison.persistent_defect_signatures == ("resolved",)
    assert "environment_stable" not in {item.metric for item in comparison.metric_deltas}
    assert render_comparison_json(comparison) == render_comparison_json(
        compare_run_reports(baseline, current)
    )
    assert render_comparison_markdown(comparison) == render_comparison_markdown(
        compare_run_reports(baseline, current)
    )
    assert render_comparison_html(comparison) == render_comparison_html(
        compare_run_reports(baseline, current)
    )
    assert "new" in render_comparison_markdown(comparison)

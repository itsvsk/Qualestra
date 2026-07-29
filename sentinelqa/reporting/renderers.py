from __future__ import annotations

import json
from html import escape

from .contracts import RunComparison, RunReportDocument


def render_report_json(report: RunReportDocument) -> str:
    """Render canonical, stable JSON suitable for persistence and hashing."""

    return (
        json.dumps(
            report.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )


def render_comparison_json(comparison: RunComparison) -> str:
    return (
        json.dumps(
            comparison.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )


def render_comparison_markdown(comparison: RunComparison) -> str:
    lines = [
        "# Qualestra run comparison",
        "",
        f"> {comparison.narrative}",
        "",
        f"- Baseline run: `{comparison.baseline_run_id}`",
        f"- Current run: `{comparison.current_run_id}`",
        f"- Baseline gate: **{comparison.baseline_outcome}**",
        f"- Current gate: **{comparison.current_outcome}**",
        f"- Verified-defect delta: **{comparison.verified_defect_delta:+d}**",
        "",
        "## Defect signatures",
        "",
        f"- New: {_signature_list(comparison.new_defect_signatures)}",
        f"- Resolved: {_signature_list(comparison.resolved_defect_signatures)}",
        f"- Persistent: {_signature_list(comparison.persistent_defect_signatures)}",
        "",
        "## Metric changes",
        "",
        "| Metric | Baseline | Current | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {item.metric} | {item.baseline} | {item.current} | {item.delta:+} |"
        for item in comparison.metric_deltas
    )
    if not comparison.metric_deltas:
        lines.append("| No comparable numeric metrics | — | — | — |")
    lines.extend(
        [
            "",
            f"_Comparison schema {comparison.schema_version}; ID `{comparison.comparison_id}`._",
            "",
        ]
    )
    return "\n".join(lines)


def render_comparison_html(comparison: RunComparison) -> str:
    metric_rows = "".join(
        "<tr>"
        f"<td>{escape(item.metric)}</td><td>{item.baseline}</td>"
        f"<td>{item.current}</td><td>{item.delta:+}</td>"
        "</tr>"
        for item in comparison.metric_deltas
    )
    if not metric_rows:
        metric_rows = (
            "<tr><td>No comparable numeric metrics</td><td>—</td><td>—</td><td>—</td></tr>"
        )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        "<title>Qualestra run comparison</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;"
        "padding:0 1rem;line-height:1.5}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #bbb;padding:.45rem;text-align:left}"
        "code{overflow-wrap:anywhere}</style></head><body>"
        "<h1>Qualestra run comparison</h1>"
        f"<p>{escape(comparison.narrative)}</p>"
        f"<p>Baseline: <code>{comparison.baseline_run_id}</code> "
        f"({comparison.baseline_outcome})<br>"
        f"Current: <code>{comparison.current_run_id}</code> "
        f"({comparison.current_outcome})</p>"
        "<h2>Defect signatures</h2><ul>"
        f"<li>New: {escape(_signature_text(comparison.new_defect_signatures))}</li>"
        f"<li>Resolved: {escape(_signature_text(comparison.resolved_defect_signatures))}</li>"
        f"<li>Persistent: {escape(_signature_text(comparison.persistent_defect_signatures))}</li>"
        "</ul><h2>Metric changes</h2>"
        "<table><thead><tr><th>Metric</th><th>Baseline</th><th>Current</th>"
        f"<th>Delta</th></tr></thead><tbody>{metric_rows}</tbody></table>"
        f"<p><small>Comparison schema {comparison.schema_version}; "
        f"ID <code>{escape(comparison.comparison_id)}</code>.</small></p>"
        "</body></html>\n"
    )


def render_report_markdown(report: RunReportDocument) -> str:
    technical = report.technical_summary
    release = report.release_summary
    counts = technical.severity_counts
    lines = [
        f"# Qualestra run report: {technical.target_id}",
        "",
        f"> {report.executive_summary.narrative}",
        "",
        "## Release decision",
        "",
        f"- Outcome: **{release.outcome}**",
        f"- Release confidence: **{release.release_confidence:.2f}**",
        f"- Run status: **{technical.run_status}**",
        f"- Run ID: `{technical.run_id}`",
        f"- Commit: `{technical.commit_sha or 'not provided'}`",
        "",
        release.narrative,
        "",
        "## Verified findings",
        "",
        f"- Total: **{technical.verified_defect_count}**",
        f"- Critical: **{counts.critical}**",
        f"- High: **{counts.high}**",
        f"- Medium: **{counts.medium}**",
        f"- Low: **{counts.low}**",
        f"- Evidence references: **{technical.evidence_reference_count}**",
        "",
    ]
    if report.defects:
        for defect in report.defects:
            lines.extend(
                [
                    f"### {defect.title}",
                    "",
                    f"- Severity: **{defect.severity.upper()}**",
                    f"- Confidence: **{defect.confidence:.2f}**",
                    f"- Signature: `{defect.signature}`",
                    f"- Affected area: `{defect.affected_area}`",
                    f"- Suspected component: `{defect.suspected_component}`",
                    "",
                    "**Expected:**",
                    "",
                    defect.expected,
                    "",
                    "**Actual:**",
                    "",
                    defect.actual,
                    "",
                    "**Evidence:**",
                    "",
                ]
            )
            if defect.evidence:
                lines.extend(
                    f"- {item.get('kind', 'artifact')}: `{item.get('artifact_id', 'unavailable')}`"
                    for item in defect.evidence
                )
            else:
                lines.append("- No evidence references committed.")
            lines.append("")
    else:
        lines.extend(["No independently verified defects were committed for this run.", ""])
    lines.extend(
        [
            "## Gate operands",
            "",
            "| Level | Metric | Operator | Threshold | Actual | Matched |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    lines.extend(
        "| {level} | {metric} | `{operator}` | `{threshold}` | `{actual}` | {matched} |".format(
            level=rule.level,
            metric=rule.metric,
            operator=rule.operator,
            threshold=_display(rule.threshold),
            actual=_display(rule.actual),
            matched="yes" if rule.matched else "no",
        )
        for rule in release.rules
    )
    if not release.rules:
        lines.append("| — | No configured rules | — | — | — | — |")
    lines.extend(["", f"_Report schema {report.schema_version}; ID `{report.report_id}`._", ""])
    return "\n".join(lines)


def render_report_html(report: RunReportDocument) -> str:
    technical = report.technical_summary
    release = report.release_summary
    counts = technical.severity_counts
    defect_sections = "".join(
        (
            "<article>"
            f"<h3>{escape(defect.title)}</h3>"
            f"<p><strong>{escape(defect.severity.upper())}</strong> · "
            f"confidence {defect.confidence:.2f}</p>"
            f"<p><strong>Expected:</strong> {escape(defect.expected)}</p>"
            f"<p><strong>Actual:</strong> {escape(defect.actual)}</p>"
            "<ul>"
            + "".join(
                f"<li>{escape(str(item.get('kind', 'artifact')))}: "
                f"<code>{escape(str(item.get('artifact_id', 'unavailable')))}</code></li>"
                for item in defect.evidence
            )
            + "</ul></article>"
        )
        for defect in report.defects
    )
    if not defect_sections:
        defect_sections = "<p>No independently verified defects were committed for this run.</p>"
    rule_rows = "".join(
        "<tr>"
        f"<td>{escape(rule.level)}</td><td>{escape(rule.metric)}</td>"
        f"<td>{escape(rule.operator)}</td><td>{escape(_display(rule.threshold))}</td>"
        f"<td>{escape(_display(rule.actual))}</td>"
        f"<td>{'yes' if rule.matched else 'no'}</td>"
        "</tr>"
        for rule in release.rules
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        f"<title>Qualestra report — {escape(technical.target_id)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;"
        "padding:0 1rem;line-height:1.5}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #bbb;padding:.45rem;text-align:left}"
        "code{overflow-wrap:anywhere}</style></head><body>"
        f"<h1>Qualestra run report: {escape(technical.target_id)}</h1>"
        f"<p>{escape(report.executive_summary.narrative)}</p>"
        "<h2>Release decision</h2>"
        f"<p><strong>{release.outcome}</strong> · release confidence "
        f"{release.release_confidence:.2f}</p>"
        f"<p>{escape(release.narrative)}</p>"
        "<h2>Verified findings</h2>"
        f"<p>{technical.verified_defect_count} total · {counts.critical} critical · "
        f"{counts.high} high · {counts.medium} medium · {counts.low} low</p>"
        f"{defect_sections}"
        "<h2>Gate operands</h2>"
        "<table><thead><tr><th>Level</th><th>Metric</th><th>Operator</th>"
        "<th>Threshold</th><th>Actual</th><th>Matched</th></tr></thead>"
        f"<tbody>{rule_rows}</tbody></table>"
        f"<p><small>Report schema {report.schema_version}; "
        f"ID <code>{escape(report.report_id)}</code>.</small></p>"
        "</body></html>\n"
    )


def _display(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _signature_list(signatures: tuple[str, ...]) -> str:
    return ", ".join(f"`{item}`" for item in signatures) if signatures else "none"


def _signature_text(signatures: tuple[str, ...]) -> str:
    return ", ".join(signatures) if signatures else "none"

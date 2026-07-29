from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from sentinelqa.domain.enums import ReportFormat
from sentinelqa.domain.models import RunReport
from sentinelqa.reporting.contracts import RunReportDocument

_KEY_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,49}$")
_TITLE_LIMIT = 200
_BODY_LIMIT = 16_000
_FINDING_LIMIT = 8


@dataclass(frozen=True, slots=True)
class TicketPayload:
    title: str
    body: str
    labels: tuple[str, ...]
    idempotency_marker: str


def build_ticket_payload(
    report: RunReport,
    idempotency_key: str,
    *,
    base_labels: Iterable[str] = (),
) -> TicketPayload:
    """Create a bounded ticket without executing or embedding raw report content."""

    marker = idempotency_marker(idempotency_key)
    document = _structured_document(report)
    labels = list(base_labels)
    labels.append("sentinelqa")

    if document is None:
        title = f"[Qualestra] QA report for run {report.run_id}"
        details = [
            "Qualestra generated an immutable QA report.",
            "",
            f"Run ID: {report.run_id}",
            f"Report ID: {report.report_id}",
            f"Report digest: {report.digest}",
            f"Report format: {report.format}",
        ]
    else:
        technical = document.technical_summary
        release = document.release_summary
        outcome = str(release.outcome).upper()
        labels.append(f"gate-{outcome.lower()}")
        severity = _highest_severity(document)
        if severity is not None:
            labels.append(f"severity-{severity}")
        title = (
            f"[Qualestra] {technical.target_id}: {outcome} "
            f"with {technical.verified_defect_count} verified defects"
        )
        details = [
            f"Qualestra release decision: {outcome}",
            "",
            f"Target: {_safe_text(technical.target_id)}",
            f"Run ID: {technical.run_id}",
            f"Commit: {_safe_text(technical.commit_sha or 'not provided')}",
            f"Verified defects: {technical.verified_defect_count}",
            (
                "Severity: "
                f"{technical.severity_counts.critical} critical, "
                f"{technical.severity_counts.high} high, "
                f"{technical.severity_counts.medium} medium, "
                f"{technical.severity_counts.low} low"
            ),
            f"Release confidence: {release.release_confidence:.2f}",
            f"Report digest: {report.digest}",
        ]
        if release.blockers:
            details.extend(
                (
                    "",
                    "Blocking gate operands:",
                    *[f"- {_safe_text(item)}" for item in release.blockers[:_FINDING_LIMIT]],
                )
            )
        if document.defects:
            details.extend(("", "Verified findings:"))
            details.extend(
                f"- [{defect.severity.upper()}] {_safe_text(defect.title)} "
                f"({_safe_text(defect.affected_area)})"
                for defect in document.defects[:_FINDING_LIMIT]
            )
            remaining = len(document.defects) - _FINDING_LIMIT
            if remaining > 0:
                details.append(f"- … and {remaining} additional verified findings")

    safe_title = _clip(_safe_text(title), _TITLE_LIMIT)
    body_without_marker = _clip("\n".join(details).strip(), _BODY_LIMIT - len(marker) - 2)
    return TicketPayload(
        title=safe_title,
        body=f"{body_without_marker}\n\n{marker}",
        labels=_labels(labels),
        idempotency_marker=marker,
    )


def idempotency_marker(idempotency_key: str) -> str:
    if not _KEY_PATTERN.fullmatch(idempotency_key):
        raise ValueError("ticket idempotency keys must use sha256:<64 lowercase hexadecimal>")
    return f"<!-- sentinelqa-idempotency: {idempotency_key} -->"


def idempotency_label(idempotency_key: str) -> str:
    if not _KEY_PATTERN.fullmatch(idempotency_key):
        raise ValueError("ticket idempotency keys must use sha256:<64 lowercase hexadecimal>")
    return f"sentinelqa-idem-{idempotency_key.removeprefix('sha256:')[:32]}"


def _structured_document(report: RunReport) -> RunReportDocument | None:
    if report.format != ReportFormat.JSON or len(report.content) > 2_000_000:
        return None
    try:
        raw = json.loads(report.content)
        return RunReportDocument.model_validate(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _highest_severity(document: RunReportDocument) -> str | None:
    counts = document.technical_summary.severity_counts
    for severity in ("critical", "high", "medium", "low"):
        if getattr(counts, severity) > 0:
            return severity
    return None


def _safe_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    printable = "".join(
        (
            character
            if character in "\n\t" or not unicodedata.category(character).startswith("C")
            else " "
        )
        for character in normalized
    )
    collapsed = " ".join(printable.split())
    return (
        collapsed.replace("@", "@\u200b")
        .replace("://", ":\u200b//")
        .replace("<", "‹")
        .replace(">", "›")
        .replace("`", "'")
    )


def _labels(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if not _LABEL_PATTERN.fullmatch(value):
            raise ValueError("ticket labels must be static, bounded identifiers")
        if value not in result:
            result.append(value)
    return tuple(result[:10])


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"

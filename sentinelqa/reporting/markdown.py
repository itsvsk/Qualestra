from sentinelqa.domain.models import Defect

from .redaction import redact_report_text, redact_report_value


def defect_markdown(defect: Defect) -> str:
    title = redact_report_text(defect.title)
    redacted_steps = [redact_report_value(step) for step in defect.steps_to_reproduce]
    steps = "\n".join(
        f"{index}. `{step.get('type', 'action')}` — {step}"
        for index, step in enumerate(redacted_steps, start=1)
    )
    redacted_evidence = [redact_report_value(item) for item in defect.evidence]
    evidence = "\n".join(
        f"- {item.get('kind', 'artifact')}: `{item.get('artifact_id')}`"
        for item in redacted_evidence
    )
    return f"""# {title}

- Severity: **{str(defect.severity).upper()}**
- Confidence: **{defect.confidence:.2f}**
- Status: **verified**
- Signature: `{redact_report_text(defect.signature)}`
- Suspected component: `{redact_report_text(defect.suspected_component)}`

## Reproduction

{steps}

## Expected

{redact_report_text(defect.expected)}

## Actual

{redact_report_text(defect.actual)}

## Evidence

{evidence}

## Suggested investigation

{redact_report_text(defect.suggested_investigation)}
"""

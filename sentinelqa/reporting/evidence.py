from __future__ import annotations

import json
from pathlib import Path

from sentinelqa.domain.models import Defect, GateDecision

from .markdown import defect_markdown
from .redaction import redact_report_value


def write_release_bundle(root: str | Path, defects: list[Defect], gate: GateDecision) -> list[Path]:
    target = Path(root)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for defect in defects:
        json_path = target / f"{defect.defect_id}.json"
        md_path = target / f"{defect.defect_id}.md"
        json_path.write_text(
            json.dumps(
                redact_report_value(defect.model_dump(mode="json")),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        md_path.write_text(defect_markdown(defect), encoding="utf-8")
        written.extend((json_path, md_path))
    gate_path = target / "gate.json"
    gate_path.write_text(
        json.dumps(
            redact_report_value(gate.model_dump(mode="json")),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(gate_path)
    handoff = target / "verified-defects.json"
    handoff.write_text(
        json.dumps(
            redact_report_value([item.model_dump(mode="json") for item in defects]),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(handoff)
    return written

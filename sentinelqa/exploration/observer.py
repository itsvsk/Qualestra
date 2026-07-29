from __future__ import annotations

import re
from collections.abc import Iterable
from hashlib import sha256

from sentinelqa.domain.models import Control

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", re.IGNORECASE)
INTEGER_ID_RE = re.compile(r"(?<=/)\d+(?=/|$)")


def normalize_route(url_path: str) -> str:
    normalized = UUID_RE.sub(":id", url_path.split("?", 1)[0])
    return INTEGER_ID_RE.sub(":id", normalized)


def state_fingerprint(
    route: str,
    landmarks: Iterable[str],
    controls: Iterable[Control],
    role: str,
    entity_types: Iterable[str] = (),
    overlay_state: str = "",
) -> str:
    signatures = sorted(f"{control.type}:{control.name.strip().lower()}" for control in controls)
    fields = [
        normalize_route(route),
        ",".join(sorted(name.strip().lower() for name in landmarks)),
        ",".join(signatures),
        role,
        ",".join(sorted(entity_types)),
        overlay_state.strip().lower(),
    ]
    return f"sha256:{sha256('|'.join(fields).encode()).hexdigest()}"


def control_id(control_type: str, name: str, ordinal: int) -> str:
    digest = sha256(f"{control_type}|{name.strip().lower()}|{ordinal}".encode()).hexdigest()[:12]
    return f"control_{digest}"

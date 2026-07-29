from uuid import uuid4

from sentinelqa.domain.models import EvidenceRefs, Observation, PageInfo, PersonaRef, SignalSet
from sentinelqa.oracles.differential import differential_anomaly
from sentinelqa.oracles.invariant import evaluate_invariant
from sentinelqa.oracles.metamorphic import metamorphic_anomaly
from sentinelqa.oracles.network import network_anomalies
from sentinelqa.oracles.semantic import semantic_anomaly


def observation():
    return Observation(
        run_id=uuid4(),
        session_id=uuid4(),
        state_fingerprint="sha256:a",
        page=PageInfo(url="http://atlas-web:5173/items", route="/items", title="Items"),
        persona=PersonaRef(id="editor", role="editor"),
        signals=SignalSet(responses=({"status": 500, "method": "PATCH", "url": "/item"},)),
        evidence=EvidenceRefs(screenshot_artifact_id="artifact://screen"),
    )


def test_required_oracle_categories_emit_evidence_linked_anomalies():
    seen = observation()
    explicit = network_anomalies(seen)[0]
    invariant = evaluate_invariant(
        run_id=seen.run_id,
        invariant_id="viewer_no_mutation",
        expected=403,
        actual=200,
        route="/items/:id",
        action_type="api_request",
        evidence_refs=("artifact://api",),
        severity="critical",
    )["anomaly"]
    differential = differential_anomaly(
        run_id=seen.run_id,
        route="/items/:id",
        left_name="ui",
        left="old",
        right_name="api",
        right="new",
        evidence_refs=("artifact://diff",),
    )
    metamorphic = metamorphic_anomaly(
        run_id=seen.run_id,
        relation="refresh",
        route="/items/:id",
        before="new",
        after="old",
        evidence_refs=("artifact://refresh",),
    )
    semantic = semantic_anomaly(
        run_id=seen.run_id,
        route="/items/:id",
        signal="success_error_contradiction",
        expected="one consistent message",
        actual="success toast with failed request",
        evidence_refs=("artifact://semantic",),
    )
    assert {
        item.oracle_type.split(":")[0]
        for item in [explicit, invariant, differential, metamorphic, semantic]
    } == {
        "explicit_network",
        "invariant",
        "differential",
        "metamorphic",
        "semantic",
    }
    assert all(
        item.evidence_refs for item in [explicit, invariant, differential, metamorphic, semantic]
    )
    assert str(semantic.severity_hint) == "medium"

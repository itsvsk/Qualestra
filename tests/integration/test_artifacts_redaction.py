from sentinelqa.policy.redaction import redact_headers
from sentinelqa.storage.artifacts import LocalArtifactStore


def test_artifact_hash_and_secret_redaction(tmp_path):
    store = LocalArtifactStore(tmp_path)
    record = store.put(
        kind="report",
        content=b"Authorization: Bearer abcdefghijklmnop",
        mime_type="text/plain",
        redact=True,
    )
    loaded, content = store.get(record.id)
    assert loaded.sha256 == record.sha256
    assert b"abcdefghijklmnop" not in content
    assert b"[REDACTED]" in content

    reloaded, reloaded_content = LocalArtifactStore(tmp_path).get(record.id)
    assert reloaded == record
    assert reloaded_content == content


def test_structured_headers_are_redacted():
    assert redact_headers({"Authorization": "Bearer secret", "content-type": "text/plain"}) == {
        "Authorization": "[REDACTED]",
        "content-type": "text/plain",
    }

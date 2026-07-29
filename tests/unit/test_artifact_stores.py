from __future__ import annotations

import io
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from sentinelqa.storage.artifacts import (
    ArtifactIntegrityError,
    ArtifactStore,
    LocalArtifactStore,
    RetentionPolicy,
    S3ArtifactStore,
    build_artifact_store_from_env,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        body = kwargs["Body"]
        assert isinstance(body, bytes)
        self.put_calls.append(dict(kwargs))
        self.objects[str(kwargs["Key"])] = body
        return {}

    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise FileNotFoundError(key)
        return {"Body": io.BytesIO(self.objects[key])}

    def delete_object(self, **kwargs: object) -> Mapping[str, object]:
        self.objects.pop(str(kwargs["Key"]), None)
        return {}

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        prefix = str(kwargs.get("Prefix", ""))
        return {
            "Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)],
            "IsTruncated": False,
        }

    def generate_presigned_url(
        self, client_method: str, *, Params: Mapping[str, object], ExpiresIn: int
    ) -> str:
        return f"https://evidence.invalid/{Params['Key']}?expires={ExpiresIn}"


def test_artifact_store_protocol_integrity_and_retention(tmp_path) -> None:
    store: ArtifactStore = LocalArtifactStore(tmp_path)
    first = store.put(kind="dom", content=b"old", mime_type="text/plain")
    second = store.put(kind="dom", content=b"newer", mime_type="text/plain")

    assert store.get(first.id)[1] == b"old"
    assert store.prune(RetentionPolicy(max_total_bytes=5)) == (first.id,)
    assert store.get(second.id)[1] == b"newer"

    content_path = tmp_path / f"{second.id}.bin"
    content_path.write_bytes(b"wrong")
    with pytest.raises(ArtifactIntegrityError):
        store.get(second.id)


def test_local_retention_by_age_uses_persistent_metadata(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    record = store.put(kind="report", content=b"report", mime_type="text/plain")
    metadata_path = tmp_path / f"{record.id}.meta.json"
    payload: dict[str, Any] = __import__("json").loads(metadata_path.read_text())
    payload["created_at"] = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    metadata_path.write_text(__import__("json").dumps(payload))

    reloaded = LocalArtifactStore(tmp_path)
    deleted = reloaded.prune(RetentionPolicy(max_age=timedelta(days=14)), now=datetime.now(UTC))

    assert deleted == (record.id,)
    with pytest.raises(FileNotFoundError):
        reloaded.get(record.id)


def test_s3_store_uses_sidecar_metadata_redaction_and_signed_urls() -> None:
    client = FakeS3Client()
    store = S3ArtifactStore("evidence", client=client, prefix="runs")

    record = store.put(
        kind="request_response",
        content=b"Authorization: Bearer abcdefghijklmnop",
        mime_type="text/plain",
        redact=True,
    )
    loaded, content = store.get(record.id)

    assert loaded == record
    assert b"abcdefghijklmnop" not in content
    assert all(call["ServerSideEncryption"] == "AES256" for call in client.put_calls)
    assert store.signed_get_url(record.id, 60).endswith(f"{record.id}.bin?expires=60")
    client.objects[f"runs/{record.id}.bin"] = b"tampered"
    with pytest.raises(ArtifactIntegrityError):
        store.get(record.id)
    assert store.delete(record.id)
    assert not store.delete(record.id)


def test_artifact_factory_stays_local_without_cloud_dependency(tmp_path) -> None:
    store = build_artifact_store_from_env(
        {"SENTINEL_ARTIFACT_BACKEND": "local", "ARTIFACT_ROOT": str(tmp_path)}
    )
    assert isinstance(store, LocalArtifactStore)
    with pytest.raises(ValueError, match="SENTINEL_ARTIFACT_S3_BUCKET"):
        build_artifact_store_from_env({"SENTINEL_ARTIFACT_BACKEND": "s3"})


def test_s3_signed_url_has_bounded_lifetime() -> None:
    store = S3ArtifactStore("evidence", client=FakeS3Client())
    with pytest.raises(ValueError, match="expire"):
        store.signed_get_url(UUID(int=1), 86_400)

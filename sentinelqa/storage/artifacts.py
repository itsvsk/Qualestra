from __future__ import annotations

import importlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from sentinelqa.policy.redaction import redact_text


class ArtifactIntegrityError(ValueError):
    """Raised when stored content no longer matches its immutable metadata."""


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: UUID
    kind: str
    uri: str
    sha256: str
    size: int
    mime_type: str
    redaction_status: str
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Retention bounds applied by an explicit maintenance job, never during reads."""

    max_age: timedelta | None = None
    max_total_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.max_age is not None and self.max_age < timedelta(0):
            raise ValueError("max_age must be non-negative")
        if self.max_total_bytes is not None and self.max_total_bytes < 0:
            raise ValueError("max_total_bytes must be non-negative")


@runtime_checkable
class ArtifactStore(Protocol):
    """Evidence store boundary shared by local and hosted deployments."""

    def put(
        self,
        *,
        kind: str,
        content: bytes,
        mime_type: str,
        redact: bool = False,
    ) -> ArtifactRecord: ...

    def get(self, artifact_id: UUID) -> tuple[ArtifactRecord, bytes]: ...

    def delete(self, artifact_id: UUID) -> bool: ...

    def prune(
        self, policy: RetentionPolicy, *, now: datetime | None = None
    ) -> tuple[UUID, ...]: ...


def _prepare_content(
    content: bytes, mime_type: str, redact: bool, max_bytes: int
) -> tuple[bytes, str]:
    if len(content) > max_bytes:
        raise ValueError("artifact exceeds size limit")
    status = "not_required"
    if redact:
        if not mime_type.startswith(("text/", "application/json")):
            raise ValueError("automatic redaction supports text artifacts only")
        content = redact_text(content.decode("utf-8")).encode()
        status = "redacted"
    return content, status


def _record_payload(record: ArtifactRecord) -> dict[str, object]:
    payload = asdict(record)
    payload["id"] = str(record.id)
    payload["created_at"] = record.created_at.isoformat() if record.created_at else None
    return payload


def _record_from_payload(payload: Mapping[str, object]) -> ArtifactRecord:
    raw_created_at = payload.get("created_at")
    created_at = datetime.fromisoformat(str(raw_created_at)) if raw_created_at else None
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return ArtifactRecord(
        id=UUID(str(payload["id"])),
        kind=str(payload["kind"]),
        uri=str(payload["uri"]),
        sha256=str(payload["sha256"]),
        size=int(str(payload["size"])),
        mime_type=str(payload["mime_type"]),
        redaction_status=str(payload["redaction_status"]),
        created_at=created_at,
    )


def _verify(record: ArtifactRecord, content: bytes) -> None:
    if len(content) != record.size or sha256(content).hexdigest() != record.sha256:
        raise ArtifactIntegrityError(f"artifact {record.id} failed integrity verification")


def _retention_candidates(
    records: list[ArtifactRecord], policy: RetentionPolicy, now: datetime
) -> set[UUID]:
    ordered = sorted(
        records,
        key=lambda item: (item.created_at or datetime.min.replace(tzinfo=UTC), str(item.id)),
    )
    deleted: set[UUID] = set()
    if policy.max_age is not None:
        cutoff = now - policy.max_age
        deleted.update(
            record.id
            for record in ordered
            if record.created_at is not None and record.created_at < cutoff
        )
    if policy.max_total_bytes is not None:
        retained_bytes = sum(record.size for record in ordered if record.id not in deleted)
        for record in ordered:
            if retained_bytes <= policy.max_total_bytes:
                break
            if record.id in deleted:
                continue
            deleted.add(record.id)
            retained_bytes -= record.size
    return deleted


class LocalArtifactStore:
    def __init__(self, root: str | Path, max_bytes: int = 104_857_600) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self._records: dict[UUID, ArtifactRecord] = {}

    def put(
        self,
        *,
        kind: str,
        content: bytes,
        mime_type: str,
        redact: bool = False,
    ) -> ArtifactRecord:
        content, status = _prepare_content(content, mime_type, redact, self.max_bytes)
        digest = sha256(content).hexdigest()
        artifact_id = uuid4()
        created_at = datetime.now(UTC)
        record = ArtifactRecord(
            id=artifact_id,
            kind=kind,
            uri=f"artifact://{artifact_id}",
            sha256=digest,
            size=len(content),
            mime_type=mime_type,
            redaction_status=status,
            created_at=created_at,
        )
        content_target = self._content_path(artifact_id, mime_type)
        metadata_target = self._metadata_path(artifact_id)
        content_temp = content_target.with_suffix(f"{content_target.suffix}.tmp")
        metadata_temp = metadata_target.with_suffix(".tmp")
        content_temp.write_bytes(content)
        metadata_temp.write_text(json.dumps(_record_payload(record), sort_keys=True))
        content_temp.replace(content_target)
        metadata_temp.replace(metadata_target)
        self._records[artifact_id] = record
        return record

    def get(self, artifact_id: UUID) -> tuple[ArtifactRecord, bytes]:
        record = self._load_record(artifact_id)
        content = self._content_path(artifact_id, record.mime_type).read_bytes()
        _verify(record, content)
        return record, content

    def delete(self, artifact_id: UUID) -> bool:
        try:
            record = self._load_record(artifact_id)
        except FileNotFoundError:
            return False
        self._content_path(artifact_id, record.mime_type).unlink(missing_ok=True)
        self._metadata_path(artifact_id).unlink(missing_ok=True)
        self._records.pop(artifact_id, None)
        return True

    def prune(self, policy: RetentionPolicy, *, now: datetime | None = None) -> tuple[UUID, ...]:
        records: list[ArtifactRecord] = []
        for path in sorted(self.root.glob("*.meta.json")):
            artifact_id = UUID(path.name.removesuffix(".meta.json"))
            records.append(self._load_record(artifact_id))
        deleted = _retention_candidates(records, policy, now or datetime.now(UTC))
        for artifact_id in deleted:
            self.delete(artifact_id)
        return tuple(sorted(deleted, key=str))

    def _metadata_path(self, artifact_id: UUID) -> Path:
        return self.root / f"{artifact_id}.meta.json"

    def _load_record(self, artifact_id: UUID) -> ArtifactRecord:
        record = self._records.get(artifact_id)
        if record is not None:
            return record
        metadata_path = self._metadata_path(artifact_id)
        payload: dict[str, object] = json.loads(metadata_path.read_text())
        if "created_at" not in payload:
            payload["created_at"] = datetime.fromtimestamp(
                metadata_path.stat().st_mtime, tz=UTC
            ).isoformat()
        record = _record_from_payload(payload)
        self._records[artifact_id] = record
        return record

    def _content_path(self, artifact_id: UUID, mime_type: str) -> Path:
        suffix = ".json" if mime_type == "application/json" else ".bin"
        return self.root / f"{artifact_id}{suffix}"


class S3Client(Protocol):
    """Minimal synchronous surface implemented by boto3 and lightweight test clients."""

    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...

    def generate_presigned_url(
        self, client_method: str, *, Params: Mapping[str, object], ExpiresIn: int
    ) -> str: ...


class S3ArtifactStore:
    """S3-compatible evidence store with sidecar metadata and verified reads.

    A client may be injected for any S3-compatible SDK. If omitted, boto3 is loaded lazily so
    local/offline installations do not require a cloud dependency.
    """

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "sentinelqa/artifacts",
        client: S3Client | None = None,
        endpoint_url: str | None = None,
        region: str | None = None,
        server_side_encryption: str | None = "AES256",
        max_bytes: int = 104_857_600,
    ) -> None:
        if not bucket or any(character.isspace() for character in bucket):
            raise ValueError("a valid S3 bucket name is required")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.max_bytes = max_bytes
        self.server_side_encryption = server_side_encryption
        self.client = client or self._boto3_client(endpoint_url=endpoint_url, region=region)

    @staticmethod
    def _boto3_client(*, endpoint_url: str | None, region: str | None) -> S3Client:
        try:
            boto3 = importlib.import_module("boto3")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "S3 artifact storage requires an injected S3 client or the 's3' extra"
            ) from exc
        return boto3.client("s3", endpoint_url=endpoint_url, region_name=region)  # type: ignore[no-any-return]

    def put(
        self,
        *,
        kind: str,
        content: bytes,
        mime_type: str,
        redact: bool = False,
    ) -> ArtifactRecord:
        content, status = _prepare_content(content, mime_type, redact, self.max_bytes)
        artifact_id = uuid4()
        key = self._content_key(artifact_id)
        record = ArtifactRecord(
            id=artifact_id,
            kind=kind,
            uri=f"s3://{self.bucket}/{key}",
            sha256=sha256(content).hexdigest(),
            size=len(content),
            mime_type=mime_type,
            redaction_status=status,
            created_at=datetime.now(UTC),
        )
        storage_options: dict[str, object] = {}
        if self.server_side_encryption:
            storage_options["ServerSideEncryption"] = self.server_side_encryption
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=mime_type,
            Metadata={"sha256": record.sha256},
            **storage_options,
        )
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._metadata_key(artifact_id),
                Body=json.dumps(_record_payload(record), sort_keys=True).encode(),
                ContentType="application/json",
                **storage_options,
            )
        except Exception:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            raise
        return record

    def get(self, artifact_id: UUID) -> tuple[ArtifactRecord, bytes]:
        record = self._load_record(artifact_id)
        response = self.client.get_object(Bucket=self.bucket, Key=self._content_key(artifact_id))
        content = self._body(response)
        _verify(record, content)
        return record, content

    def delete(self, artifact_id: UUID) -> bool:
        metadata_key = self._metadata_key(artifact_id)
        response = self.client.list_objects_v2(Bucket=self.bucket, Prefix=metadata_key, MaxKeys=1)
        contents = response.get("Contents", [])
        if not isinstance(contents, list):
            raise TypeError("S3 object listing did not return a list")
        if not any(
            isinstance(item, Mapping) and str(item.get("Key", "")) == metadata_key
            for item in contents
        ):
            return False
        self.client.delete_object(Bucket=self.bucket, Key=self._content_key(artifact_id))
        self.client.delete_object(Bucket=self.bucket, Key=self._metadata_key(artifact_id))
        return True

    def prune(self, policy: RetentionPolicy, *, now: datetime | None = None) -> tuple[UUID, ...]:
        records: list[ArtifactRecord] = []
        continuation: str | None = None
        while True:
            request: dict[str, object] = {
                "Bucket": self.bucket,
                "Prefix": f"{self.prefix}/" if self.prefix else "",
            }
            if continuation:
                request["ContinuationToken"] = continuation
            response = self.client.list_objects_v2(**request)
            contents = response.get("Contents", [])
            if not isinstance(contents, list):
                raise TypeError("S3 object listing did not return a list")
            for item in contents:
                if not isinstance(item, Mapping):
                    continue
                key = str(item.get("Key", ""))
                if not key.endswith(".meta.json"):
                    continue
                artifact_id = UUID(key.rsplit("/", 1)[-1].removesuffix(".meta.json"))
                records.append(self._load_record(artifact_id))
            if not response.get("IsTruncated"):
                break
            continuation = str(response["NextContinuationToken"])
        deleted = _retention_candidates(records, policy, now or datetime.now(UTC))
        for artifact_id in deleted:
            self.delete(artifact_id)
        return tuple(sorted(deleted, key=str))

    def signed_get_url(self, artifact_id: UUID, expires_seconds: int = 900) -> str:
        if not 1 <= expires_seconds <= 3600:
            raise ValueError("signed artifact URLs must expire between 1 and 3600 seconds")
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._content_key(artifact_id)},
            ExpiresIn=expires_seconds,
        )

    def _content_key(self, artifact_id: UUID) -> str:
        return f"{self.prefix}/{artifact_id}.bin" if self.prefix else f"{artifact_id}.bin"

    def _metadata_key(self, artifact_id: UUID) -> str:
        return (
            f"{self.prefix}/{artifact_id}.meta.json" if self.prefix else f"{artifact_id}.meta.json"
        )

    def _load_record(self, artifact_id: UUID) -> ArtifactRecord:
        metadata_response = self.client.get_object(
            Bucket=self.bucket, Key=self._metadata_key(artifact_id)
        )
        metadata: dict[str, object] = json.loads(self._body(metadata_response).decode())
        return _record_from_payload(metadata)

    @staticmethod
    def _body(response: Mapping[str, object]) -> bytes:
        body = response["Body"]
        if isinstance(body, bytes):
            return body
        reader = getattr(body, "read", None)
        if reader is None:
            raise TypeError("S3 response body is not readable")
        value = reader()
        if not isinstance(value, bytes):
            raise TypeError("S3 response body did not return bytes")
        return value


def build_artifact_store_from_env(
    environ: Mapping[str, str] | None = None,
) -> ArtifactStore:
    """Build the configured store without importing a cloud SDK for the local backend."""

    values: Mapping[str, str] = environ if environ is not None else os.environ
    backend = values.get("SENTINEL_ARTIFACT_BACKEND", "local").lower()
    max_bytes = int(values.get("SENTINEL_ARTIFACT_MAX_BYTES", "104857600"))
    if backend == "local":
        return LocalArtifactStore(values.get("ARTIFACT_ROOT", "artifacts"), max_bytes=max_bytes)
    if backend == "s3":
        bucket = values.get("SENTINEL_ARTIFACT_S3_BUCKET")
        if not bucket:
            raise ValueError("SENTINEL_ARTIFACT_S3_BUCKET is required for the S3 backend")
        return S3ArtifactStore(
            bucket,
            prefix=values.get("SENTINEL_ARTIFACT_S3_PREFIX", "sentinelqa/artifacts"),
            endpoint_url=values.get("SENTINEL_ARTIFACT_S3_ENDPOINT"),
            region=values.get("SENTINEL_ARTIFACT_S3_REGION"),
            server_side_encryption=(values.get("SENTINEL_ARTIFACT_S3_SSE", "AES256") or None),
            max_bytes=max_bytes,
        )
    raise ValueError(f"unsupported artifact backend: {backend}")

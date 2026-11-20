import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class StoredObject:
    uri: str
    backend: str
    bucket: Optional[str] = None
    key: Optional[str] = None


def safe_filename(filename: str) -> str:
    name = Path(filename or "recording").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "recording"


def save_upload(upload_id: str, filename: str, content: bytes) -> StoredObject:
    backend = os.getenv("FILE_STORAGE_BACKEND", "local").strip().lower()
    object_name = f"{upload_id}-{safe_filename(filename)}"

    if backend == "local":
        root = Path(os.getenv("UPLOAD_ROOT", "uploaded_recordings"))
        root.mkdir(parents=True, exist_ok=True)
        path = root / object_name
        path.write_bytes(content)
        return StoredObject(uri=str(path), backend="local", key=str(path))

    if backend in {"s3", "r2"}:
        bucket = os.getenv("S3_BUCKET") or os.getenv("R2_BUCKET")
        endpoint_url = os.getenv("S3_ENDPOINT_URL") or os.getenv("R2_ENDPOINT_URL")
        if not bucket:
            raise RuntimeError("S3_BUCKET or R2_BUCKET is required when FILE_STORAGE_BACKEND is s3 or r2")
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required when FILE_STORAGE_BACKEND is s3 or r2") from exc

        prefix = os.getenv("S3_PREFIX", "field-case-uploads").strip("/")
        key = f"{prefix}/{object_name}" if prefix else object_name
        client_kwargs = {"region_name": os.getenv("AWS_REGION", "auto")}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        client = boto3.client("s3", **client_kwargs)
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType="application/octet-stream",
        )
        scheme = "r2" if backend == "r2" else "s3"
        return StoredObject(uri=f"{scheme}://{bucket}/{key}", backend=backend, bucket=bucket, key=key)

    raise RuntimeError(f"Unsupported FILE_STORAGE_BACKEND: {backend}")

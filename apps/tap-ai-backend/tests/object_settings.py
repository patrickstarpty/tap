"""Loopback S3/MinIO settings every `TapperSettings` mapping must now carry."""

from __future__ import annotations

S3_SETTINGS = {
    "TAPPER_S3_ENDPOINT": "http://127.0.0.1:29000",
    "TAPPER_S3_BUCKET": "tapper-test-objects",
    "TAPPER_S3_REGION": "us-east-1",
    "TAPPER_S3_ACCESS_KEY": "owned-key",
    "TAPPER_S3_SECRET_KEY": "owned-secret",
    "TAPPER_S3_STORE_ID": "owned-store",
}

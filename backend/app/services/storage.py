import asyncio
from functools import lru_cache

from app.config import get_settings


class StorageError(Exception):
    pass


def s3_enabled() -> bool:
    s = get_settings()
    return bool(s.s3_endpoint and s.s3_access_key and s.s3_secret_key and s.s3_bucket)


@lru_cache
def _client():
    import boto3

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
    )


def _key(key: str) -> str:
    return f"{get_settings().s3_prefix.rstrip('/')}/{key.lstrip('/')}"


def upload_bytes_sync(key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
    full = _key(key)
    _client().put_object(Bucket=get_settings().s3_bucket, Key=full, Body=content, ContentType=content_type)
    return full


def download_bytes_sync(full_key: str) -> bytes:
    response = _client().get_object(Bucket=get_settings().s3_bucket, Key=full_key)
    return response["Body"].read()


def delete_object_sync(full_key: str) -> None:
    _client().delete_object(Bucket=get_settings().s3_bucket, Key=full_key)


# boto3 синхронный — в event loop ходим через thread pool.


async def upload_bytes(key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
    return await asyncio.to_thread(upload_bytes_sync, key, content, content_type)


async def download_bytes(full_key: str) -> bytes:
    return await asyncio.to_thread(download_bytes_sync, full_key)


async def delete_object(full_key: str) -> None:
    await asyncio.to_thread(delete_object_sync, full_key)

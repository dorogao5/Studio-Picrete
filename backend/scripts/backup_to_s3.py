"""Ночной бэкап Студии в S3. Запуск внутри контейнера: python /app/scripts/backup_to_s3.py

1. SQLite (если есть) — консистентная копия через backup API → gzip → S3.
2. Дампы из /app/data/backups (pg_dump кладёт хост-крон) → S3, локально удаляются.
3. В S3 остаются последние KEEP бэкапов.
"""

import gzip
import io
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

from app.services import storage  # noqa: E402

DATA = Path("/app/data")
BACKUPS = DATA / "backups"
PREFIX = "backups/"
KEEP = 30


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def backup_sqlite() -> None:
    db_path = DATA / "studio.db"
    if not db_path.exists():
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(":memory:")
    src.backup(dst)
    raw = b"".join(line.encode() + b"\n" for line in dst.iterdump())
    src.close()
    dst.close()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(raw)
    key = storage.upload_bytes_sync(f"{PREFIX}sqlite-{stamp}.sql.gz", buf.getvalue(), "application/gzip")
    log(f"sqlite → s3://{key} ({buf.tell()} bytes)")


def upload_dumps() -> None:
    if not BACKUPS.exists():
        return
    for path in sorted(BACKUPS.glob("*.gz")):
        key = storage.upload_bytes_sync(f"{PREFIX}{path.name}", path.read_bytes(), "application/gzip")
        log(f"{path.name} → s3://{key}")
        path.unlink()


def prune() -> None:
    settings_prefix = storage._key(PREFIX)
    client = storage._client()
    from app.config import get_settings

    bucket = get_settings().s3_bucket
    objects = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": settings_prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        objects.extend(page.get("Contents", []))
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    objects.sort(key=lambda o: o["LastModified"], reverse=True)
    for obj in objects[KEEP:]:
        client.delete_object(Bucket=bucket, Key=obj["Key"])
        log(f"pruned s3://{obj['Key']}")
    log(f"в S3 бэкапов: {min(len(objects), KEEP)}")


if __name__ == "__main__":
    if not storage.s3_enabled():
        log("S3 не настроен (STUDIO_S3_*) — выходим")
        sys.exit(1)
    backup_sqlite()
    upload_dumps()
    prune()
    log("done")

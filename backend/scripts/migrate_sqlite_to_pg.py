"""Перенос данных Студии SQLite → PostgreSQL.

Запуск в контейнере:
  STUDIO_PG_URL=postgresql+asyncpg://studio:...@127.0.0.1:5432/picrete_studio \
  python scripts/migrate_sqlite_to_pg.py

Идемпотентен на пустой целевой базе; при повторном запуске падает на дублях PK — это защита.
"""

import asyncio
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.models import Base  # noqa: E402

SQLITE_URL = os.environ.get("STUDIO_SQLITE_URL", "sqlite+aiosqlite:////app/data/studio.db")
PG_URL = os.environ["STUDIO_PG_URL"]


async def main() -> None:
    src = create_async_engine(SQLITE_URL)
    dst = create_async_engine(PG_URL)

    async with dst.begin() as conn:
        try:
            await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            print("pgvector: ok")
        except Exception as err:
            print(f"pgvector: пропущен ({str(err)[:80]})")
    async with dst.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    total = 0
    for table in Base.metadata.sorted_tables:
        async with src.connect() as sconn:
            rows = (await sconn.execute(select(table))).mappings().all()
        if not rows:
            print(f"{table.name}: пусто")
            continue
        async with dst.begin() as dconn:
            await dconn.execute(table.insert(), [dict(r) for r in rows])
        total += len(rows)
        print(f"{table.name}: {len(rows)}")

    # поисковый индекс
    async with dst.begin() as conn:
        await conn.exec_driver_sql("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS search_vector tsvector")
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_kb_search ON knowledge_chunks USING gin (search_vector)"
        )
        await conn.exec_driver_sql(
            "UPDATE knowledge_chunks SET search_vector = to_tsvector('russian', heading || ' ' || content)"
        )

    # верификация
    print("\n=== верификация ===")
    ok = True
    for table in Base.metadata.sorted_tables:
        async with src.connect() as sconn:
            n_src = (await sconn.execute(select(func.count()).select_from(table))).scalar()
        async with dst.connect() as dconn:
            n_dst = (await dconn.execute(select(func.count()).select_from(table))).scalar()
        mark = "✓" if n_src == n_dst else "✗ РАСХОЖДЕНИЕ"
        if n_src != n_dst:
            ok = False
        print(f"{mark} {table.name}: sqlite={n_src} pg={n_dst}")
    async with dst.connect() as dconn:
        n_vec = (
            await dconn.execute(text("SELECT count(*) FROM knowledge_chunks WHERE search_vector IS NOT NULL"))
        ).scalar()
        smoke = (
            await dconn.execute(
                text(
                    "SELECT count(*) FROM knowledge_chunks "
                    "WHERE search_vector @@ to_tsquery('russian', 'адсорбция | гиббс')"
                )
            )
        ).scalar()
    print(f"tsvector заполнен: {n_vec} | смоук-поиск 'адсорбция|гиббс': {smoke} хитов")
    print(f"\nперенесено строк: {total} | {'MIGRATION OK' if ok else 'MIGRATION FAILED'}")
    await src.dispose()
    await dst.dispose()
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

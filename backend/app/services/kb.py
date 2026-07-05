async def ensure_fts(conn) -> None:
    await conn.exec_driver_sql(
        "CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(chunk_id UNINDEXED, content_norm)"
    )

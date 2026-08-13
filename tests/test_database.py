import asyncio

from new_chat_learning.infrastructure.database import SQLiteStore


def test_database_initializes_schema_and_statistics(tmp_path):
    async def scenario():
        store = SQLiteStore(tmp_path / "runtime.sqlite3")
        await store.open()
        try:
            health = await store.health()
            statistics = await store.statistics()
        finally:
            await store.close()
        return health, statistics

    health, statistics = asyncio.run(scenario())

    assert health["connected"] is True
    assert health["schema_version"] == 1
    assert health["integrity"] == "ok"
    assert statistics["questions"] == 0
    assert statistics["answers"] == 0

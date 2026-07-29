from importlib import import_module
from io import StringIO

from alembic.migration import MigrationContext
from alembic.operations import Operations

from sentinelqa.storage.database import Base


def test_report_and_delivery_metadata_matches_migration_tables():
    expected = {
        "run_reports",
        "release_summaries",
        "deliveries",
        "delivery_attempts",
    }
    assert expected <= set(Base.metadata.tables)
    deliveries = Base.metadata.tables["deliveries"]
    assert deliveries.c.idempotency_key.unique
    assert {"status", "attempt_count", "last_error", "next_attempt_at"} <= set(deliveries.c.keys())
    attempts = Base.metadata.tables["delivery_attempts"]
    assert [column.name for column in attempts.primary_key.columns] == [
        "delivery_id",
        "attempt",
    ]


def test_report_and_delivery_migration_emits_postgresql_upgrade_sql():
    migration = import_module("atlas.migrations.versions.0005_reports_and_delivery_outbox")
    assert migration.revision == "0005"
    assert migration.down_revision == "0004"
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    for table in ("run_reports", "release_summaries", "deliveries", "delivery_attempts"):
        assert f"CREATE TABLE {table}" in sql

from narranexus.sdk import ColumnSpec, Contribution, IndexSpec, TableSpec

# Table names of a user plugin must start with __TABLE_PREFIX__ (the kernel enforces it).
# Contribution ids are global within a slot (two plugins named "items" cannot coexist): name yours after the plugin.
TABLES = (
    Contribution(
        "__PLUGIN_PKG___items",
        lambda: TableSpec(
            "__TABLE_PREFIX__items",
            (
                ColumnSpec("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
                ColumnSpec("key", "TEXT", "VARCHAR(128)", nullable=False),
                ColumnSpec("value_json", "TEXT", "MEDIUMTEXT"),
                ColumnSpec("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
            ),
            indexes=(IndexSpec("idx___PLUGIN_PKG___items_key", ("key",), unique=True),),
        ),
    ),
)


def activate(ctx):
    ctx.log.info("tables registered: __TABLE_PREFIX__items")

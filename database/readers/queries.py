"""Read-only SQL Server metadata queries."""

TABLES_SQL = """
SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_TYPE = 'BASE TABLE'
{filters}
ORDER BY TABLE_SCHEMA, TABLE_NAME
"""

COLUMNS_SQL = """
SELECT
    c.TABLE_CATALOG,
    c.TABLE_SCHEMA,
    c.TABLE_NAME,
    c.COLUMN_NAME,
    c.ORDINAL_POSITION,
    c.DATA_TYPE,
    c.IS_NULLABLE,
    c.CHARACTER_MAXIMUM_LENGTH,
    c.NUMERIC_PRECISION,
    c.NUMERIC_SCALE,
    c.COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS c
{filters}
ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
"""

PRIMARY_KEYS_SQL = """
SELECT
    tc.TABLE_SCHEMA,
    tc.TABLE_NAME,
    kcu.COLUMN_NAME,
    kcu.ORDINAL_POSITION
FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
    ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
    AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
{filters}
ORDER BY tc.TABLE_SCHEMA, tc.TABLE_NAME, kcu.ORDINAL_POSITION
"""

FOREIGN_KEYS_SQL = """
SELECT
    fk.name AS foreign_key_name,
    sch_fk.name AS foreign_table_schema,
    tab_fk.name AS foreign_table_name,
    col_fk.name AS foreign_column_name,
    sch_pk.name AS primary_table_schema,
    tab_pk.name AS primary_table_name,
    col_pk.name AS primary_column_name,
    fkc.constraint_column_id AS ordinal_position
FROM sys.foreign_keys fk
INNER JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
INNER JOIN sys.tables tab_fk ON fkc.parent_object_id = tab_fk.object_id
INNER JOIN sys.schemas sch_fk ON tab_fk.schema_id = sch_fk.schema_id
INNER JOIN sys.columns col_fk ON fkc.parent_object_id = col_fk.object_id AND fkc.parent_column_id = col_fk.column_id
INNER JOIN sys.tables tab_pk ON fkc.referenced_object_id = tab_pk.object_id
INNER JOIN sys.schemas sch_pk ON tab_pk.schema_id = sch_pk.schema_id
INNER JOIN sys.columns col_pk ON fkc.referenced_object_id = col_pk.object_id AND fkc.referenced_column_id = col_pk.column_id
{filters}
ORDER BY foreign_table_schema, foreign_table_name, ordinal_position
"""

-- Keep the SQL signature and function ACL while rebinding the C wrapper to
-- the upgraded shared library, which now validates required NULL arguments.
CREATE OR REPLACE FUNCTION lance_insert(
    uri text,
    id bigint,
    vector real[],
    label text
)
RETURNS void
LANGUAGE c
AS 'MODULE_PATHNAME', 'lance_insert_wrapper';

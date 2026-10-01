-- Run BEFORE ALTER EXTENSION UPDATE, outside the extension update script.
-- This preserves user grants as user grants in pg_dump bookkeeping.
    DO $pgwarc_lance_privileges$
    DECLARE
        extension_function regprocedure;
        function_count integer := 0;
    BEGIN
        FOR extension_function IN
            SELECT p.oid::regprocedure
            FROM pg_proc AS p
            JOIN pg_depend AS d
              ON d.classid = 'pg_proc'::regclass
             AND d.objid = p.oid
             AND d.refclassid = 'pg_extension'::regclass
             AND d.deptype = 'e'
            JOIN pg_extension AS e ON e.oid = d.refobjid
            WHERE e.extname = 'pgwarc_lance'
        LOOP
            EXECUTE format(
                'REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC',
                extension_function
            );
            function_count := function_count + 1;
        END LOOP;
        IF function_count = 0 THEN
            RAISE EXCEPTION 'pgwarc_lance function privilege lockdown found no functions';
        END IF;
    END
    $pgwarc_lance_privileges$;


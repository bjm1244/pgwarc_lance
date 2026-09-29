-- scalar function smoke tests for pgwarc_lance
SELECT hello_pgwarc_lance()                AS hello;
SELECT add_numbers(3, 4)              AS sum;
SELECT fibonacci(10)                  AS fib10;
SELECT fibonacci(0)                   AS fib0;
SELECT fibonacci(-1)                  AS fib_neg;
SELECT array_sum(ARRAY[1,2,3,4,5])    AS arr_sum;
SELECT shout('hello world')           AS shout;
SELECT greatest_of(3, 7, 2)           AS greatest;
SELECT is_even(8)                     AS even8;
SELECT is_even(7)                     AS even7;
SELECT word_count('hello world from pgwarc_lance') AS words;
SELECT double_or_none(21)             AS doubled;
SELECT double_or_none(NULL::int4)     AS doubled_null;
SELECT greeting('pgwarc_lance')            AS greeting_default;
SELECT greeting('pgwarc_lance', 42)        AS greeting_age;
-- end scalar function smoke tests

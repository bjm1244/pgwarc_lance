#!/usr/bin/env python3
"""Verify BM25 scoring, search limits, executor spill and cancellation.

Creates a new disposable database, then drops it. The large fixture has
100,000 documents and 800,000 common-term postings. Resource observations
describe this fixture, not a universal bound on PostgreSQL process RSS.
The default planner may stream ordered groups without temporary files. A
separate forced hash-join plan exercises executor spill and temporary quotas.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import re
import shlex
import subprocess
import time
import uuid
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exec-prefix', default='docker compose exec -T postgres')
    parser.add_argument('--pg-bin', default='/usr/lib/postgresql/16/bin')
    parser.add_argument('--host', default='/var/run/postgresql')
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--user', default='pgwarc_lance')
    parser.add_argument('--json-output', type=Path)
    args = parser.parse_args()
    prefix = shlex.split(args.exec_prefix)
    connection = ['-h', args.host, '-p', str(args.port), '-U', args.user]
    suffix = uuid.uuid4().hex[:16]
    db, uri = 'pgwarc_searchmem_' + suffix, '/tmp/pgwarc_searchmem_' + suffix + '.lance'
    command = prefix + [str(Path(args.pg_bin) / 'psql')] + connection + ['-d', db, '-X', '-q', '-At', '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose']
    report = {'status': 'running', 'checks': {}, 'documents': 100000, 'postings': 800000,
              'work_mem': '64kB', 'scope': 'single disposable backend and fixed common-term fixture; no operational SLA or universal RSS bound'}
    checks, created, actor = report['checks'], False, None

    def run(argv: list[str], text: str | None = None, timeout: float = 120) -> str:
        result = subprocess.run(argv, input=text, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise AssertionError(result.stderr[-4000:])
        return result.stdout.strip()

    def sql(text: str) -> str:
        return run(command, text)

    def expect(actual: str, expected: str, name: str) -> None:
        if actual != expected:
            raise AssertionError(f'{name}: expected {expected!r}, got {actual!r}')
        checks[name] = True

    def literal(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    def temp_bytes() -> int:
        return int(sql(f"SELECT temp_bytes FROM pg_stat_database WHERE datname='{db}';"))

    try:
        run(prefix + [str(Path(args.pg_bin) / 'createdb')] + connection + [db])
        created = True
        sql(f"CREATE EXTENSION pgwarc_lance; SELECT lance_create_table('{uri}',4,false); SELECT lance_insert('{uri}',1,ARRAY[0,0,0,0]::float4[],'one');")
        vector = 'ARRAY[0,0,0,0]::float4[]'
        for label, expression in [
            ('bm25_query_bytes', "bm25_search(repeat('x',65537),10)"),
            ('bm25_utf8_bytes', "bm25_search(repeat('한',21846),10)"),
            ('bm25_result_count', "bm25_search('alpha',10001)"),
            ('vector_result_count', f"lance_vector_search('{uri}',{vector},10001)"),
            ('hybrid_query_bytes', f"hybrid_search(repeat('x',65537),{vector},'{uri}',10)"),
            ('hybrid_result_count', f"hybrid_search('alpha',{vector},'{uri}',10001)"),
            ('warc_query_bytes', f"hybrid_warc_search(repeat('x',65537),{vector},'{uri}',10)"),
            ('warc_result_count', f"hybrid_warc_search('alpha',{vector},'{uri}',10001)"),
        ]:
            # A missing expected exception makes the DO block fail.
            sql(f"DO $$ BEGIN BEGIN PERFORM * FROM {expression}; RAISE EXCEPTION 'oversize input accepted'; EXCEPTION WHEN program_limit_exceeded THEN NULL; END; END $$;")
            checks[label + '_rejected_54000'] = True
        expect(sql("SELECT count(*) FROM bm25_search(repeat('x',65536),10000);"), '0', 'exact_query_and_result_boundaries_accepted')
        expect(sql(f"SELECT count(*) FROM lance_vector_search('{uri}',{vector},10000);"), '1', 'vector_result_boundary_accepted')
        for label, expression in [
            ('bm25', "bm25_search('alpha',0)"),
            ('vector', f"lance_vector_search('{uri}',{vector},0)"),
            ('hybrid', f"hybrid_search('alpha',{vector},'{uri}',0)"),
            ('warc', f"hybrid_warc_search('alpha',{vector},'{uri}',0)"),
        ]:
            expect(sql('SELECT count(*) FROM ' + expression + ';'), '0', label + '_zero_results')

        # Independent mathematical reference: fixed ASCII token counts, empty
        # document included in n/avgdl, repeated query terms counted once.
        texts = ['', 'alpha beta alpha', 'alpha gamma gamma gamma', 'beta beta', 'delta alpha beta', 'orphan omega']
        counts = {index: Counter(text.split()) for index, text in enumerate(texts, 1)}
        avgdl, n = sum(sum(tf.values()) for tf in counts.values()) / len(texts), len(texts)
        for doc, text in enumerate(texts, 1):
            sql(f'SELECT bm25_index_document({doc},{literal(text)});')
        score_checks = []
        for query in ['alpha', 'alpha beta', 'alpha alpha', 'gamma beta delta', 'alpha beta gamma delta', 'absent']:
            expected = []
            for doc, frequencies in counts.items():
                score, matched = 0., False
                for term in sorted(set(query.split())):
                    if term not in frequencies:
                        continue
                    matched = True
                    df = sum(term in tf for tf in counts.values())
                    tf, dl = frequencies[term], sum(frequencies.values())
                    score += math.log((n - df + .5) / (df + .5) + 1) * (tf * 2.2) / (tf + 1.2 * (.25 + .75 * dl / avgdl))
                if matched:
                    expected.append({'doc_id': doc, 'score': score})
            expected.sort(key=lambda row: (-row['score'], row['doc_id']))
            actual = json.loads(sql(f"SELECT coalesce(json_agg(t),'[]'::json) FROM bm25_search({literal(query)},10000) t;"))
            if [x['doc_id'] for x in actual] != [x['doc_id'] for x in expected] or any(abs(a['score'] - b['score']) > 1e-12 for a, b in zip(actual, expected)):
                raise AssertionError(f'independent BM25 reference differs for {query}: {actual} / {expected}')
            score_checks.append({'query': query, 'expected': expected, 'actual': actual})
        report['score_reference'] = score_checks
        checks['independent_bm25_score_and_rank_reference'] = True

        sql("TRUNCATE pgwarc_lance.bm25_doc CASCADE; INSERT INTO pgwarc_lance.bm25_doc(doc_id,content,doc_len) SELECT id,'alpha beta gamma delta epsilon zeta eta theta',8 FROM generate_series(1,100000) id; INSERT INTO pgwarc_lance.bm25_term(term,doc_id,tf) SELECT term,id,1 FROM unnest(ARRAY['alpha','beta','gamma','delta','epsilon','zeta','eta','theta']) term CROSS JOIN generate_series(1,100000) id; ANALYZE pgwarc_lance.bm25_doc; ANALYZE pgwarc_lance.bm25_term; SELECT pg_stat_force_next_flush();")
        expect(sql('SELECT count(*) FROM pgwarc_lance.bm25_term;'), '800000', 'large_common_term_fixture_seeded')
        query = 'alpha beta gamma delta epsilon zeta eta theta'
        default_ids = json.loads(sql(f"SET work_mem='64kB'; SELECT json_agg(doc_id) FROM bm25_search('{query}',10);"))
        if default_ids != list(range(1, 11)):
            raise AssertionError(f'default planner common-term tie order/count differs: {default_ids}')
        checks['large_default_plan_search_returns_exact_top_k_and_tie_order'] = True
        # Stress an alternative executor plan explicitly. Ordered index scans
        # and incremental sorting can legitimately avoid all temporary files,
        # so their absence in the default plan is not a memory-safety failure.
        spill_settings = "SET enable_nestloop=off; SET enable_mergejoin=off;"
        report['spill_planner_settings'] = {'enable_nestloop': False, 'enable_mergejoin': False}
        before = temp_bytes()
        actor = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        actor.stdin.write("SELECT hello_pgwarc_lance(); SELECT pg_backend_pid();\n")
        actor.stdin.flush()
        if actor.stdout.readline().strip() != 'Hello, pgwarc_lance!':
            raise AssertionError('resource actor did not initialize extension')
        pid = int(actor.stdout.readline().strip())
        actor.stdin.write(f"SET work_mem='64kB'; {spill_settings} SET statement_timeout='30s'; SELECT json_agg(doc_id) FROM bm25_search('{query}',10); SELECT pg_stat_force_next_flush(); SELECT pg_sleep(1);\n")
        actor.stdin.close()
        peak_kib, started = 0, time.monotonic()
        while actor.poll() is None:
            status = subprocess.run(prefix + ['cat', f'/proc/{pid}/status'], text=True, capture_output=True, timeout=10)
            match = re.search(r'^VmHWM:\s+(\d+)\s+kB', status.stdout, re.M)
            if match:
                peak_kib = max(peak_kib, int(match[1]))
            if time.monotonic() - started > 40:
                raise AssertionError('large native query exceeded its bounded test deadline')
            time.sleep(.05)
        stdout, stderr = actor.stdout.read(), actor.stderr.read()
        if actor.returncode or stderr:
            raise AssertionError(f'large native query failed: {stderr}')
        returned = json.loads(next(line for line in stdout.splitlines() if line.strip()))
        if returned != list(range(1,11)):
            raise AssertionError(f'common-term tie order/count differs: {returned}')
        checks['large_native_search_returns_exact_top_k_and_tie_order'] = True
        deadline = time.monotonic() + 5
        after = temp_bytes()
        while after <= before and time.monotonic() < deadline:
            time.sleep(.1)
            after = temp_bytes()
        if after <= before:
            raise AssertionError('native search did not demonstrate executor spill to temporary files')
        checks['native_executor_spill_observed'] = True
        if not peak_kib:
            raise AssertionError('backend high-water memory was not observed')
        report['resource_observation'] = {'backend_vm_hwm_kib': peak_kib, 'temp_bytes_delta': after - before,
                                          'client_elapsed_seconds_including_one_second_sleep': time.monotonic() - started}
        cancelled = subprocess.run(command + ['-v','ON_ERROR_STOP=0'], input=f"SET work_mem='64kB'; SET statement_timeout='10ms'; SELECT count(*) FROM bm25_search('{query}',10); RESET statement_timeout; SELECT 'backend-reusable-after-bm25-timeout';", text=True, capture_output=True, timeout=30)
        if cancelled.returncode or '57014' not in cancelled.stderr or 'backend-reusable-after-bm25-timeout' not in cancelled.stdout:
            raise AssertionError(f'BM25 timeout/reuse was not exercised: {cancelled.stdout} {cancelled.stderr}')
        report['timeout_stderr'] = cancelled.stderr[-4000:]
        checks['native_bm25_statement_timeout_and_backend_reuse'] = True
        quota = subprocess.run(command + ['-v','ON_ERROR_STOP=0'], input=f"SET work_mem='64kB'; {spill_settings} SET temp_file_limit='1MB'; SET statement_timeout='30s'; SELECT count(*) FROM bm25_search('{query}',10); RESET temp_file_limit; SELECT 'backend-reusable-after-temp-quota';", text=True, capture_output=True, timeout=40)
        if quota.returncode or '53400' not in quota.stderr or 'backend-reusable-after-temp-quota' not in quota.stdout:
            raise AssertionError(f'temporary file quota/reuse was not exercised: {quota.stdout} {quota.stderr}')
        report['temp_quota_stderr'] = quota.stderr[-4000:]
        checks['native_bm25_temp_file_quota_and_backend_reuse'] = True
        expect(sql("SELECT count(*) FROM bm25_search('alpha',10);"), '10', 'query_succeeds_after_timeout_in_new_session')
        report['status'] = 'passed'
        return 0
    except Exception as exc:
        report.update(status='failed',error=str(exc))
        raise
    finally:
        if created:
            subprocess.run(prefix + [str(Path(args.pg_bin)/'dropdb')] + connection + ['--force',db], capture_output=True, check=True, timeout=30)
        if actor and actor.poll() is None:
            actor.wait(timeout=15)
        text = json.dumps(report,indent=2) + '\n'
        if args.json_output:
            args.json_output.parent.mkdir(parents=True,exist_ok=True)
            args.json_output.write_text(text)
        print(text,end='')


if __name__ == '__main__':
    raise SystemExit(main())

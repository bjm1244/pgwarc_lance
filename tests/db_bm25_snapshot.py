#!/usr/bin/env python3
"""Check native BM25 scores while committed corpus sizes alternate.

Creates and removes its own database. Each reader transaction assigns an XID,
as happens after DML, so pgrx SPI uses writable snapshots. Each writer commit
atomically replaces the corpus; a search score must match one complete state.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import shlex
import subprocess
import threading
import time
import uuid


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
    db = 'pgwarc_snapshot_' + uuid.uuid4().hex[:16]
    command = prefix + [str(Path(args.pg_bin) / 'psql')] + connection + [
        '-d', db, '-X', '-q', '-At', '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose']
    sizes, writes, reads = [1000, 4000], 120, 400
    expected = [math.log(1 + .5 / (n + .5)) for n in sizes]
    report = {'status': 'running', 'checks': {}, 'corpus_sizes': sizes,
              'writer_transactions': writes, 'reader_transactions': reads,
              'expected_scores': expected,
              'scope': 'SQL BM25 statement consistency under atomic committed replacements; no SQL/Lance atomicity claim'}
    created = False

    def replace(n: int) -> str:
        return f"""DELETE FROM pgwarc_lance.bm25_term;
            DELETE FROM pgwarc_lance.bm25_doc;
            INSERT INTO pgwarc_lance.bm25_doc(doc_id,content,doc_len)
                SELECT i,'alpha',1 FROM generate_series(1,{n}) i;
            INSERT INTO pgwarc_lance.bm25_term(term,doc_id,tf)
                SELECT 'alpha',i,1 FROM generate_series(1,{n}) i;"""

    def sql(text: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, input=text, text=True, capture_output=True, timeout=120)

    try:
        subprocess.run(prefix + [str(Path(args.pg_bin) / 'createdb')] + connection + [db],
                       check=True, capture_output=True, text=True, timeout=30)
        created = True
        result = sql('CREATE EXTENSION pgwarc_lance; ' + replace(sizes[0]))
        if result.returncode:
            raise AssertionError(result.stderr)
        report['checks']['isolated_fixture_seeded'] = True
        # Also ensure ordinary prior writes in the same transaction remain
        # visible to native search after the snapshot change.
        result = sql("BEGIN; SELECT bm25_index_document(10001,'unique'); "
                     "SELECT doc_id FROM bm25_search('unique',1); ROLLBACK;")
        if result.returncode or result.stdout.strip() != '10001':
            raise AssertionError('search did not see own transaction write: ' + result.stderr)
        report['checks']['search_sees_own_transaction_writes'] = True
        writer = ''.join('BEGIN; ' + replace(sizes[i % 2]) +
                         " COMMIT; SELECT 'writer'; SELECT pg_sleep(0.002);\n"
                         for i in range(writes))
        reader = ''.join("BEGIN; SELECT pg_current_xact_id(); "
                         "SELECT json_build_object('id',doc_id,'score',score) "
                         "FROM bm25_search('alpha',1); COMMIT;\n" for _ in range(reads))
        barrier = threading.Barrier(2)

        def actor(text: str) -> tuple[subprocess.CompletedProcess[str], float, float]:
            barrier.wait(timeout=10)
            started = time.monotonic()
            result = sql(text)
            return result, started, time.monotonic()

        with ThreadPoolExecutor(max_workers=2) as pool:
            wf, rf = pool.submit(actor, writer), pool.submit(actor, reader)
            w, ws, we = wf.result()
            r, rs, re = rf.result()
        report['writer_stderr'], report['reader_stderr'] = w.stderr, r.stderr
        report['observed_writer_transactions'] = w.stdout.splitlines().count('writer')
        results = [json.loads(line) for line in r.stdout.splitlines() if line.startswith('{')]
        report['observed_reader_results'] = len(results)
        report['overlap_seconds'] = max(0., min(we, re) - max(ws, rs))
        invalid = [row for row in results if row['id'] != 1 or not math.isfinite(row['score'])
                   or not any(math.isclose(row['score'], score, rel_tol=1e-10, abs_tol=1e-14)
                              for score in expected)]
        report['inconsistent_score_examples'] = invalid[:10]
        if w.returncode or r.returncode:
            raise AssertionError('actor failed: ' + w.stderr + r.stderr)
        if report['observed_writer_transactions'] != writes or len(results) != reads:
            raise AssertionError('actor transaction/result count mismatch')
        report['checks']['all_reader_and_writer_transactions_succeed'] = True
        if report['overlap_seconds'] <= 0:
            raise AssertionError('reader and writer did not overlap')
        report['checks']['reader_writer_execution_overlaps'] = True
        if invalid:
            raise AssertionError('BM25 score mixed different committed corpus states')
        report['checks']['all_scores_match_a_complete_committed_state'] = True
        observed_states = [sum(math.isclose(row['score'], score, rel_tol=1e-10, abs_tol=1e-14)
                               for row in results) for score in expected]
        report['results_per_committed_state'] = observed_states
        if not all(observed_states):
            raise AssertionError('reader did not observe both committed corpus states')
        report['checks']['reader_observes_both_committed_states'] = True
        result = sql("BEGIN; DELETE FROM pgwarc_lance.bm25_term; "
                     "DELETE FROM pgwarc_lance.bm25_doc; "
                     "SELECT count(*) FROM bm25_search('alpha',10); "
                     "SELECT bm25_index_document(1,''); "
                     "SELECT count(*) FROM bm25_search('alpha',10); ROLLBACK;")
        if result.returncode or result.stdout.split() != ['0', '0']:
            raise AssertionError('empty or all-empty corpus returned results: ' + result.stderr)
        report['checks']['empty_and_zero_length_corpora_return_no_results'] = True
        report['status'] = 'passed'
    except Exception as exc:
        report['status'], report['error'] = 'failed', str(exc)
    finally:
        if created:
            subprocess.run(prefix + [str(Path(args.pg_bin) / 'dropdb')] + connection + ['--force', db],
                           check=True, capture_output=True, text=True, timeout=30)
    output = json.dumps(report, indent=2) + '\n'
    if args.json_output:
        args.json_output.write_text(output)
    print(output, end='')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())

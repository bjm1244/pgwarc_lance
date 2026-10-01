#!/usr/bin/env python3
"""Measure logged hybrid query latency on an already populated quality fixture.

No schema or corpus writes are made. Use a disposable benchmark database. This
measures warm, fixed-corpus throughput and transaction latency, not an SLA or
concurrent-ingestion performance. Individual SQL statements have a server
statement_timeout and the entire pgbench child has a bounded client timeout.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import subprocess
import time
from pathlib import Path
import eval_quality


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentage * len(ordered)) - 1)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-json', type=Path, required=True)
    parser.add_argument('--lance-uri', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--host', default='/var/run/postgresql')
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--user', default='pgwarc_lance')
    parser.add_argument('--pgbench', default='/usr/lib/postgresql/16/bin/pgbench')
    parser.add_argument('--output-dir', type=Path, required=True, help='new directory; existing output is never overwritten')
    parser.add_argument('--clients', type=int, default=4)
    parser.add_argument('--transactions', type=int, default=100, help='per client')
    parser.add_argument('--k', type=int, default=10)
    parser.add_argument('--statement-timeout-seconds', type=float, default=2)
    parser.add_argument('--run-timeout-seconds', type=float, default=120)
    args = parser.parse_args()
    if min(args.clients, args.transactions, args.k) <= 0 or not all(math.isfinite(v) and v > 0 for v in [args.statement_timeout_seconds,args.run_timeout_seconds]):
        parser.error('positive finite client/transaction/k/time limits required')
    fixture = eval_quality.load_fixture(args.fixture_json)
    if len(fixture['docs']) < args.k:
        parser.error('fixture must have at least k documents')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    files = []
    for index, query in enumerate(fixture['queries']):
        path = args.output_dir / f'query-{index:03d}.sql'
        # Division by zero fails the transaction if an otherwise successful
        # search returned an incomplete result set. Labels are evaluated by the
        # separate quality run; not every fixture query is expected to hit.
        sql = f"BEGIN;\nSET LOCAL statement_timeout = '{max(1,int(args.statement_timeout_seconds * 1000))}ms';\nSELECT 1 / (count(*) = {args.k})::int FROM hybrid_warc_search({eval_quality.sql_string(query['query'])},{eval_quality.vector_sql(query['vector'])},{eval_quality.sql_string(args.lance_uri)},{args.k});\nCOMMIT;\n"
        path.write_text(sql)
        files.extend(['-f', str(path)])
    command = [args.pgbench, '-h', args.host, '-p', str(args.port), '-U', args.user,
               '-n', '-c', str(args.clients), '-j', str(args.clients), '-t', str(args.transactions),
               '--random-seed=20261001', '--max-tries=1', '--verbose-errors', '-l',
               '--log-prefix=' + str(args.output_dir / 'latency')] + files + [args.database]
    started = time.monotonic()
    evidence = {'status': 'running', 'fixture_sha256': hashlib.sha256(args.fixture_json.read_bytes()).hexdigest(),
                'scope': 'warm fixed public corpus; no concurrent writers; no operational SLA',
                'documents': len(fixture['docs']), 'queries': len(fixture['queries']), 'k': args.k,
                'clients': args.clients, 'transactions_per_client': args.transactions,
                'statement_timeout_seconds': args.statement_timeout_seconds, 'command': command}
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=args.run_timeout_seconds)
        (args.output_dir / 'pgbench.stdout').write_text(result.stdout)
        (args.output_dir / 'pgbench.stderr').write_text(result.stderr)
        if result.returncode:
            raise ValueError(f'pgbench failed with exit {result.returncode}: {result.stderr[-1500:]}')
        durations, scripts, failures = [], set(), []
        for path in args.output_dir.glob('latency.*'):
            for line in path.read_text().splitlines():
                columns = line.split()
                if len(columns) < 6 or not columns[2].isdigit():
                    failures.append(line)
                    continue
                durations.append(float(columns[2]) / 1000)
                scripts.add(int(columns[3]))
        expected = args.clients * args.transactions
        if failures or len(durations) != expected:
            raise ValueError(f'expected {expected} successful logged transactions, got {len(durations)}, failed log entries {len(failures)}')
        if len(scripts) != len(fixture['queries']):
            raise ValueError('load did not exercise every fixture query; increase transactions')
        evidence.update(status='passed', successful_transactions=len(durations), exercised_query_scripts=len(scripts),
                        transaction_latency_ms={'p50':percentile(durations,.50), 'p95':percentile(durations,.95),
                                                'p99':percentile(durations,.99), 'max':max(durations)})
        return 0
    except Exception as exc:
        evidence.update(status='failed', error=str(exc))
        raise
    finally:
        evidence['elapsed_seconds'] = time.monotonic() - started
        (args.output_dir / 'load.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps(evidence,indent=2))


if __name__ == '__main__':
    raise SystemExit(main())

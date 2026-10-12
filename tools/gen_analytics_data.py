#!/usr/bin/env python3
# Copyright 2016-2026 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

'''Populate an analytics database with synthetic results.

This generates run reports for a hypothetical cluster and stores them in the
DuckDB analytics database, in the legacy SQLite database, or in both, so that
queries can be exercised at scale and the two backends compared on the same
data.

Two scenarios are simulated:

single
    The single-node tests run on every compute node of the cluster: one
    session per node, each running all of them.

multi
    The multi-node tests run on one block of nodes each: one session per
    block, running a single test family over that block. Blocks are
    contiguous, non-overlapping sets of nodes.

A sweep is one pass of each enabled scenario, with the scenarios interleaved
so that any prefix of the data set holds a representative mix of them. Sweeps
are generated until the requested number of sessions has been stored; how many
rows those sessions become is up to each backend's schema, which is exactly
what the two are being compared on.
'''

import argparse
import heapq
import os
import random
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import reframe.core.config as config          # noqa: E402
import reframe.core.runtime as runtime        # noqa: E402
import reframe.analytics as analytics         # noqa: E402
import reframe.frontend.reporting as reporting   # noqa: E402
from reframe.analytics.backends import Backend   # noqa: E402
from reframe.utility import nodelist_abbrev      # noqa: E402

#: Tests that run on a single compute node, with their parameters and their
#: performance variables as (name, unit, reference, scale)
SINGLE_NODE_TESTS = [
    ('GEMMTest',
     {'dtype': ['fp32', 'fp16', 'bf16'], 'mode': ['nn', 'tn']},
     [('tflops', 'Tflop/s', 320.0, 0.05), ('power', 'W', 450.0, 0.1)]),
    ('StreamTest',
     {'elem_type': ['float', 'double']},
     [('triad_bw', 'GB/s', 2800.0, 0.03), ('copy_bw', 'GB/s', 3100.0, 0.03)]),
]

#: Tests that run on a whole block of nodes
MULTI_NODE_TESTS = [
    ('NCCLTest',
     {'nodes': [2, 4, 8]},
     [('busbw', 'GB/s', 180.0, 0.08), ('latency', 'us', 12.0, 0.15)]),
]

#: Test that builds on a login node
BUILD_TEST = ('BuildTest', {}, [])

#: Tests that may fail
FLAKY_TESTS = frozenset({'GEMMTest', 'NCCLTest'})


def _parse_size(value):
    '''Parse a row count such as `2M` or `500k`'''

    suffixes = {'k': 10**3, 'm': 10**6, 'g': 10**9}
    factor = suffixes.get(value[-1].lower(), 1)
    return int(float(value[:-1]) * factor) if factor != 1 else int(value)


def _parse_interval(value):
    '''Parse a time interval such as `6h` or `30m`'''

    suffixes = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    factor = suffixes.get(value[-1].lower())
    return float(value[:-1]) * factor if factor else float(value)


def _parameter_combinations(params):
    '''Expand a parameter space into a list of dictionaries'''

    combos = [{}]
    for name, values in params.items():
        combos = [{**c, name: v} for c in combos for v in values]

    return combos


class Cluster:
    '''The node names of a hypothetical cluster'''

    def __init__(self, num_nodes, block_size, num_login_nodes=2):
        self.nodes = [f'node{i:04d}' for i in range(1, num_nodes + 1)]
        self.login_nodes = [f'cluster-login{i:02d}'
                            for i in range(1, num_login_nodes + 1)]
        self.blocks = [self.nodes[i:i + block_size]
                       for i in range(0, num_nodes, block_size)]


def _sweep_totals(cluster, options):
    '''The sessions and rows of one sweep of each enabled scenario'''

    rows = sessions = 0
    if 'single' in options.scenarios:
        # Every single-node test on every node, plus the build on a login node
        per_session = 1 + sum(
            len(_parameter_combinations(params)) * max(1, len(pvars))
            for _, params, pvars in SINGLE_NODE_TESTS
        )
        rows += len(cluster.nodes) * per_session
        sessions += len(cluster.nodes)

    if 'multi' in options.scenarios:
        # One session per block and test family
        for _, params, pvars in MULTI_NODE_TESTS:
            rows += len(cluster.blocks) * (
                len(_parameter_combinations(params)) * max(1, len(pvars))
            )
            sessions += len(cluster.blocks)

    return sessions, rows


def _interleave(streams):
    '''Yield from several streams, spreading each evenly over the output.

    The scenarios differ by orders of magnitude in the number of sessions
    they produce, so without this any prefix of the data set would hold only
    the larger one.
    '''

    heap = [(0.5/n, 1/n, i, it) for i, (n, it) in enumerate(streams) if n]
    heapq.heapify(heap)
    while heap:
        pos, step, i, it = heapq.heappop(heap)
        try:
            yield next(it)
        except StopIteration:
            continue

        heapq.heappush(heap, (pos + step, step, i, it))


class ReportGenerator:
    def __init__(self, cluster, options, num_sessions_total):
        self.cluster = cluster
        self.opts = options
        self.rng = random.Random(options.seed)
        self.time = options.start
        self.num_sessions = 0

        # The driver version changes at evenly spaced points of the data set
        self.sessions_per_driver = max(
            1, num_sessions_total // (options.driver_changes + 1)
        )

    @property
    def driver_index(self):
        return min(self.num_sessions // self.sessions_per_driver,
                   self.opts.driver_changes)

    @property
    def driver_version(self):
        return f'v{self.driver_index + 1}.0'

    def _blob(self, name, tc_time):
        '''A job script of a given size, unique enough not to compress away'''

        head = (f'#!/bin/bash\n#SBATCH --job-name={name}\n'
                f'#SBATCH --time=00:10:00\n# generated at {tc_time}\n')
        if self.opts.blob_size <= len(head):
            return head

        pad = self.rng.getrandbits(32)
        body = f'# {pad:08x}\n' * ((self.opts.blob_size - len(head)) // 12)
        return head + body

    def _perfvalues(self, pvars, failing):
        '''Build the `perfvalues` mapping of a test case'''

        perfvalues = {}
        for name, unit, ref, frac in pvars:
            # Noise around the reference, with a step at every driver change,
            # so that comparisons across driver versions show a real delta
            drift = 1.0 + 0.02*self.rng.gauss(0, 1) + 0.05*self.driver_index
            value = ref * drift
            if failing:
                value = ref * (1 - 3*frac)

            result = 'fail' if failing else 'pass'
            perfvalues[name] = [round(value, 3), ref, -frac, frac, unit,
                                result]

        return perfvalues

    def _testcase(self, test, params, nodes, index, failing):
        name, _, pvars = test
        display = name
        if params:
            display += ' ' + ' '.join(f'%{k}={v}' for k, v in params.items())

        # Times are consistent but not serialized: all tests of a session may
        # run in parallel
        submit = self.time + self.rng.uniform(1, 30)
        start = submit + self.rng.uniform(1, 60)
        completion = start + self.rng.uniform(30, 600)
        fmt = reporting._format_time_rfc3339

        partition = 'login' if not nodes[0].startswith('node') else 'gpu'
        sysenv = f'{self.opts.system}:{partition}+builtin'
        tc = {
            'name': display,
            'basename': name,
            'display_name': display,
            'short_name': display,
            'unique_name': display,
            'system': self.opts.system,
            'partition': partition,
            'environ': 'builtin',
            'sysenv': sysenv,
            'result': 'fail' if failing else 'pass',
            'run_index': 0,
            'testcase_index': index,
            'job_scheduler': 'slurm',
            'job_id': str(self.rng.randint(10**6, 10**7)),
            'job_state': 'FAILED' if failing else 'COMPLETED',
            'job_exitcode': 1 if failing else 0,
            'job_nodelist': nodes,
            'job_nodelist_folded': nodelist_abbrev(nodes),
            'job_submit_time_us': int(submit * 1e6),
            'job_start_time_us': int(start * 1e6),
            'job_completion_time_us': int(completion * 1e6),
            'job_submit_timestamp': fmt(submit, '%FT%T%:z'),
            'job_start_timestamp': fmt(start, '%FT%T%:z'),
            'job_completion_timestamp': fmt(completion, '%FT%T%:z'),
            'job_completion_time_unix': completion,
            'job_script_contents': self._blob(display, int(completion)),
            'num_tasks': len(nodes),
            'num_tasks_per_node': 1,
            'time_limit': 600,
            'tags': ['production', name.lower()],
            'maintainers': ['perf-team'],
            'env_vars': {'OMP_NUM_THREADS': '8',
                         'NCCL_DEBUG': 'WARN'},
            'perfvalues': self._perfvalues(pvars, failing) if pvars else {},
            **params
        }
        if failing:
            tc['fail_phase'] = 'performance'
            tc['fail_info'] = {
                'exc_type': 'PerformanceError',
                'exc_value': f'failed to meet reference for {display}',
                'traceback': ['  File "pipeline.py", line 1, in performance\n']
            }

        return tc

    def _session(self, testcases):
        '''Wrap test cases into a report, retrying the failed ones'''

        # The session must outlive its test cases, which run in parallel
        start = self.time
        end = max((tc['job_completion_time_us'] / 1e6
                   for tc in testcases), default=start)
        end += self.rng.uniform(1, 60)
        runs = [{'run_index': 0, 'num_cases': len(testcases),
                 'num_failures': sum(tc['result'] == 'fail'
                                     for tc in testcases),
                 'testcases': testcases}]

        # `testcase_index` is the position of the test case within its own
        # run, so the retried ones have to be renumbered; the legacy backend
        # indexes into the run with it
        retried = [dict(tc, run_index=1, testcase_index=i, result='pass',
                        job_state='COMPLETED', job_exitcode=0,
                        fail_phase=None, fail_info=None,
                        perfvalues=self._perfvalues(
                            _test_by_name(tc['basename'])[2], False
                        ))
                   for i, tc in enumerate(tc for tc in testcases
                                          if tc['result'] == 'fail')]
        if retried:
            runs.append({'run_index': 1, 'num_cases': len(retried),
                         'num_failures': 0, 'testcases': retried})

        driver = self.driver_version
        self.num_sessions += 1
        fmt = reporting._format_time_rfc3339
        return {
            'session_info': {
                # Drawn from the seeded generator, so that the whole data set
                # is reproducible from `--seed`
                'uuid': str(uuid.UUID(int=self.rng.getrandbits(128),
                                      version=4)),
                'data_version': reporting.DATA_VERSION,
                'hostname': f'{self.opts.system}-login01',
                'user': 'perfuser',
                'version': '5.0.0-dev.0',
                'cmdline': 'reframe -c checks/ -r',
                'config_files': ['/etc/reframe.d/settings.py'],
                'prefix_output': '/scratch/reframe/output',
                'prefix_stage': '/scratch/reframe/stage',
                'workdir': '/scratch/reframe',
                'driver_version': driver,
                'time_start': fmt(start, '%FT%T%:z'),
                'time_end': fmt(end, '%FT%T%:z'),
                'time_start_unix': start,
                'time_end_unix': end,
                'time_elapsed': end - start,
                'start_time_us': int(start * 1e6),
                'end_time_us': int(end * 1e6),
                'num_cases': len(testcases),
                'num_failures': runs[0]['num_failures'],
                'num_aborted': 0,
                'num_skipped': 0,
            },
            'runs': runs,
            'restored_cases': []
        }

    def _failing(self, test_name):
        return (test_name in FLAKY_TESTS and
                self.rng.random() < self.opts.fail_rate)

    def _single_node_sessions(self):
        '''One session per compute node, running all single-node tests'''

        for node in self.cluster.nodes:
            testcases = []
            for test in SINGLE_NODE_TESTS:
                for params in _parameter_combinations(test[1]):
                    testcases.append(self._testcase(
                        test, params, [node], len(testcases),
                        self._failing(test[0])
                    ))

            # The build runs on a login node, not on the node under test
            login = self.rng.choice(self.cluster.login_nodes)
            testcases.append(self._testcase(BUILD_TEST, {}, [login],
                                            len(testcases), False))
            yield self._session(testcases)
            self.time += self.opts.interval

    def _multi_node_sessions(self):
        '''One session per block, running a single multi-node test family'''

        for block_id, block in enumerate(self.cluster.blocks, start=1):
            for test in MULTI_NODE_TESTS:
                testcases = []
                for params in _parameter_combinations(test[1]):
                    nodes = block[:params.get('nodes', len(block))]
                    testcases.append(self._testcase(
                        test, {**params,
                               'topology_block': f'block{block_id:02d}'},
                        nodes, len(testcases), self._failing(test[0])
                    ))

                yield self._session(testcases)
                self.time += self.opts.interval

    def sweep(self):
        '''Generate one pass of each enabled scenario, interleaved'''

        streams = []
        if 'single' in self.opts.scenarios:
            streams.append((len(self.cluster.nodes),
                            self._single_node_sessions()))

        if 'multi' in self.opts.scenarios:
            streams.append((len(self.cluster.blocks) * len(MULTI_NODE_TESTS),
                            self._multi_node_sessions()))

        yield from _interleave(streams)


def _num_rows(report):
    '''Number of results rows a report flattens to.

    Computed without flattening, so that the amount of data generated does
    not depend on which backends are being written to.
    '''

    return sum(max(1, len(tc['perfvalues']))
               for run in report['runs'] for tc in run['testcases'])


def _test_by_name(name):
    for test in SINGLE_NODE_TESTS + MULTI_NODE_TESTS + [BUILD_TEST]:
        if test[0] == name:
            return test

    raise KeyError(name)


def _init_runtime(sqlite_file):
    '''Set up a minimal runtime, since the SQLite backend reads its file from
    the configuration'''

    site_config = config.load_config('<builtin>')
    site_config.select_subconfig('generic')
    if sqlite_file:
        site_config.add_sticky_option('storage/sqlite_db_file', sqlite_file)

    runtime.init_runtime(site_config)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('-n', '--sessions', type=_parse_size, default='10k',
                        metavar='N',
                        help=('Number of run sessions to store (e.g. 5k, 1M); '
                              'how many rows these become is up to each '
                              'backend'))
    parser.add_argument('--duckdb', metavar='URI',
                        help='URI of the DuckDB analytics database')
    parser.add_argument('--sqlite', metavar='FILE',
                        help='Path of the legacy SQLite database')
    parser.add_argument('--nodes', type=int, default=256, metavar='N',
                        help='Number of compute nodes in the cluster')
    parser.add_argument('--block-size', type=int, default=8, metavar='N',
                        help='Number of nodes per topology block')
    parser.add_argument('--scenarios', default='single,multi',
                        help='Scenarios to generate: single, multi or both')
    parser.add_argument('--fail-rate', type=float, default=0.01,
                        metavar='RATE',
                        help='Fraction of flaky test cases that fail')
    parser.add_argument('--start', default='2026-01-01',
                        help='Date of the oldest session')
    parser.add_argument('--interval', type=_parse_interval, default='15m',
                        help='Time between sessions (e.g. 6h, 30m)')
    parser.add_argument('--driver-changes', type=int, default=2, metavar='N',
                        help=('Number of driver version changes to spread '
                              'over the data set'))
    parser.add_argument('--blob-size', type=int, default=512, metavar='BYTES',
                        help='Size of the synthetic job script of each test')
    parser.add_argument('--batch', type=int, default=200, metavar='N',
                        help='Sessions per DuckDB append')
    parser.add_argument('--system', default='cluster',
                        help='Name of the hypothetical system')
    parser.add_argument('--seed', type=int, default=1,
                        help='Seed of the random generator')
    options = parser.parse_args()
    if not options.duckdb and not options.sqlite:
        parser.error('at least one of --duckdb or --sqlite is required')

    options.scenarios = options.scenarios.split(',')
    options.start = time.mktime(time.strptime(options.start, '%Y-%m-%d'))

    _init_runtime(options.sqlite)
    backend = Backend.create(options.duckdb) if options.duckdb else None
    storage = None
    if options.sqlite:
        storage = reporting.StorageBackend.create('sqlite')

    cluster = Cluster(options.nodes, options.block_size)
    sessions_sweep, rows_sweep = _sweep_totals(cluster, options)
    if not sessions_sweep:
        parser.error(f'no scenario selected: {options.scenarios}')

    generator = ReportGenerator(cluster, options, options.sessions)
    print(f'Cluster: {len(cluster.nodes)} nodes in {len(cluster.blocks)} '
          f'blocks of {options.block_size}, '
          f'{len(cluster.login_nodes)} login nodes')
    print(f'Storing {options.sessions:,d} sessions from the '
          f'{", ".join(options.scenarios)} scenario(s); one sweep is '
          f'{sessions_sweep:,d} sessions / {rows_sweep:,d} rows, '
          f'{options.driver_changes} driver change(s)')

    import pyarrow as pa

    num_rows = num_sessions = 0
    pending = []
    t_start = time.time()
    while num_sessions < options.sessions:
        for report in generator.sweep():
            num_sessions += 1
            num_rows += _num_rows(report)
            if storage:
                storage.store(report)

            if backend:
                pending.append(analytics.flatten(report))
                if len(pending) >= options.batch:
                    backend.append(pa.concat_tables(pending))
                    pending.clear()

            if num_sessions % options.batch == 0:
                _progress(num_rows, num_sessions, options, t_start)

            if num_sessions >= options.sessions:
                break

    if backend and pending:
        backend.append(pa.concat_tables(pending))

    _progress(num_rows, num_sessions, options, t_start)
    print()
    for label, path in (('DuckDB', _db_path(options.duckdb)),
                        ('SQLite', options.sqlite)):
        if path and os.path.exists(path):
            print(f'{label}: {path} '
                  f'({os.path.getsize(path) / 1e6:.1f} MB)')


def _db_path(database):
    return database.partition('://')[2] if database else None


def _progress(num_rows, num_sessions, options, t_start):
    elapsed = max(time.time() - t_start, 1e-9)
    pct = 100 * num_sessions / options.sessions
    print(f'\r  {num_sessions:>10,d} sessions / {num_rows:>12,d} rows  '
          f'{min(pct, 100):5.1f}%  {num_sessions / elapsed:>8,.0f} sess/s',
          end='', flush=True)


if __name__ == '__main__':
    main()

# Copyright 2016-2026 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import json
import math
import os
import pytest
import subprocess
import sys

import reframe.analytics as analytics
import reframe.analytics.schema as schema
import reframe.analytics.writer as writer
import reframe.utility.osext as osext
from reframe.analytics.backends import Backend, BackendBusy
from reframe.core.exceptions import ReframeError, SanityError

duckdb = pytest.importorskip('duckdb')
pa = pytest.importorskip('pyarrow')


def _testcase(index, name, **kwargs):
    return {
        'system': 'testsys', 'partition': 'gpu', 'environ': 'gnu',
        'sysenv': 'testsys:gpu+gnu',
        'name': name, 'basename': name, 'display_name': name,
        'result': 'success', 'run_index': 0, 'testcase_index': index,
        'job_scheduler': 'slurm', 'job_exitcode': 0,
        **kwargs
    }


@pytest.fixture
def report():
    perf_case = _testcase(
        0, 'A',
        env_vars={'OMP_NUM_THREADS': '4'}, tags=['perf', 'gpu'],
        fail_phase=None,
        perfvalues={
            'tflops': [12.5, 10.0, -0.1, 0.1, 'Tflop/s', 'pass'],
            'power': [250.0, 300.0, -0.2, None, 'W', 'pass']
        }
    )
    failed_case = _testcase(
        1, 'B', result='fail', fail_phase='sanity',
        # `exc_type` is an exception class, not a JSON type
        fail_info={'exc_type': SanityError, 'traceback': ['x']}
    )
    return {
        'session_info': {'uuid': 'uuid-1', 'hostname': 'host',
                         'driver_version': '123.4'},
        'runs': [{'num_cases': 2, 'num_failures': 1, 'run_index': 0,
                  'testcases': [perf_case, failed_case]}],
        'restored_cases': [_testcase(0, 'restored')]
    }


@pytest.fixture
def rows(report):
    # Arrow renders a MAP column as a list of key/value tuples by default,
    # since a MAP is ordered and may hold duplicate keys
    return analytics.flatten(report).to_pylist(maps_as_pydicts='strict')


def test_schema_consistency():
    assert set(schema.TC_TYPED_FIELDS) <= set(schema.COLUMNS)
    assert schema.arrow_schema().names == list(schema.COLUMNS)


def test_flatten_row_per_perfvalue(rows):
    # One row per performance variable of `A`, and one row for `B`, which is
    # not a performance test; restored cases are ignored
    assert [(r['name'], r['pvar']) for r in rows] == [
        ('A', 'tflops'), ('A', 'power'), ('B', None)
    ]

    tflops, power, _ = rows
    assert (tflops['pval'], tflops['pref']) == (12.5, 10.0)
    assert tflops['plower'] == pytest.approx(9.0)
    assert tflops['pupper'] == pytest.approx(11.0)
    assert (tflops['punit'], tflops['pres']) == ('Tflop/s', 'pass')

    # A missing bound is unbounded
    assert power['plower'] == pytest.approx(240.0)
    assert power['pupper'] == math.inf


def test_flatten_no_perfvalues(rows):
    failed = rows[2]
    for col in ('pvar', 'pval', 'pref', 'plower', 'pupper', 'punit', 'pres'):
        assert failed[col] is None

    assert failed['result'] == 'fail'


def test_flatten_typed_columns(rows):
    row = rows[0]
    assert row['testcase_index'] == 0
    assert row['sysenv'] == 'testsys:gpu+gnu'
    assert row['job_scheduler'] == 'slurm'
    assert row['job_exitcode'] == 0
    assert row['job_id'] is None


def test_flatten_session_and_run_data(rows):
    # The session and the run data are replicated onto every row, so that
    # they can be queried without a join
    for row in rows:
        assert row['sess_data'] == {'hostname': 'host',
                                    'driver_version': '123.4'}
        assert row['run_data'] == {'num_cases': '2', 'num_failures': '1'}
        assert row['sess_uuid'] == 'uuid-1'
        assert row['run_index'] == 0


def test_flatten_tc_data(rows):
    data = rows[0]['tc_data']
    assert data['$env_OMP_NUM_THREADS'] == '4'
    assert data['tags'] == '["perf", "gpu"]'

    # `None` is `NULL`, not an empty string
    assert 'fail_phase' in data and data['fail_phase'] is None

    # All the performance variables of the test case are available in each of
    # its rows, prefixed with `$` so that they cannot collide with an actual
    # test variable name
    assert data['$tflops'] == '12.5'
    assert data['$tflops_ref'] == '10.0'
    assert float(data['$tflops_lower']) == pytest.approx(9.0)
    assert float(data['$tflops_upper']) == pytest.approx(11.0)
    assert data['$power_upper'] == 'inf'
    assert rows[1]['tc_data'] == data

    # Typed and specially handled fields are not repeated
    for key in ('name', 'result', 'env_vars', 'perfvalues', 'run_index'):
        assert key not in data


def test_flatten_absent_keys_are_not_padded(rows):
    failed = rows[2]['tc_data']
    assert json.loads(failed['fail_info'])['exc_type'] == 'SanityError'
    assert '$tflops' not in failed
    assert '$env_OMP_NUM_THREADS' not in failed


def test_flatten_empty_report():
    table = analytics.flatten({'session_info': {'uuid': 'u'}, 'runs': []})
    assert table.num_rows == 0
    assert table.schema.equals(schema.arrow_schema())


@pytest.fixture
def db_file(tmp_path):
    return str(tmp_path / 'sub' / 'results.duckdb')


@pytest.fixture
def database(db_file):
    # An absolute file URI has three slashes, the last of which is the one of
    # the path itself
    return f'duckdb://{db_file}'


def _query(db_file, sql):
    conn = duckdb.connect(db_file, read_only=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _count(db_file):
    return _query(db_file, f'SELECT count(*) FROM {schema.TABLE_NAME}')


def test_store_and_append(report, database, db_file):
    analytics.store(report, database)
    assert _count(db_file) == [(3,)]

    # Storing the same session again is a no-op
    analytics.store(report, database)
    assert _count(db_file) == [(3,)]

    # A different session is appended
    report['session_info']['uuid'] = 'uuid-2'
    analytics.store(report, database)
    assert _count(db_file) == [(6,)]
    assert _query(
        db_file,
        f'SELECT DISTINCT sess_uuid FROM {schema.TABLE_NAME} ORDER BY 1'
    ) == [('uuid-1',), ('uuid-2',)]


def test_stored_types_and_values(report, database, db_file):
    analytics.store(report, database)
    types = {row[0]: row[1]
             for row in _query(db_file, f'DESCRIBE {schema.TABLE_NAME}')}
    assert types['pval'] == 'DOUBLE'
    assert types['job_exitcode'] == 'BIGINT'
    assert types['tc_data'] == 'MAP(VARCHAR, VARCHAR)'

    # A missing key gives `NULL`, so casts of absent perf variables succeed
    res = _query(
        db_file,
        "SELECT name, pvar, tc_data['$power']::DOUBLE, "
        "tc_data['nonexistent'] "
        f"FROM {schema.TABLE_NAME} ORDER BY testcase_index, pvar"
    )
    assert res == [('A', 'power', 250.0, None),
                   ('A', 'tflops', 250.0, None),
                   ('B', None, None, None)]


def test_append_releases_lock(report, database, db_file):
    analytics.store(report, database)
    osext.run_command(
        [sys.executable, '-c',
         'import duckdb, sys; duckdb.connect(sys.argv[1]).close()', db_file],
        check=True, timeout=60
    )


def test_append_busy(report, database, db_file):
    analytics.store(report, database)
    holder = osext.run_command_async(
        [sys.executable, '-c',
         'import duckdb, sys\n'
         'conn = duckdb.connect(sys.argv[1])\n'
         'print("ready", flush=True)\n'
         'sys.stdin.read()', db_file],
        stdin=subprocess.PIPE
    )
    try:
        assert holder.stdout.readline().strip() == 'ready'
        with pytest.raises(BackendBusy):
            analytics.store(report, database)
    finally:
        holder.communicate(timeout=60)

    # The database is usable again once the other process releases it
    report['session_info']['uuid'] = 'uuid-2'
    analytics.store(report, database)
    assert _count(db_file) == [(6,)]


def test_database_uri(monkeypatch):
    monkeypatch.setenv('TEST_DB_DIR', '/data/analytics')

    # The expanded variable supplies the third slash of the file URI itself
    backend = Backend.create('duckdb://${TEST_DB_DIR}/results.duckdb')
    assert backend._db_file == '/data/analytics/results.duckdb'
    assert backend.fallback_dir == '/data/analytics'

    assert Backend.create(
        'duckdb:///data/analytics/results.duckdb'
    )._db_file == '/data/analytics/results.duckdb'

    # The location is taken verbatim, so it is relative without a leading
    # slash
    assert Backend.create('duckdb://results.duckdb')._db_file == \
        'results.duckdb'


def test_invalid_database_uri(report):
    with pytest.raises(ReframeError, match='no such analytics backend'):
        analytics.store(report, 'foo:///results.db')

    with pytest.raises(ReframeError, match='invalid analytics database URI'):
        analytics.store(report, '/results.duckdb')

    with pytest.raises(ReframeError, match='no analytics database file'):
        analytics.store(report, 'duckdb://')

    # An in-memory database would discard the results right after storing them
    for uri in ('duckdb://:memory:', 'duckdb://:memory:named'):
        with pytest.raises(ReframeError, match='in-memory analytics'):
            analytics.store(report, uri)


class _FlakyBackend(Backend):
    '''Backend that is busy for its first ``num_busy`` appends'''

    def __init__(self, num_busy, fallback_dir):
        self.num_busy = num_busy
        self.appended = []
        self._fallback_dir = fallback_dir

    @property
    def fallback_dir(self):
        return self._fallback_dir

    def append(self, table):
        if self.num_busy > 0:
            self.num_busy -= 1
            raise BackendBusy('busy')

        self.appended.append(table)


@pytest.fixture
def no_sleep(monkeypatch):
    '''Record the retry intervals instead of actually sleeping'''

    intervals = []
    monkeypatch.setattr(writer.time, 'sleep', intervals.append)
    return intervals


@pytest.fixture
def table(report):
    return analytics.flatten(report)


def test_writer_no_retry_needed(table, tmp_path, no_sleep):
    backend = _FlakyBackend(0, str(tmp_path))
    assert writer.append(backend, table, backend.fallback_dir) is None
    assert len(backend.appended) == 1
    assert no_sleep == []
    assert list(tmp_path.iterdir()) == []


def test_writer_retries_until_free(table, tmp_path, no_sleep):
    backend = _FlakyBackend(3, str(tmp_path))
    assert writer.append(backend, table, backend.fallback_dir) is None
    assert len(backend.appended) == 1
    assert len(no_sleep) == 3
    assert list(tmp_path.iterdir()) == []


def test_writer_retry_intervals_back_off(table, tmp_path, no_sleep):
    backend = _FlakyBackend(4, str(tmp_path))
    writer.append(backend, table, backend.fallback_dir, base_interval=1,
                  max_interval=60)

    # Equal jitter: every interval lies in the upper half of its nominal one,
    # and the nominal interval doubles on every retry
    for attempt, interval in enumerate(no_sleep):
        nominal = 2**attempt
        assert nominal/2 <= interval <= nominal

    # The randomization must actually randomize
    assert len(set(no_sleep)) > 1


def test_writer_retry_intervals_are_capped(table, tmp_path, no_sleep):
    backend = _FlakyBackend(10, str(tmp_path))
    writer.append(backend, table, backend.fallback_dir, max_retries=10,
                  base_interval=1, max_interval=4)
    assert max(no_sleep) <= 4


def test_writer_falls_back_to_parquet(table, tmp_path, no_sleep):
    import pyarrow.parquet as pq

    backend = _FlakyBackend(100, str(tmp_path / 'sub'))
    filename = writer.append(backend, table, backend.fallback_dir,
                             max_retries=2)
    assert backend.appended == []
    assert len(no_sleep) == 2

    # The results are not lost: the Parquet file is named after the session,
    # holds the same rows and can be imported later
    assert os.path.basename(filename) == 'uuid-1.parquet'
    assert [filename] == [str(p) for p in (tmp_path / 'sub').iterdir()]
    assert pq.read_table(filename).equals(table)


def test_writer_no_fallback_for_empty_table(tmp_path, no_sleep):
    empty = analytics.flatten({'session_info': {'uuid': 'u'}, 'runs': []})
    backend = _FlakyBackend(100, str(tmp_path / 'sub'))
    assert writer.append(backend, empty, backend.fallback_dir,
                         max_retries=1) is None
    assert not (tmp_path / 'sub').exists()


def test_writer_propagates_other_errors(table, tmp_path, no_sleep):
    class _BrokenBackend(_FlakyBackend):
        def append(self, table):
            raise ReframeError('not a lock problem')

    backend = _BrokenBackend(0, str(tmp_path))
    with pytest.raises(ReframeError, match='not a lock problem'):
        writer.append(backend, table, backend.fallback_dir)

    assert no_sleep == []


def test_store_persistent_to_db(report, database, db_file):
    assert analytics.store(report, database, persistent=True) is None
    assert _count(db_file) == [(3,)]


def test_store_persistent_fallback_when_busy(report, database, db_file):
    # Create the database first, so that the holder below can lock it
    analytics.store(report, database)
    holder = osext.run_command_async(
        [sys.executable, '-c',
         'import duckdb, sys\n'
         'conn = duckdb.connect(sys.argv[1])\n'
         'print("ready", flush=True)\n'
         'sys.stdin.read()', db_file],
        stdin=subprocess.PIPE
    )
    try:
        assert holder.stdout.readline().strip() == 'ready'
        filename = analytics.store(
            report, database, persistent=True,
            retry_options={'max_retries': 1, 'base_interval': 0.01}
        )
    finally:
        holder.communicate(timeout=60)

    # The fallback lands next to the database file, named after the session
    assert os.path.dirname(filename) == os.path.dirname(db_file)
    assert os.path.basename(filename) == 'uuid-1.parquet'

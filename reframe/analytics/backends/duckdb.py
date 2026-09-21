# Copyright 2016-2026 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import os
from pathlib import Path

import reframe.utility.osext as osext
from reframe.core.runtime import runtime
from ..schema import COLUMNS, FLOAT, INT, STR, STR_MAP, TABLE_NAME
from . import Backend, BackendBusy

_DUCKDB_TYPES = {
    STR: 'VARCHAR',
    INT: 'BIGINT',
    FLOAT: 'DOUBLE',
    STR_MAP: 'MAP(VARCHAR, VARCHAR)',
}


class DuckDBBackend(Backend):
    '''Analytics backend that stores the results in a DuckDB database file.

    :arg db_file: path of the database file. If :obj:`None`, the
        ``storage/sqlite_db_file`` option is reused with its extension
        replaced by ``.duckdb``.
    '''

    def __init__(self, db_file=None):
        if db_file is None:
            sqlite_file = osext.expandvars(
                runtime().get_option('storage/0/sqlite_db_file')
            )
            db_file = str(Path(sqlite_file).with_suffix('.duckdb'))

        self._db_file = db_file

    def append(self, table):
        import duckdb

        os.makedirs(os.path.dirname(self._db_file) or '.', exist_ok=True)
        try:
            conn = duckdb.connect(self._db_file)
        except duckdb.IOException as err:
            if 'lock' in str(err).lower():
                raise BackendBusy(
                    f'database {self._db_file!r} is locked'
                ) from err

            raise

        # The connection is closed on exit, so that the file lock is released
        # as soon as possible for the other writers
        with conn:
            columns = ', '.join(f'{name} {_DUCKDB_TYPES[typ]}'
                                for name, typ in COLUMNS.items())
            conn.register('batch', table)
            conn.begin()
            try:
                conn.execute(f'CREATE TABLE IF NOT EXISTS {TABLE_NAME} '
                             f'({columns})')
                conn.execute(f'INSERT INTO {TABLE_NAME} BY NAME '
                             f'SELECT * FROM batch')
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()

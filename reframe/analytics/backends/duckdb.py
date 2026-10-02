# Copyright 2016-2026 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import os

from reframe.core.exceptions import ReframeError
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

    :arg db_file: path of the database file, i.e. the location part of a
        ``duckdb://`` URI, taken verbatim; it is relative unless it starts
        with a slash. In-memory databases are not supported, since the results
        would be discarded as soon as they were stored.
    :raises ReframeError: if no database file is given or if it refers to an
        in-memory database.
    '''

    def __init__(self, db_file):
        if not db_file:
            raise ReframeError('no analytics database file specified')

        # Named in-memory databases are spelled `:memory:<name>`
        if db_file.startswith(':memory:'):
            raise ReframeError('in-memory analytics databases are not '
                               'supported')

        self._db_file = db_file

    @property
    def fallback_dir(self):
        return os.path.dirname(self._db_file) or '.'

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

                # Sessions are stored at most once, so that re-appending a
                # session, e.g. by importing the same results twice, does not
                # duplicate its rows. The subquery sees the table as it was
                # before this statement, so either all rows of a new session
                # are inserted or none at all.
                conn.execute(
                    f'INSERT INTO {TABLE_NAME} BY NAME '
                    f'SELECT * FROM batch b '
                    f'WHERE NOT EXISTS (SELECT 1 FROM {TABLE_NAME} t '
                    f'WHERE t.sess_uuid = b.sess_uuid)'
                )
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()

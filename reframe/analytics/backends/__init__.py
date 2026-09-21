# Copyright 2016-2026 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import abc

from reframe.core.exceptions import ReframeError


class BackendBusy(ReframeError):
    '''Raised when the database is locked by another process.'''


class Backend(abc.ABC):
    '''Abstract class that represents an analytics database backend.'''

    @classmethod
    def create(cls, backend, *args, **kwargs):
        '''Factory method for creating analytics backends'''

        if backend == 'duckdb':
            from .duckdb import DuckDBBackend
            return DuckDBBackend(*args, **kwargs)
        else:
            raise ReframeError(f'no such analytics backend: {backend}')

    @abc.abstractmethod
    def append(self, table):
        '''Append the rows of an Arrow table to the results table.

        The results table is created if it does not exist. The database must
        not be held locked after this method returns.

        :arg table: a :class:`pyarrow.Table` following the schema of
            :func:`reframe.analytics.schema.arrow_schema`.
        :raises BackendBusy: if the database is locked by another process.
        '''

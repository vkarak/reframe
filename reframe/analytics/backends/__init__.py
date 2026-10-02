# Copyright 2016-2026 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import abc

import reframe.utility.osext as osext
from reframe.core.exceptions import ReframeError


class BackendBusy(ReframeError):
    '''Raised when the database is locked by another process.'''


class Backend(abc.ABC):
    '''Abstract class that represents an analytics database backend.'''

    @classmethod
    def create(cls, database, *args, **kwargs):
        '''Factory method for creating analytics backends.

        :arg database: URI of the database in the form
            ``<backend>://<location>``. The scheme selects the backend and the
            location is interpreted by the backend itself; for the file-based
            ones it is simply a path, so that an absolute file URI carries the
            customary three slashes, as in
            ``duckdb:///home/user/results.duckdb``. Any environment variable
            reference in the URI is expanded before it is parsed, which is
            also the way to supply the credentials of a remote backend without
            storing them in the configuration. Note that a variable holding an
            absolute path supplies the third slash itself, as in
            ``duckdb://${HOME}/results.duckdb``.
        :arg args: arguments to pass to the backend's constructor.
        :arg kwargs: keyword arguments to pass to the backend's constructor.
        :raises ReframeError: if the URI has no scheme or its scheme does not
            name a known backend.
        '''

        database = osext.expandvars(database)
        scheme, sep, location = database.partition('://')
        if not sep:
            raise ReframeError(
                f'invalid analytics database URI: {database!r}; it must be of '
                f'the form <backend>://<location>'
            )

        if scheme == 'duckdb':
            from .duckdb import DuckDBBackend
            return DuckDBBackend(location, *args, **kwargs)
        else:
            raise ReframeError(f'no such analytics backend: {scheme!r}')

    @abc.abstractmethod
    def append(self, table):
        '''Append the rows of an Arrow table to the results table.

        The results table is created if it does not exist. The database must
        not be held locked after this method returns.

        Appending is idempotent per session: the rows of a session that is
        already stored are ignored, so that appending the same results twice,
        e.g. by importing them again, does not duplicate them.

        :arg table: a :class:`pyarrow.Table` following the schema of
            :func:`reframe.analytics.schema.arrow_schema`.
        :raises BackendBusy: if the database is locked by another process.
        '''

    @property
    @abc.abstractmethod
    def fallback_dir(self):
        '''Directory where results that cannot be stored in this backend will
        be written to, so that they can be imported at a later time.'''

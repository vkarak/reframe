# Copyright 2016-2026 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

from reframe.core.runtime import runtime
from . import writer

# `BackendBusy` is re-exported, as it is part of `store()`'s interface
from .backends import Backend, BackendBusy   # noqa: F401
from .flatten import flatten


def store(report, database=None, persistent=False, retry_options=None,
          **options):
    '''Flatten a run report and append it to the analytics database.

    By default, the database is attempted only once and a
    :class:`BackendBusy` is raised if another process holds it, leaving it to
    the caller to decide how to react. If ``persistent`` is set, the results
    are instead never lost: a busy database is retried and, once the retries
    are exhausted, the results are written to a Parquet file in the backend's
    fallback directory, to be imported at a later time.

    :arg report: the run report to store; it must follow the
        ``reframe/schemas/runreport.json`` schema of the current data version.
    :arg database: URI of the database to store the results in, as described
        in :func:`~reframe.analytics.backends.Backend.create`. If
        :obj:`None`, the :attr:`~config.analytics.database` configuration
        option is used.
    :arg persistent: retry a busy database and fall back to a Parquet file
        instead of raising :class:`BackendBusy`.
    :arg retry_options: options to pass to
        :func:`reframe.analytics.writer.append`, which control how the busy
        database is retried; meaningful only if ``persistent`` is set.
    :arg options: options to pass to the backend's constructor.
    :returns: the path of the Parquet file written if ``persistent`` is set
        and the retries were exhausted, or :obj:`None` if the results were
        stored in the database. The Parquet file is named after the session's
        UUID.
    :raises BackendBusy: if the database is locked by another process and
        ``persistent`` is not set.
    '''

    if database is None:
        database = runtime().get_option('analytics/0/database')

    backend = Backend.create(database, **options)
    table = flatten(report)
    if not persistent:
        backend.append(table)
        return None

    return writer.append(backend, table, backend.fallback_dir,
                         **(retry_options or {}))

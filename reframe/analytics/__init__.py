# Copyright 2016-2026 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

from .backends import Backend, BackendBusy   # noqa: F401
from .flatten import flatten


def store(report, backend='duckdb', **options):
    '''Flatten a run report and append it to the analytics database.

    :arg report: the report to store.
    :arg backend: the name of the analytics backend to use.
    :arg options: options to pass to the backend's constructor.
    '''

    Backend.create(backend, **options).append(flatten(report))

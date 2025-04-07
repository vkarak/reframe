# Copyright 2016-2024 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import reframe as rfm
import reframe.utility.sanity as sn


@rfm.simple_test
@rfm.xfail('bug 123', lambda t: t.known_failure > 1)
class HelloTest(rfm.RegressionTest, pin_prefix=True):
    descr = 'C Hello World test'

    # All available systems are supported
    valid_systems = ['*']
    valid_prog_environs = ['*']
    sourcepath = 'hello.c'
    tags = {'foo', 'bar'}
    maintainers = ['VK']
    known_failure = variable(int, value=0)

    @sanity_function
    def validate(self):
        if self.known_failure:
            return sn.assert_true(0)

        return sn.assert_found(r'Hello, World\!', self.stdout)


@rfm.simple_test
class CompileOnlyHelloTest(rfm.CompileOnlyRegressionTest):
    descr = 'Compile-only C Hello World test'

    # All available systems are supported
    valid_systems = ['*']
    valid_prog_environs = ['*']
    sourcepath = 'hello.c'


@rfm.simple_test
class SkipTest(rfm.RunOnlyRegressionTest):
    '''Test to be always skipped'''
    valid_systems = ['*']
    valid_prog_environs = ['*']
    sanity_patterns = sn.assert_true(1)

    @run_after('init')
    def foo(self):
        self.skip_if(True, 'unsupported')

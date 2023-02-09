# Copyright 2016-2023 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import re


def make_tc_highlighter(styles):
    regex = re.compile(
        r'^(\^?)(?P<name>\S+)(?P<params>(\s(%\S+))*)(?P<scope>(\s~\S+)*)'
        r'(?P<hash>\s/\S+)(?P<pe>\s@\S+)*'
    )

    def _highlight(text):
        m = regex.match(text)
        if not m:
            return text

        ret = ''
        for grp in ('name', 'params', 'scope', 'hash', 'pe'):
            style = styles.get(grp)
            segment = m.group(grp)
            if style and segment:
                ret += f'[{style}]{segment}[/{style}]'
            elif segment:
                ret += segment

        return ret

    return _highlight


def make_string_highlighter(style):
    regex = re.compile(r"('|\").*?('|\")")

    def _highlight(text):
        return regex.sub(f'[{style}]\g<0>[/{style}]', text)

    return _highlight


hT = make_tc_highlighter(styles={'params': 'yellow',
                                 'scope': 'magenta',
                                 'hash': 'cyan',
                                 'pe': 'blue'})

hS = make_string_highlighter('bold')

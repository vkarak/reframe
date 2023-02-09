# Copyright 2016-2022 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import datetime
from rich.console import Console
from rich.emoji import Emoji
from rich.highlighter import JSONHighlighter
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

import reframe.core.logging as logging
import reframe.utility.color as color
import reframe.utility.jsonext as jsonext


class PrettyPrinter:
    '''Pretty printing facility for the framework.

    It takes care of formatting the progress output and adds some more
    cosmetics to specific levels of messages, such as warnings and errors.

    The actual printing is delegated to an internal logger, which is
    responsible for printing.
    '''

    def __init__(self):
        self.colorize = True
        self.line_width = 78
        self.status_width = 10

    def reset_progress(self, total_cases):
        self._progress_count = 0
        self._progress_total = total_cases

    def separator(self, linestyle, msg=''):
        if linestyle == 'short double line':
            line = self.status_width * '='
        elif linestyle == 'short single line':
            line = self.status_width * '-'
        else:
            raise ValueError('unknown line style')

        self.info('[%s] %s' % (line, msg))

    def status(self, status, message='', just=None, level=logging.INFO):
        if just == 'center':
            status = status.center(self.status_width - 2)
        elif just == 'right':
            status = status.rjust(self.status_width - 2)
        else:
            status = status.ljust(self.status_width - 2)

        status_stripped = status.strip()
        if self.colorize:
            if status_stripped == 'SKIP':
                status = color.colorize(status, color.YELLOW)
            elif status_stripped in ['FAIL', 'FAILED', 'ERROR']:
                status = color.colorize(status, color.RED)
            else:
                status = color.colorize(status, color.GREEN)

        final_msg = f'[ {status} ] '
        if status_stripped in ['OK', 'SKIP', 'FAIL']:
            self._progress_count += 1
            width = len(str(self._progress_total))
            padded_progress = str(self._progress_count).rjust(width)
            final_msg += f'({padded_progress}/{self._progress_total}) '

        final_msg += message
        logging.getlogger().log(level, final_msg)

    def timestamp(self, msg='', separator=None):
        msg = '%s %s' % (msg, datetime.datetime.today().strftime('%c %Z'))
        if separator:
            self.separator(separator, msg)
        else:
            self.info(msg)

    def log_json(self, level, obj):
        console = Console(highlighter=JSONHighlighter())
        self.log(level, jsonext.dumps(obj, indent=2),
                 extra={'rich_console': console})

    def info_json(self, obj):
        return self.log_json(logging.INFO, obj)

    def info_session(self, obj):
        grid = Table.grid()
        grid.add_column(style='bold blue')
        grid.add_column(style='italic')
        grid.add_row('  Version: ', obj['version'])
        grid.add_row('  Command: ', obj['cmdline'])
        grid.add_row('  Launched by: ',
                     f"{obj['user'] or '<unknown>'}@{obj['hostname']}")
        grid.add_row('  Working directory: ', obj['workdir'])
        grid.add_row('  Configuration files: ', ', '.join(obj['config_files']))
        grid.add_row('  Check search path: ',
                     f"{'(R) ' if obj['check_search_recurse'] else ''}"
                     f"{':'.join(obj['check_search_path'])}")
        grid.add_row('  Stage directory: ', obj['prefix_stage'])
        grid.add_row('  Output directory: ', obj['prefix_output'])
        grid.add_row('  Log files: ', ', '.join(logging.log_files()))
        grid.add_row()
        self.info('', extra={'rich_console': Console(), 'rich_object': grid})

    def __getattr__(self, attr):
        # delegate all other attribute lookup to the underlying logger
        return getattr(logging.getlogger(), attr)

    def __setattr__(self, attr, value):
        # Delegate colorize setting to the backend logger
        if attr == 'colorize':
            logging.getlogger().colorize = value
            self.__dict__['colorize'] = value
        else:
            super().__setattr__(attr, value)

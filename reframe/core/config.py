# Copyright 2016-2020 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import copy
import itertools
import json
import jsonschema
import os
import re
import tempfile
import socket

import reframe
import reframe.core.debug as debug
import reframe.core.fields as fields
import reframe.core.settings as settings
import reframe.utility as util
import reframe.utility.os_ext as os_ext
import reframe.utility.typecheck as types
from reframe.core.exceptions import (ConfigError,
                                     ReframeDeprecationWarning,
                                     ReframeFatalError)
from reframe.core.logging import getlogger
from reframe.utility import ScopedDict


class _SiteConfig:
    def __init__(self, site_config, filename):
        self._site_config = copy.deepcopy(site_config)
        self._filename = filename
        self._local_config = {}
        self._local_system = None
        self._sticky_options = {}

        # Open and store the JSON schema for later validation
        schema_filename = os.path.join(reframe.INSTALL_PREFIX,
                                       'schemas', 'config.json')
        with open(schema_filename) as fp:
            try:
                self._schema = json.loads(fp.read())
            except json.JSONDecodeError as e:
                raise ReframeFatalError(
                    f"invalid configuration schema: '{schema_filename}'"
                ) from e

    def _pick_config(self):
        return self._local_config if self._local_config else self._site_config

    def __repr__(self):
        return f'{type(self).__name__}(site_config={self._site_config!r}, filename={self._filename!r})'

    def __str__(self):
        return json.dumps(self._pick_config(), indent=2)

    # Delegate everything to either the original config or to the reduced one
    # if a system is selected

    def __iter__(self):
        return iter(self._pick_config())

    def __getitem__(self, key):
        return self._pick_config()[key]

    def __getattr__(self, attr):
        return getattr(self._pick_config(), attr)

    def add_sticky_option(self, option, value):
        self._sticky_options[option] = value

    def remove_sticky_option(self, option):
        self._sticky_options.pop(option, None)

    def get(self, option, default=None):
        '''Retrieve value of option.

        If the option cannot be retrieved, ``default`` will be returned.
        '''

        # Convert any indices to integers
        prepared_option = []
        for opt in option.split('/'):
            try:
                opt = int(opt)
            except ValueError:
                pass

            prepared_option.append(opt)

        stripped_opt = '/'.join(x for x in prepared_option
                                if not isinstance(x, int) and x[0] != '@')
        try:
            # If a sticky option exists, return that value
            return self._sticky_options[stripped_opt]
        except KeyError:
            pass

        value = self._pick_config()
        for d in prepared_option:
            if not isinstance(d, int) and d[0] == '@':
                # We are in an element addressable by name
                d = d[1:]
                found = False
                for obj in value:
                    if obj['name'] == d:
                        value, found = obj, True
                        break

                if not found:
                    return default
                else:
                    continue

            try:
                value = value[d]
            except (TypeError, IndexError):
                return default
            except KeyError:
                try:
                    return self._schema['defaults'][stripped_opt]
                except KeyError:
                    return default

        return value

    @property
    def filename(self):
        return self._filename

    @property
    def subconfig_system(self):
        return self._local_system

    @classmethod
    def create(cls, filename):
        _, ext = os.path.splitext(filename)
        if ext == '.py':
            return cls._create_from_python(filename)
        elif ext == '.json':
            return cls._create_from_json(filename)
        else:
            raise ConfigError(f"unknown configuration file type: '{filename}'")

    @classmethod
    def _create_from_python(cls, filename):
        try:
            mod = util.import_module_from_file(filename)
        except ImportError as e:
            # import_module_from_file() may raise an ImportError if the
            # configuration file is under ReFrame's top-level directory
            raise ConfigError(
                f"could not load Python configuration file: '{filename}'"
            ) from e

        if hasattr(mod, 'settings'):
            # Looks like an old style config
            raise ReframeDeprecationWarning(
                f"the syntax of the configuration file '{filename}' "
                f"is deprecated"
            )

        mod = util.import_module_from_file(filename)
        if not hasattr(mod, 'site_configuration'):
            raise ConfigError(
                f"not a valid Python configuration file: '{filename}'"
            )

        return _SiteConfig(mod.site_configuration, filename)

    @classmethod
    def _create_from_json(cls, filename):
        with open(filename) as fp:
            try:
                config = json.loads(fp.read())
            except json.JSONDecodeError as e:
                raise ConfigError(
                    f"invalid JSON syntax in configuration file '{filename}'"
                ) from e

        return _SiteConfig(config, filename)

    def _detect_system(self):
        if os.path.exists('/etc/xthostname'):
            # Get the cluster name on Cray systems
            with open('/etc/xthostname') as fp:
                hostname = fp.read()
        else:
            hostname = socket.gethostname()

        for system in self._site_config['systems']:
            for patt in system['hostnames']:
                if re.match(patt, hostname):
                    return system['name']

        raise ConfigError(f"could not find a configuration entry "
                          f"for the current system: '{hostname}'")

    def validate(self):
        site_config = self._pick_config()
        try:
            jsonschema.validate(site_config, self._schema)
        except jsonschema.ValidationError as e:
            raise ConfigError(f"could not validate configuration file: "
                              f"'{self._filename}'") from e

        # Make sure that system and partition names are unique
        system_names = set()
        for system in self._site_config['systems']:
            sysname = system['name']
            if sysname in system_names:
                raise ConfigError(f"system '{sysname}' already defined")

            system_names.add(sysname)
            partition_names = set()
            for part in system['partitions']:
                partname = part['name']
                if partname in partition_names:
                    raise ConfigError(
                        f"partition '{partname}' already defined "
                        f"for system '{sysname}'"
                    )

                partition_names.add(partname)

    def select_subconfig(self, system_fullname=None):
        if (self._local_system is not None and
            self._local_system == system_fullname):
            return

        system_fullname = system_fullname or self._detect_system()
        try:
            system_name, part_name = system_fullname.split(':', maxsplit=1)
        except ValueError:
            # system_name does not have a partition
            system_name, part_name = system_fullname, None

        # Start from a fresh copy of the site_config, because we will be
        # modifying it
        site_config = copy.deepcopy(self._site_config)
        self._local_config = {}
        systems = list(
            filter(lambda x: x['name'] == system_name, site_config['systems'])
        )
        if not systems:
            raise ConfigError(
                f"could not find a configuration entry "
                f"for the requested system: '{system_name}'"
            )

        if part_name is not None:
            # Filter out also partitions
            systems[0]['partitions'] = list(
                filter(lambda x: x['name'] == part_name,
                       systems[0]['partitions'])
            )

        if not systems[0]['partitions']:
            raise ConfigError(
                f"could not find a configuration entry "
                f"for the requested system/partition combination: "
                f"'{system_name}:{part_name}'"
            )

        # Create local configuration for the current or the requested system
        self._local_config['systems'] = systems
        for name, section in site_config.items():
            if name == 'systems':
                # The systems sections has already been treated
                continue

            # Convert section to a scoped dict that will handle correctly and
            # transparently the system/partition resolution
            scoped_section = ScopedDict()
            for obj in section:
                key = obj.get('name', name)
                target_systems = obj.get(
                    'target_systems',
                    self._schema['defaults'][f'{name}/target_systems']
                )
                for t in target_systems:
                    scoped_section[f'{t}:{key}'] = obj

            unique_keys = set()
            for obj in section:
                key = obj.get('name', name)
                if key in unique_keys:
                    continue

                unique_keys.add(key)
                try:
                    val = scoped_section[f"{system_fullname}:{key}"]
                except KeyError:
                    pass
                else:
                    self._local_config.setdefault(name, [])
                    self._local_config[name].append(val)

        required_sections = self._schema['required']
        for name in required_sections:
            if name not in self._local_config.keys():
                raise ConfigError(f"section '{name}' not defined "
                                  f"for system '{system_fullname}'")

        # Verify that all environments defined by the system are defined for
        # the current system
        sys_environs = {
            *itertools.chain(*(p['environs']
                               for p in systems[0]['partitions']))
        }
        found_environs = {
            e['name'] for e in self._local_config['environments']
        }
        undefined_environs = sys_environs - found_environs
        if undefined_environs:
            env_descr = ', '.join(f"'{e}'" for e in undefined_environs)
            raise ConfigError(
                f"environments {env_descr} "
                f"are not defined for '{system_fullname}'"
            )

        self._local_system = system_fullname


def convert_old_config(filename):
    old_config = util.import_module_from_file(filename).settings
    converted = {
        'systems': [],
        'environments': [],
        'logging': [],
        'perf_logging': [],
    }
    old_systems = old_config.site_configuration['systems'].items()
    for sys_name, sys_spec in old_systems:
        sys_dict = {'name': sys_name}
        sys_dict.update(sys_spec)

        # hostnames is now a required property
        if 'hostnames' not in sys_spec:
            sys_dict['hostnames'] = []

        # Make variables dictionary into a list of lists
        if 'variables' in sys_spec:
            sys_dict['variables'] = [
                [vname, v] for vname, v in sys_dict['variables'].items()
            ]

        # Make partitions dictionary into a list
        if 'partitions' in sys_spec:
            sys_dict['partitions'] = []
            for pname, p in sys_spec['partitions'].items():
                new_p = {'name': pname}
                new_p.update(p)
                if p['scheduler'] == 'nativeslurm':
                    new_p['scheduler'] = 'slurm'
                    new_p['launcher'] = 'srun'
                elif p['scheduler'] == 'local':
                    new_p['scheduler'] = 'local'
                    new_p['launcher'] = 'local'
                else:
                    sched, launch, *_ = p['scheduler'].split('+')
                    new_p['scheduler'] = sched
                    new_p['launcher'] = launch

                # Make resources dictionary into a list
                if 'resources' in p:
                    new_p['resources'] = [
                        {'name': rname, 'options': r}
                        for rname, r in p['resources'].items()
                    ]

                # Make variables dictionary into a list of lists
                if 'variables' in p:
                    new_p['variables'] = [
                        [vname, v] for vname, v in p['variables'].items()
                    ]

                if 'container_platforms' in p:
                    new_p['container_platforms'] = []
                    for cname, c in p['container_platforms'].items():
                        new_c = {'name': cname}
                        new_c.update(c)
                        if 'variables' in c:
                            new_c['variables'] = [
                                [vn, v] for vn, v in c['variables'].items()
                            ]

                        new_p['container_platforms'].append(new_c)

                sys_dict['partitions'].append(new_p)

        converted['systems'].append(sys_dict)

    old_environs = old_config.site_configuration['environments'].items()
    for env_target, env_entries in old_environs:
        for ename, e in env_entries.items():
            new_env = {'name': ename}
            if env_target != '*':
                new_env['target_systems'] = [env_target]

            new_env.update(e)

            # Convert variables dictionary to a list of lists
            if 'variables' in e:
                new_env['variables'] = [
                    [vname, v] for vname, v in e['variables'].items()
                ]

            # Type attribute is not used anymore
            if 'type' in new_env:
                del new_env['type']

            converted['environments'].append(new_env)

    if 'modes' in old_config.site_configuration:
        converted['modes'] = []
        old_modes = old_config.site_configuration['modes'].items()
        for target_mode, mode_entries in old_modes:
            for mname, m in mode_entries.items():
                new_mode = {'name': mname, 'options': m}
                if target_mode != '*':
                    new_mode['target_systems'] = [target_mode]

                converted['modes'].append(new_mode)

    def update_logging_config(log_name, original_log):
        new_handlers = []
        for h in original_log['handlers']:
            new_h = h
            new_h['level'] = h['level'].lower()
            new_handlers.append(new_h)

        converted[log_name].append(
            {
                'level': original_log['level'].lower(),
                'handlers': new_handlers
            }
        )

    update_logging_config('logging', old_config.logging_config)
    update_logging_config('perf_logging', old_config.perf_logging_config)
    converted['general'] = [{}]
    if hasattr(old_config, 'checks_path'):
        converted['general'][0][
            'check_search_path'
        ] = old_config.checks_path

    if hasattr(old_config, 'checks_path_recurse'):
        converted['general'][0][
            'check_search_recursive'
        ] = old_config.checks_path_recurse

    if converted['general'] == [{}]:
        del converted['general']

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py',
                                     delete=False) as fp:
        fp.write(f"#\n# This file was automatically generated "
                 f"by ReFrame based on '{filename}'.\n#\n\n")
        fp.write(f'site_configuration = {util.ppretty(converted)}\n')

    return fp.name


def _find_config_file():
    # The order of elements is important, since it defines the priority
    prefixes = [
        os.path.join(os.getlogin(), '.reframe'),
        reframe.INSTALL_PREFIX,
        '/etc/reframe.d'
    ]
    valid_exts = ['py', 'json']
    for d in prefixes:
        for ext in valid_exts:
            filename = os.path.join(d, f'settings.{ext}')
            if os.path.exists(filename):
                return filename

    return None


def load_config(filename=None):
    if filename is None:
        filename = _find_config_file()
        if filename is None:
            # Return the generic configuration
            getlogger().debug('no configuration found; '
                              'falling back to a generic one')
            return _SiteConfig(settings.site_configuration, '<builtin>')

    return _SiteConfig.create(filename)

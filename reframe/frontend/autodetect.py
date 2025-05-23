# Copyright 2016-2024 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import json
import jsonschema
import os
import shutil
import tempfile
import traceback

import reframe as rfm
import reframe.core.runtime as runtime
import reframe.utility.osext as osext
from reframe.core.exceptions import ConfigError
from reframe.core.logging import getlogger
from reframe.core.schedulers import Job
from reframe.core.systems import DeviceInfo, ProcessorInfo
from reframe.utility.cpuinfo import cpuinfo


# This is meant to be used by the unit tests
_TREAT_WARNINGS_AS_ERRORS = False


def _contents(filename):
    '''Return the contents of a file.'''

    with open(filename) as fp:
        return fp.read()


def _log_contents(filename):
    filename = os.path.abspath(filename)
    getlogger().debug(f'--- {filename} ---\n'
                      f'{_contents(filename)}\n'
                      f'--- {filename} ---')


class _prepare_reframe:
    def __init__(self):
        self._prefix = runtime.runtime().get_option('general/0/remote_workdir')
        self._workdir = None

    def __enter__(self):
        self._workdir = os.path.abspath(
            tempfile.mkdtemp(prefix='rfm.', dir=self._prefix)
        )
        paths = ['bin/', 'reframe/', 'tools/',
                 'bootstrap.sh', 'requirements.txt']
        use_pip = False
        custom_command = runtime.runtime().get_option(
            'general/0/remote_install'
        )
        if not custom_command:
            try:
                for p in paths:
                    src = os.path.join(rfm.INSTALL_PREFIX, p)
                    if os.path.isdir(src):
                        dst = os.path.join(self._workdir, p)
                        osext.copytree(src, dst, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src, self._workdir)
            except FileNotFoundError:
                use_pip = True
            except Exception as err:
                osext.rmtree(self._workdir)
                raise err

        return self._workdir, use_pip, custom_command

    def __exit__(self, exc_type, exc_val, exc_tb):
        osext.rmtree(self._workdir)


def _subschema(fragment):
    '''Create a configuration subschema.'''

    full_schema = runtime.runtime().site_config.schema
    return {
        '$schema': full_schema['$schema'],
        'defs': full_schema['defs'],
        '$ref': fragment
    }


def _validate_info(info, schema):
    if schema is None:
        return info

    jsonschema.validate(info, schema)
    return info


def _load_info(filename, schema=None):
    try:
        with open(filename) as fp:
            return _validate_info(json.load(fp), schema)
    except OSError as e:
        if _TREAT_WARNINGS_AS_ERRORS:
            raise

        getlogger().warning(
            f'could not load file: {filename!r}: {e}'
        )
        return {}
    except jsonschema.ValidationError as e:
        getlogger().debug(str(e))
        raise ConfigError(
            f'could not validate meta-config file {filename!r}'
        ) from e


def _save_info(filename, topo_info):
    if not topo_info:
        return

    os.makedirs(os.path.dirname(filename), exist_ok=True)
    try:
        with open(filename, 'w') as fp:
            json.dump(topo_info, fp, indent=2)
    except OSError as e:
        if _TREAT_WARNINGS_AS_ERRORS:
            raise

        getlogger().warning(
            f'could not save topology file: {filename!r}: {e}'
        )
    else:
        getlogger().debug(f'> saved topology in {filename!r}')


def _is_part_local(part):
    return (part.scheduler.registered_name == 'local' and
            part.launcher_type.registered_name == 'local')


def _remote_detect(part, cli_job_options):
    use_login_shell = runtime.runtime().get_option('general/0/use_login_shell')

    def _emit_script_for_source(job, env):
        commands = [
            './bootstrap.sh',
            './bin/reframe --detect-host-topology=topo.json'
        ]
        job.prepare(commands, env, trap_errors=True, login=use_login_shell)

    def _emit_script_for_pip(job, env):
        commands = [
            'python3 -m venv venv.reframe',
            'source venv.reframe/bin/activate',
            'pip install --upgrade pip',
            f'pip install reframe-hpc=={rfm.VERSION}',
            'reframe --detect-host-topology=topo.json',
            'deactivate'
        ]
        job.prepare(commands, env, trap_errors=True, login=use_login_shell)

    def _emit_custom_script(job, env, commands):
        commands.append('reframe --detect-host-topology=topo.json')
        job.prepare(commands, env, trap_errors=True, login=use_login_shell)

    getlogger().info(
        f'Detecting topology of remote partition {part.fullname!r}: '
        f'this may take some time...'
    )
    topo_info = {}
    try:
        with _prepare_reframe() as (dirname, use_pip, custom_command):
            with osext.change_dir(dirname):
                job = Job.create(part.scheduler,
                                 part.launcher_type(),
                                 name='rfm-detect-job',
                                 sched_access=part.access,
                                 sched_options=cli_job_options)
                custom_command = runtime.runtime().get_option(
                    'general/0/remote_install'
                )
                if custom_command:
                    _emit_custom_script(job, [part.local_env], custom_command)
                elif use_pip:
                    _emit_script_for_pip(job, [part.local_env])
                else:
                    _emit_script_for_source(job, [part.local_env])

                getlogger().debug('submitting detection script')
                _log_contents(job.script_filename)
                job.submit()
                job.wait()
                getlogger().debug('job finished')
                _log_contents(job.stdout)
                _log_contents(job.stderr)
                topo_info = json.loads(_contents('topo.json'))
    except Exception as e:
        if _TREAT_WARNINGS_AS_ERRORS:
            raise

        getlogger().warning(f'failed to retrieve remote processor info: {e}')
        getlogger().debug(traceback.format_exc())

    return topo_info


def detect_topology(cli_job_options=None):
    cli_job_options = [] if cli_job_options is None else cli_job_options
    rt = runtime.runtime()
    detect_remote_systems = rt.get_option('general/0/remote_detect')
    topo_prefix = osext.expandvars(rt.get_option('general/0/topology_prefix'))
    for part in rt.system.partitions:
        getlogger().debug(f'detecting topology info for {part.fullname}')
        found_procinfo = False
        found_devinfo  = False
        if part.processor.info != {}:
            # Processor info set up already in the configuration
            getlogger().debug(
                '> topology found in configuration file; skipping...'
            )
            found_procinfo = True

        if part.devices:
            # Devices set up already in the configuration
            getlogger().debug(
                '> devices found in configuration file; skipping...'
            )
            found_devinfo = True

        if found_procinfo and found_devinfo:
            continue

        topo_file = os.path.join(
            topo_prefix, f'{rt.system.name}-{part.name}', 'processor.json'
        )
        dev_file = os.path.join(
            topo_prefix, f'{rt.system.name}-{part.name}', 'devices.json'
        )
        if not found_procinfo and os.path.exists(topo_file):
            getlogger().debug(
                f'> found topology file {topo_file!r}; loading...'
            )
            try:
                part._processor = ProcessorInfo(
                    _load_info(topo_file, _subschema('#/defs/processor_info'))
                )
                found_procinfo = True
            except json.decoder.JSONDecodeError as e:
                getlogger().debug(
                    f'> could not load {topo_file!r}: {e}: ignoring...'
                )

        if not found_devinfo and os.path.exists(dev_file):
            getlogger().debug(
                f'> found devices file {dev_file!r}; loading...'
            )
            try:
                devices_info = _load_info(
                    dev_file, _subschema('#/defs/devices')
                )
                part._devices = [DeviceInfo(d) for d in devices_info]
                found_devinfo = True
            except json.decoder.JSONDecodeError as e:
                getlogger().debug(
                    f'> could not load {dev_file!r}: {e}: ignoring...'
                )

        if found_procinfo and found_devinfo:
            continue

        if not found_procinfo:
            # No topology found, try to auto-detect it
            getlogger().debug('> no topology file found; auto-detecting...')
            modules = list(rt.system.preload_environ.modules)
            vars = dict(rt.system.preload_environ.env_vars.items())
            if _is_part_local(part):
                modules += part.local_env.modules
                vars.update(part.local_env.env_vars)

                # Unconditionally detect the system for fully local partitions
                with runtime.temp_environment(modules=modules, env_vars=vars):
                    part._processor = ProcessorInfo(cpuinfo())

                _save_info(topo_file, part.processor.info)
            elif detect_remote_systems:
                with runtime.temp_environment(modules=modules, env_vars=vars):
                    part._processor = ProcessorInfo(
                        _remote_detect(part, cli_job_options)
                    )

                if part.processor.info:
                    _save_info(topo_file, part.processor.info)

        if not found_devinfo:
            getlogger().debug('> device auto-detection is not supported')

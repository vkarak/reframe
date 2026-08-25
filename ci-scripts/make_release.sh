#!/bin/bash

oldpwd=$(pwd)

usage()
{
    echo "Usage: $0 VERSION"
    echo "  Environment:"
    echo "    - GITHUB_CREDENTIALS=<user>:<token>"
}

_onerror()
{
    exitcode=$?
    echo "$0: ReFrame release failed!"
    echo "$0: command \`$BASH_COMMAND' failed (exit code: $exitcode)"
    cd $oldpwd
    exit $exitcode
}

trap _onerror ERR

version=$1
if [ -z $version ]; then
    echo "$0: missing version number" >&2
    usage
    exit 1
fi

if [ ! uv --version >/dev/null 2>&1 ]; then
    echo "$0: uv is not installed" >&2
    exit 1
fi

if [ -z "$GITHUB_CREDENTIALS" ]; then
    _gh_creds_prefix=""
else
    _gh_creds_prefix="${GITHUB_CREDENTIALS}@"
fi


tmpdir=$(mktemp -d)
echo "Releasing ReFrame version $version ..."
echo "Working directory: $tmpdir ..."
cd $tmpdir
git clone --branch master https://${_gh_creds_prefix}github.com/reframe-hpc/reframe.git
cd reframe
found_version=$(uv run reframe -V | sed -e 's/\(.*\)\+.*/\1/g')
if [ $found_version != $version ]; then
    echo "$0: version mismatch: found $found_version, but required $version" >&2
    exit 1
fi

uv run ./test_reframe.py
git tag -a v$version -m "ReFrame $version"
git push origin --tags

echo "Pushing of tags was successful!"

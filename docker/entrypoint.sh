#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
    set -- --version
fi
exec /opt/chromix/chromix "$@"

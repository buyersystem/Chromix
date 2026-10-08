#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
    set -- --version
fi
case "$1" in
    serve|cdp)
        shift
        exec python3 /usr/local/lib/chromix/server.py serve "$@"
        ;;
esac
exec /opt/chromix/chromix "$@"

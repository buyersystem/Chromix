#!/bin/sh
# Verify both the release checksum manifest and the pinned asset digest.
set -eu
arch="${1:?usage: download.sh amd64|arm64 destination}"
destination="${2:?missing destination}"
case "$arch" in
    amd64)
        asset=chromix-linux-x64.zip
        manifest=SHA256SUMS
        digest=9b769a5b151b0778a42e6883dd12454817fcd0bef0268b1a93008b25052e0669
        ;;
    arm64)
        asset=chromix-linux-arm64.zip
        manifest=SHA256SUMS-linux-arm64
        digest=26be9806543e2957ed82469830c17d4b38bc018b5c85dcaefd434fa2e50c2a60
        ;;
    *) echo "Unsupported architecture: $arch" >&2; exit 2 ;;
esac
base=https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.57
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT HUP INT TERM
curl --fail --location --retry 5 --connect-timeout 30 --max-time 1800 \
    "$base/$asset" -o "$work/$asset"
curl --fail --location --retry 5 --connect-timeout 30 --max-time 120 \
    "$base/$manifest" -o "$work/$manifest"
expected="$(awk -v asset="$asset" '$2 == asset {print $1}' "$work/$manifest")"
if [ "$expected" != "$digest" ]; then
    echo "Release checksum manifest does not match pinned digest for $asset" >&2
    exit 1
fi
(cd "$work" && printf '%s  %s\n' "$digest" "$asset" | sha256sum --check --strict -)
mkdir -p "$destination"
unzip -q "$work/$asset" -d "$destination"
for name in chromix chrome chrome-sandbox chrome_crashpad_handler; do
    test -f "$destination/chromix/$name"
done

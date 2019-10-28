#!/bin/sh

set -eu

cd "$(dirname "$(readlink -f "$0")")"

# full zip with everything

TDIR=$(mktemp -d) || exit 1

find * -not -path '*/.*' -type f | while read f; do
    if ! git check-ignore -q "$f"; then
        d="$(dirname "$f")"
        mkdir -p "$TDIR/$d"
        cp -a "$f" "$TDIR/$d"
    fi
done


zipname=infgen-$(date +%Y%m%d).zip

# the cat is necessary here because zip will write to the file directly
# if it detects that stdout is redirected to a file.
(cd "$TDIR"; zip -r - *) | cat > "$zipname"

rm -rf "$TDIR"

# executable zip containing just required components
TDIR=$(mktemp -d) || exit 1

cp -a makeinf.py "$TDIR/__main__.py"
find res -type f | while read f; do
    if ! git check-ignore -q "$f"; then
        d="$(dirname "$f")"
        mkdir -p "$TDIR/$d"
        cp -a "$f" "$TDIR/$d"
    fi
done


zipname=infgen-$(date +%Y%m%d).pyz

# the cat is necessary here because zip will write to the file directly
# if it detects that stdout is redirected to a file.
(printf '#!/usr/bin/env python3\n'; cd "$TDIR"; zip -r - *) | cat > "$zipname"
chmod +x "$zipname"

rm -rf "$TDIR"

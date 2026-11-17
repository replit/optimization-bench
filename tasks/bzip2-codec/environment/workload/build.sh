#!/bin/sh
set -eu
OUT=${1:-/app/build}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
SOURCE=${BZIP2_SOURCE:-/app/bzip2}
cp -a "$SOURCE" "$OUT/source"
cd "$OUT/source"
cc -O3 -DNDEBUG -D_FILE_OFFSET_BITS=64 -Wall -Wextra -I. blocksort.c huffman.c crctable.c randtable.c compress.c decompress.c bzlib.c /app/workload/worker.c -static -o "$OUT/codec"
make -j8 bzip2


cat > "$OUT/run" <<EOF
#!/bin/sh
set -eu
exec python3 /app/workload/runner.py --build "$OUT" "\$@"
EOF
chmod 755 "$OUT/run"

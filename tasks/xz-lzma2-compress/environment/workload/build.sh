#!/bin/sh
set -eu
OUT=${1:-/app/build}
SOURCE=${OPTIMBENCH_SOURCE:-/app/xz}
TASK=/app/workload
test ! -e "$OUT" || { echo "Use a fresh build directory" >&2; exit 1; }
mkdir -p "$OUT"
cp -a "$SOURCE" "$OUT/project"
cmake -S "$OUT/project" -B "$OUT/cmake" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS_RELEASE='-O2 -g -DNDEBUG' \
  -DBUILD_TESTING=ON
cmake --build "$OUT/cmake" -j8
cc -O2 -g -fno-omit-frame-pointer -I"$OUT/project/src/liblzma/api" \
  /app/workload/native/driver.c "$OUT/cmake/liblzma.a" -lpthread \
  -o "$OUT/xz-driver"
cc -O2 -g -fno-omit-frame-pointer -I"$OUT/project/src/liblzma/api" \
  /app/workload/native/decode.c "$OUT/cmake/liblzma.a" -lpthread \
  -o "$OUT/xz-decode"

cat > "$OUT/run" <<'SH'
#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 /app/workload/runner.py --task /app/workload/task.py --build "$HERE" --inputs /app/workload/inputs "$@"
SH
chmod +x "$OUT/run"

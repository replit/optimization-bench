#!/bin/sh
set -eu
OUT=${1:-/app/build}
SOURCE=${OPTIMBENCH_SOURCE:-/app/jq}
test ! -e "$OUT" || { echo "Use a fresh build directory" >&2; exit 1; }
printf '%s  %s\n' '97977a8e8672e2d8c04b02b73aec0f39e390ec09ffc6e16ad32651c8c8b90598' "$SOURCE/src/jq_test.c" | sha256sum -c - >&2
mkdir -p "$OUT"
cp -a "$SOURCE" "$OUT/project"
cd "$OUT/project"
autoreconf -fi
./configure --disable-docs --with-oniguruma=builtin --enable-static \
  CFLAGS='-O2 -g -pthread -fstack-protector-all'
make -j8
test -x "$OUT/project/.libs/jq"
cc -O2 -g -pthread -fstack-protector-all -I"$OUT/project/src" \
  /app/workload/native/driver.c "$OUT/project/.libs/libjq.a" \
  "$OUT/project/vendor/oniguruma/src/.libs/libonig.a" \
  -lcrypto -lm -o "$OUT/driver"

cat > "$OUT/run" <<'SH'
#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 /app/workload/runner.py --task /app/workload/task.py --build "$HERE" --inputs /app/workload/inputs "$@"
SH
chmod +x "$OUT/run"

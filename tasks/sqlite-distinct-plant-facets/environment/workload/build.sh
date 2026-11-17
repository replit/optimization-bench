#!/bin/sh
set -eu
OUT=${1:-/app/build}
SOURCE=${SQLITE_SOURCE:-/app/sqlite}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
cp -a "$SOURCE" "$OUT/source"
mkdir "$OUT/objects"
cd "$OUT/objects"
"$OUT/source/configure" --disable-shared --enable-static --enable-fts5 --enable-rtree
make -j8 libsqlite3.a sqlite3 testfixture
cc -O2 -DNDEBUG -I. /app/workload/driver/facets.c libsqlite3.a -lm -ldl -lpthread -o "$OUT/facets"
cc -O2 -g -DNDEBUG -I. /app/workload/driver/search.c libsqlite3.a -lm -ldl -lpthread -o "$OUT/search"
cc -O2 -g -DNDEBUG -I. /app/workload/driver/bulk.c libsqlite3.a -lm -ldl -lpthread -o "$OUT/bulk"
D=/app/workload/driver
for name in ichk cidx usgs storms rtree gtfs ghcn; do
  cc -O2 -DNDEBUG -I. -I"$D" "$D/$name.c" libsqlite3.a -lm -ldl -lpthread -o "$OUT/$name"
done
for name in pct fkc; do
  cc -O2 -DNDEBUG -DSQLITE_CORE -I. -I"$D" "$D/$name.c" libsqlite3.a -lm -ldl -lpthread -o "$OUT/$name"
done
cc -O2 -DNDEBUG -DSQLITE_CORE -I. -I"$D" "$D/dec.c" "$OUT/source/ext/misc/decimal.c" libsqlite3.a -lm -ldl -lpthread -o "$OUT/dec"
cat > "$OUT/run" <<EOF
#!/bin/sh
set -eu
exec python3 /app/workload/runner.py --build "$OUT" "\$@"
EOF
chmod 755 "$OUT/run"

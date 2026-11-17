/* w2-percentile driver (SQLite agent 3). Given per-site daily environmental observations, an analyst computes
 * per-site, per-year distribution statistics with SQLite's built-in percentile family (func.c,
 * SQLITE_ENABLE_PERCENTILE is on by default with --enable-math): median(), percentile(Y,P), percentile_cont(Y,F),
 * percentile_disc(Y,F). One request = one site. Fresh prepare/bind/step/finalize per request; every typed value is
 * buffered. Connection and request staging precede timing (see w2.h). */
#include "w2.h"
static const char *SQL =
  "SELECT year, count(*), min(value), max(value), median(value), percentile(value, 98), "
  "percentile_cont(value, 0.10), percentile_disc(value, 0.90), percentile(value, 2) "
  "FROM obs WHERE site = ?1 GROUP BY year ORDER BY year";
int main(int argc, char **argv) {
  unsigned long n; int mode; Request *r = load_requests(argc, argv, 1, &n, &mode);
  sqlite3 *db = open_db(argv[1]);
  Buffer out = {0}; Region g;
  region_begin(&g, mode);
  for (unsigned long i = 0; i < n; i++) {
    sqlite3_stmt *s = prepare(db, SQL);
    bind_text(s, "?1", r[i].f[0]);
    bytes(&out, r[i].f[0], strlen(r[i].f[0]));
    drain(db, s, &out);
    finish(s);
  }
  if (sqlite3_close(db) != SQLITE_OK) fail("close");
  region_end(&g);
  export_buffer(argv[3], &out); free_requests(r, n);
  return 0;
}

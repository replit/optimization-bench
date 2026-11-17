/* w2-decimal-ledger driver (SQLite agent 3). Exact money totals with SQLite's decimal extension: the driver links
 * ext/misc/decimal.c from the (editable) source tree, compiled with -DSQLITE_CORE as its own translation unit (see
 * drivers/dec.srcs), and registers it with sqlite3_decimal_init(), as the sqlite3 CLI does (src/shell.c.in INCLUDEs
 * ../ext/misc/decimal.c and calls sqlite3_decimal_init(p->db, 0, 0)). Given a ledger of
 * published money amounts kept as exact decimal text, one request = one reporting entity: per period and category it
 * returns the row count and decimal_sum(amount) (exact, arbitrary precision), plus the entity grand total via
 * decimal_add over the per-category sums. Fresh prepare/bind/step/finalize per request. */
#include "w2.h"
int sqlite3_decimal_init(sqlite3 *db, char **pzErrMsg, const sqlite3_api_routines *pApi);
static const char *SQL =
  "SELECT period, category, count(*), decimal_sum(amount), decimal_sum(decimal_mul(amount, amount)) "
  "FROM ledger WHERE grp = ?1 GROUP BY period, category ORDER BY period, category";
int main(int argc, char **argv) {
  unsigned long n; int mode; Request *r = load_requests(argc, argv, 1, &n, &mode);
  for (unsigned long i = 0; i < n; i++) unescape(r[i].f[0]);
  sqlite3 *db = open_db(argv[1]);
  if (sqlite3_decimal_init(db, 0, 0) != SQLITE_OK) fail("decimal init");
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

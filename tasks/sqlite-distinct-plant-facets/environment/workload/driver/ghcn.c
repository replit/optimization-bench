/* sqlite-ghcn-window-series: per-station climate series analytics with window functions
 * (sliding 30-day avg/max, year-to-date running sum, per-year rank, ntile deciles, lag deltas). */
#include "qb.h"
static const char *SQL =
  "SELECT date, tmax, tmin, prcp, avg30, max30, ytd_prcp, yrank, decile, dtmax, cume FROM ("
  "  SELECT date, tmax, tmin, prcp,"
  "         avg(tmax) OVER w30 AS avg30,"
  "         max(tmax) OVER w30 AS max30,"
  "         sum(prcp) OVER (PARTITION BY substr(date, 1, 4) ORDER BY date ROWS UNBOUNDED PRECEDING) AS ytd_prcp,"
  "         rank() OVER (PARTITION BY substr(date, 1, 4) ORDER BY tmax DESC) AS yrank,"
  "         ntile(10) OVER (ORDER BY tmin) AS decile,"
  "         tmax - lag(tmax) OVER (ORDER BY date) AS dtmax,"
  "         cume_dist() OVER (PARTITION BY substr(date, 6, 2) ORDER BY tmax) AS cume"
  "    FROM daily WHERE station = :station"
  "  WINDOW w30 AS (ORDER BY date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW))"
  " WHERE yrank <= 3 OR substr(date, 6, 5) = '12-31'"
  " ORDER BY date";
int main(int argc, char **argv) {
  unsigned long count; int mode; Region g;
  Request *rq = load_requests(argc, argv, 1, &count, &mode);
  sqlite3 *db = open_db(argv[1]);
  Buffer b = {0};
  region_begin(&g, mode);
  append(&b, "SQLGHCN1", 8); u64(&b, count);
  sqlite3_stmt *s = prepare(db, SQL);
  for (unsigned long i = 0; i < count; i++) {
    bytes(&b, rq[i].f[0], strlen(rq[i].f[0]));
    bind_text(s, ":station", rq[i].f[0]);
    drain(db, s, &b); reuse(s);
  }
  finish(s);
  if (sqlite3_close(db) != SQLITE_OK) fail("database close");
  region_end(&g);
  free_requests(rq, count);
  export_buffer(argv[3], &b);
  return 0;
}

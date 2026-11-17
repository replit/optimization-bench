/* sqlite-storms-printf-report: per (state, year) fixed-width report lines and CSV-style export built in SQL with
 * printf()/format() (full-narrative %Q quoting and case folding as in an SQL/CSV export; bounded output) plus upper/lower/replace/trim/substr/instr/rtrim string functions (Fossil whatis / sqlite3 shell style). */
#include "qb.h"
static const char *Q1 =
  "SELECT printf('%8d | %-24.24s | %-18.18s | %s %-6s | %3d/%-3d | %10.2f %-3s | %9.4f,%10.4f | %.40s',"
  "              event_id, upper(cz_name), event_type, begin_date_time, cz_timezone, injuries_direct, deaths_direct,"
  "              coalesce(magnitude, 0.0), coalesce(magnitude_type, '-'), coalesce(begin_lat, 0.0), coalesce(begin_lon, 0.0),"
  "              coalesce(begin_location, ''))"
  "  FROM storms WHERE state = :state AND year = :year ORDER BY event_id";
static const char *Q2 =
  "SELECT format('%d,%Q,%Q,%d', event_id, replace(trim(lower(cz_name)), ' ', '_'),"
  "              substr(coalesce(event_narrative, ''), 1, 200), length(event_narrative)),"
  "       length(format('%Q,%Q', episode_narrative, event_narrative)), length(upper(episode_narrative)),"
  "       instr(replace(lower(coalesce(event_narrative, '')), ',', ' '), ' tree'), upper(substr(coalesce(source, ''), 1, 12))"
  "  FROM storms WHERE state = :state AND year = :year ORDER BY event_id";
static const char *Q3 =
  "SELECT event_type, count(*),"
  "       printf('%.3e', total(CASE substr(damage_property, -1) WHEN 'K' THEN 1e3 WHEN 'M' THEN 1e6 WHEN 'B' THEN 1e9 ELSE 1 END"
  "                         * CAST(rtrim(damage_property, 'KMB') AS REAL))),"
  "       printf('%,d', sum(injuries_direct + injuries_indirect)), group_concat(DISTINCT upper(substr(cz_name, 1, 3)))"
  "  FROM storms WHERE state = :state AND year = :year GROUP BY event_type ORDER BY event_type";
int main(int argc, char **argv) {
  unsigned long count; int mode; Region g;
  Request *rq = load_requests(argc, argv, 2, &count, &mode);
  sqlite3 *db = open_db(argv[1]);
  Buffer b = {0};
  region_begin(&g, mode);
  append(&b, "SQLPRNT1", 8); u64(&b, count);
  sqlite3_stmt *q[3] = {prepare(db, Q1), prepare(db, Q2), prepare(db, Q3)};
  for (unsigned long i = 0; i < count; i++) {
    bytes(&b, rq[i].f[0], strlen(rq[i].f[0])); bytes(&b, rq[i].f[1], strlen(rq[i].f[1]));
    for (int k = 0; k < 3; k++) { bind_text(q[k], ":state", rq[i].f[0]); bind_int(q[k], ":year", rq[i].f[1]); drain(db, q[k], &b); reuse(q[k]); }
  }
  for (int k = 0; k < 3; k++) finish(q[k]);
  if (sqlite3_close(db) != SQLITE_OK) fail("database close");
  region_end(&g);
  free_requests(rq, count);
  export_buffer(argv[3], &b);
  return 0;
}

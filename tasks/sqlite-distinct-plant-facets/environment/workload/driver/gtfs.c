/* sqlite-gtfs-departure-join: stop departure board (OneBusAway / node-gtfs style) over a GTFS feed.
 * Statement prepared once per batch (statement-cache pattern), reset + rebound per request. */
#include "qb.h"
static const char *SQL =
  "SELECT st.departure_time, st.stop_sequence, r.route_short_name, r.route_type, a.agency_name,"
  "       t.trip_headsign, t.direction_id, t.trip_id"
  "  FROM stop_times st"
  "  JOIN trips t ON t.trip_id = st.trip_id"
  "  JOIN routes r ON r.route_id = t.route_id"
  "  LEFT JOIN agency a ON a.agency_id = r.agency_id"
  " WHERE st.stop_id = :stop_id"
  "   AND st.pickup_type IS NOT 1"
  "   AND t.service_id IN ("
  "     SELECT c.service_id FROM calendar c"
  "      WHERE :date BETWEEN c.start_date AND c.end_date"
  "        AND (CASE :dow WHEN 0 THEN c.sunday WHEN 1 THEN c.monday WHEN 2 THEN c.tuesday"
  "             WHEN 3 THEN c.wednesday WHEN 4 THEN c.thursday WHEN 5 THEN c.friday ELSE c.saturday END) = 1"
  "        AND c.service_id NOT IN (SELECT cd.service_id FROM calendar_dates cd"
  "                                  WHERE cd.date = :date AND cd.exception_type = 2)"
  "     UNION"
  "     SELECT cd.service_id FROM calendar_dates cd WHERE cd.date = :date AND cd.exception_type = 1)"
  "   AND st.departure_time >= :time"
  " ORDER BY st.departure_time, r.route_short_name, t.trip_id LIMIT 40";
int main(int argc, char **argv) {
  unsigned long count; int mode; Region g;
  Request *rq = load_requests(argc, argv, 4, &count, &mode);
  sqlite3 *db = open_db(argv[1]);
  Buffer b = {0};
  region_begin(&g, mode);
  append(&b, "SQLGTFS1", 8); u64(&b, count);
  sqlite3_stmt *s = prepare(db, SQL);
  for (unsigned long i = 0; i < count; i++) {
    bytes(&b, rq[i].f[0], strlen(rq[i].f[0])); bytes(&b, rq[i].f[1], strlen(rq[i].f[1])); bytes(&b, rq[i].f[3], strlen(rq[i].f[3]));
    bind_text(s, ":stop_id", rq[i].f[0]); bind_text(s, ":date", rq[i].f[1]); bind_int(s, ":dow", rq[i].f[2]); bind_text(s, ":time", rq[i].f[3]);
    drain(db, s, &b); reuse(s);
  }
  finish(s);
  if (sqlite3_close(db) != SQLITE_OK) fail("database close");
  region_end(&g);
  free_requests(rq, count);
  export_buffer(argv[3], &b);
  return 0;
}

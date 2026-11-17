/* sqlite-usgs-geojson-json: query raw GeoJSON feature documents stored as TEXT
 * (->/->> extraction, json_each key census, json_group_array/json_object rebuild) per one-day window. */
#include "qb.h"
static const char *Q1 =
  "SELECT doc->>'$.id', doc->>'$.properties.time', doc->>'$.properties.mag', doc->>'$.properties.magType',"
  "       doc->>'$.properties.place', doc->'$.geometry.coordinates', doc->>'$.geometry.coordinates[2]'"
  "  FROM events"
  " WHERE doc->>'$.properties.time' >= :t0 AND doc->>'$.properties.time' < :t1"
  "   AND doc->>'$.properties.mag' >= :mag"
  " ORDER BY doc->>'$.properties.time', doc->>'$.id'";
static const char *Q2 =
  "SELECT j.key, j.type, count(*) FROM events, json_each(events.doc, '$.properties') AS j"
  " WHERE events.doc->>'$.properties.time' >= :t0 AND events.doc->>'$.properties.time' < :t1"
  " GROUP BY j.key, j.type ORDER BY j.key, j.type";
static const char *Q3 =
  "SELECT doc->>'$.properties.net' AS net, count(*),"
  "       json_group_array(json_object('id', doc->>'$.id', 'mag', doc->'$.properties.mag',"
  "                                    'depth', doc->'$.geometry.coordinates[2]',"
  "                                    'types', doc->>'$.properties.types', 'ncoord', json_array_length(doc, '$.geometry.coordinates')))"
  "  FROM events"
  " WHERE doc->>'$.properties.time' >= :t0 AND doc->>'$.properties.time' < :t1"
  " GROUP BY net ORDER BY net";
int main(int argc, char **argv) {
  unsigned long count; int mode; Region g;
  Request *rq = load_requests(argc, argv, 3, &count, &mode);
  sqlite3 *db = open_db(argv[1]);
  Buffer b = {0};
  region_begin(&g, mode);
  append(&b, "SQLJSON1", 8); u64(&b, count);
  sqlite3_stmt *q1 = prepare(db, Q1), *q2 = prepare(db, Q2), *q3 = prepare(db, Q3);
  for (unsigned long i = 0; i < count; i++) {
    for (int k = 0; k < 3; k++) bytes(&b, rq[i].f[k], strlen(rq[i].f[k]));
    bind_int(q1, ":t0", rq[i].f[0]); bind_int(q1, ":t1", rq[i].f[1]); bind_real(q1, ":mag", rq[i].f[2]);
    drain(db, q1, &b); reuse(q1);
    bind_int(q2, ":t0", rq[i].f[0]); bind_int(q2, ":t1", rq[i].f[1]); drain(db, q2, &b); reuse(q2);
    bind_int(q3, ":t0", rq[i].f[0]); bind_int(q3, ":t1", rq[i].f[1]); drain(db, q3, &b); reuse(q3);
  }
  finish(q1); finish(q2); finish(q3);
  if (sqlite3_close(db) != SQLITE_OK) fail("database close");
  region_end(&g);
  free_requests(rq, count);
  export_buffer(argv[3], &b);
  return 0;
}

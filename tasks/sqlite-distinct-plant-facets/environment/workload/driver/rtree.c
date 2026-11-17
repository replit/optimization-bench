/* sqlite-geonames-rtree: GeoPackage/GDAL-style bounding-box filter through an R*Tree spatial index
 * joined back to the feature table (requires SQLITE_ENABLE_RTREE, i.e. configure --enable-rtree). */
#include "qb.h"
static const char *Q1 =
  "SELECT p.geonameid, p.feature_code, p.population"
  "  FROM places_rtree r JOIN places p ON p.geonameid = r.id"
  " WHERE r.minx <= :maxx AND r.maxx >= :minx AND r.miny <= :maxy AND r.maxy >= :miny"
  " ORDER BY p.population DESC, p.geonameid";
static const char *Q2 =
  "SELECT count(*), min(id), max(id) FROM places_rtree"
  " WHERE minx <= :maxx AND maxx >= :minx AND miny <= :maxy AND maxy >= :miny";
int main(int argc, char **argv) {
  unsigned long count; int mode; Region g;
  Request *rq = load_requests(argc, argv, 4, &count, &mode);
  sqlite3 *db = open_db(argv[1]);
  Buffer b = {0};
  region_begin(&g, mode);
  append(&b, "SQLRTRE1", 8); u64(&b, count);
  sqlite3_stmt *q[2] = {prepare(db, Q1), prepare(db, Q2)};
  for (unsigned long i = 0; i < count; i++) {
    for (int k = 0; k < 4; k++) bytes(&b, rq[i].f[k], strlen(rq[i].f[k]));
    for (int k = 0; k < 2; k++) {
      bind_real(q[k], ":minx", rq[i].f[0]); bind_real(q[k], ":maxx", rq[i].f[1]);
      bind_real(q[k], ":miny", rq[i].f[2]); bind_real(q[k], ":maxy", rq[i].f[3]);
      drain(db, q[k], &b); reuse(q[k]);
    }
  }
  for (int k = 0; k < 2; k++) finish(q[k]);
  if (sqlite3_close(db) != SQLITE_OK) fail("database close");
  region_end(&g);
  free_requests(rq, count);
  export_buffer(argv[3], &b);
  return 0;
}

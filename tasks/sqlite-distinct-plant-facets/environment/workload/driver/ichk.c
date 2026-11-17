/* ichk: PRAGMA integrity_check over staged in-memory copies of whole databases; every
 * result row is materialized. Usage: ichk OUT.bin DB... [--plain] */
#include "obcommon.h"
int main(int argc, char **argv) {
  argc = ob_args(argc, argv);
  if (argc < 3) fail("usage: ichk OUT DB... [--plain]");
  int ndoc = argc - 2; sqlite3 **dbs = calloc(ndoc, sizeof(*dbs));
  for (int k = 0; k < ndoc; k++) {
    if (sqlite3_open(":memory:", &dbs[k]) != SQLITE_OK) fail("open");
    stage_into_memory(dbs[k], argv[2 + k]);
  }
  Buffer b = {0}; append(&b, "SQLICK01", 8); u64(&b, (uint64_t)ndoc);
  ob_begin();
  for (int k = 0; k < ndoc; k++) {
    sqlite3_stmt *s = 0; int rc; uint64_t rows = 0; size_t at;
    if (sqlite3_prepare_v2(dbs[k], "PRAGMA integrity_check", -1, &s, 0)) failrc(dbs[k], "prepare");
    at = b.n; u64(&b, 0);
    while ((rc = sqlite3_step(s)) == SQLITE_ROW) {
      const unsigned char *t = sqlite3_column_text(s, 0);
      bytes(&b, t, (size_t)sqlite3_column_bytes(s, 0)); rows++;
    }
    if (rc != SQLITE_DONE || sqlite3_finalize(s) != SQLITE_OK) failrc(dbs[k], "integrity_check");
    for (int i = 0; i < 8; i++) b.p[at + i] = (unsigned char)(rows >> (8 * i));
    if (sqlite3_close(dbs[k]) != SQLITE_OK) fail("close");
  }
  ob_end();
  spill(argv[1], b.p, b.n);
  return 0;
}

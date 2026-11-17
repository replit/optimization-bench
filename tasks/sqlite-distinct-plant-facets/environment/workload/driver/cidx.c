/* cidx: CREATE INDEX on a populated in-memory table (external merge sort through the
 * VDBE sorter with native cache_size/temp_store defaults, then index b-tree build).
 * Usage: cidx OUTPREFIX DOC.tsv... [--plain]; DOC rows: id<TAB>lang<TAB>text (Tatoeba
 * export, file order = id order). Table load is untimed staging. */
#include "obcommon.h"
static const char *ddl[] = {
  "CREATE INDEX sentences_text ON sentences(text)",
  "CREATE INDEX sentences_text_nocase ON sentences(text COLLATE NOCASE)",
  "CREATE INDEX sentences_lang_text ON sentences(lang, text)",
};
int main(int argc, char **argv) {
  argc = ob_args(argc, argv);
  if (argc < 3) fail("usage: cidx OUTPREFIX DOC... [--plain]");
  int ndoc = argc - 2; sqlite3 **dbs = calloc(ndoc, sizeof(*dbs));
  for (int k = 0; k < ndoc; k++) {
    size_t len; char *p = slurp(argv[2 + k], &len), *e = p + len; sqlite3_stmt *s = 0;
    if (sqlite3_open(":memory:", &dbs[k]) != SQLITE_OK) fail("open");
    exec0(dbs[k], "CREATE TABLE sentences(id INTEGER PRIMARY KEY, lang TEXT, text TEXT)");
    exec0(dbs[k], "BEGIN");
    if (sqlite3_prepare_v2(dbs[k], "INSERT INTO sentences VALUES(?,?,?)", -1, &s, 0)) failrc(dbs[k], "prep");
    while (p < e) {
      char *nl = memchr(p, '\n', (size_t)(e - p)); if (!nl) nl = e; *nl = 0;
      char *t1 = strchr(p, '\t'); char *t2 = t1 ? strchr(t1 + 1, '\t') : 0;
      if (!t1 || !t2) fail("row shape"); *t1 = 0; *t2 = 0;
      sqlite3_bind_int64(s, 1, strtoll(p, 0, 10)); sqlite3_bind_text(s, 2, t1 + 1, -1, SQLITE_STATIC);
      sqlite3_bind_text(s, 3, t2 + 1, (int)(nl - t2 - 1), SQLITE_STATIC);
      if (sqlite3_step(s) != SQLITE_DONE || sqlite3_reset(s)) failrc(dbs[k], "load");
      p = nl + 1;
    }
    sqlite3_finalize(s); exec0(dbs[k], "COMMIT");
  }
  Buffer b = {0}; append(&b, "SQLCIX01", 8); u64(&b, (uint64_t)ndoc);
  unsigned char **img = calloc(ndoc, sizeof(*img)); sqlite3_int64 *isz = calloc(ndoc, sizeof(*isz));
  ob_begin();
  for (int k = 0; k < ndoc; k++) {
    for (size_t i = 0; i < sizeof ddl / sizeof *ddl; i++) exec0(dbs[k], ddl[i]);
    img[k] = image(dbs[k], "main", &isz[k]);
    u64(&b, (uint64_t)sqlite3_total_changes64(dbs[k]));
    if (sqlite3_close(dbs[k]) != SQLITE_OK) fail("close");
  }
  ob_end();
  char path[4096];
  for (int k = 0; k < ndoc; k++) { snprintf(path, sizeof path, "%s.%d.db", argv[1], k); spill(path, img[k], (size_t)isz[k]); sqlite3_free(img[k]); }
  snprintf(path, sizeof path, "%s.bin", argv[1]); spill(path, b.p, b.n);
  return 0;
}

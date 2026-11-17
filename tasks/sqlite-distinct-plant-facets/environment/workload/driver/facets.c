/* Protected adapter; SQLite production source is compiled separately unchanged. */
#define _POSIX_C_SOURCE 200809L
#include <sqlite3.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { unsigned char *p; size_t n, cap; } Buffer;
typedef struct { char *country, *key; } Request;
static void fail(const char *s) { fprintf(stderr, "%s\n", s); exit(2); }
static void append(Buffer *b, const void *p, size_t n) {
  if (!n) return;
  if (n > SIZE_MAX - b->n) fail("size overflow");
  if (b->n + n > b->cap) {
    size_t cap = b->cap ? b->cap : 4096;
    while (cap < b->n + n) { if (cap > SIZE_MAX / 2) fail("size overflow"); cap *= 2; }
    void *q = realloc(b->p, cap); if (!q) fail("allocation"); b->p = q; b->cap = cap;
  }
  memcpy(b->p + b->n, p, n); b->n += n;
}
static void u64(Buffer *b, uint64_t v) {
  unsigned char p[8]; int i;
  for (i = 0; i < 8; i++) p[i] = (unsigned char)(v >> (8 * i));
  append(b, p, 8);
}
static void bytes(Buffer *b, const void *p, size_t n) { u64(b, n); append(b, p, n); }
static void request(sqlite3 *db, const Request *r, Buffer *b) {
  const char *columns[] = {"country_long", "owner", "primary_fuel"};
  int k;
  bytes(b, r->country, strlen(r->country)); bytes(b, r->key, strlen(r->key));
  for (k = 0; k < 3; k++) {
    char *sql = sqlite3_mprintf(
      "select \"%w\" as value,count(*) as count from "
      "(select * from \"global-power-plants\" where country_long=:country "
      "and gppd_idnr>=:key and gppd_idnr<=:key) "
      "where \"%w\" is not null group by \"%w\" order by count desc,value limit 31",
      columns[k], columns[k], columns[k]);
    sqlite3_stmt *s = 0; int rc; uint64_t rows = 0; size_t offset = b->n;
    if (!sql || sqlite3_prepare_v2(db, sql, -1, &s, 0) != SQLITE_OK) fail(sqlite3_errmsg(db));
    sqlite3_free(sql);
    if (sqlite3_bind_text(s, sqlite3_bind_parameter_index(s, ":country"), r->country,
                          -1, SQLITE_TRANSIENT) != SQLITE_OK ||
        sqlite3_bind_text(s, sqlite3_bind_parameter_index(s, ":key"), r->key,
                          -1, SQLITE_TRANSIENT) != SQLITE_OK) fail("bind");
    u64(b, 0);
    while ((rc = sqlite3_step(s)) == SQLITE_ROW) {
      int type = sqlite3_column_type(s, 0), n = sqlite3_column_bytes(s, 0);
      const void *value = sqlite3_column_blob(s, 0);
      if (type != SQLITE_TEXT || n < 0 || sqlite3_column_type(s, 1) != SQLITE_INTEGER)
        fail("unexpected facet type");
      u64(b, (uint64_t)type); bytes(b, value, (size_t)n);
      u64(b, (uint64_t)sqlite3_column_int64(s, 1)); rows++;
    }
    if (rc != SQLITE_DONE || sqlite3_finalize(s) != SQLITE_OK) fail("query/finalize");
    for (int i = 0; i < 8; i++) b->p[offset+i] = (unsigned char)(rows >> (8*i));
  }
}
int main(int argc, char **argv) {
  if (argc != 5 && argc != 6) fail("expected database, requests, output, count, optional --plain");
  int plain = argc == 6 && !strcmp(argv[5], "--plain");
  if (argc == 6 && !plain) fail("unknown mode");
  char *end = 0; unsigned long count = strtoul(argv[4], &end, 10);
  if (!end || *end || !count || count > 100000) fail("bad request count");
  Request *requests = calloc(count, sizeof(*requests)); if (!requests) fail("allocation");
  FILE *f = fopen(argv[2], "rb"); if (!f) fail("requests open");
  char *line = 0; size_t cap = 0; unsigned long n = 0;
  while (getline(&line, &cap, f) >= 0) {
    char *tab = strchr(line, '\t');
    if (n >= count || !tab || strchr(tab+1, '\t')) fail("request shape");
    *tab++ = 0; tab[strcspn(tab, "\r\n")] = 0;
    if (!*line || !*tab) fail("empty request");
    requests[n].country = strdup(line); requests[n].key = strdup(tab);
    if (!requests[n].country || !requests[n].key) fail("allocation");
    if (n) {
      int order = strcmp(requests[n-1].country, requests[n].country);
      if (order > 0 || (order == 0 && strcmp(requests[n-1].key, requests[n].key) >= 0))
        fail("duplicate or unordered key");
    }
    n++;
  }
  free(line); if (ferror(f) || fclose(f) || n != count) fail("request inventory");
  sqlite3 *db = 0;
  char *uri = sqlite3_mprintf("file:%s?immutable=1", argv[1]);
  if (!uri || sqlite3_open_v2(uri, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, 0) != SQLITE_OK)
    fail("database open");
  sqlite3_free(uri);
  if (sqlite3_busy_timeout(db, 5000) != SQLITE_OK) fail("busy timeout");
  /* No actual request is executed before ready; no result/statement cache. */
  if (!plain) raise(SIGSTOP);
  Buffer b = {0};
  append(&b, "SQLDIST1", 8); u64(&b, count);
  for (unsigned long i = 0; i < count; i++) request(db, &requests[i], &b);
  if (sqlite3_close(db) != SQLITE_OK) fail("database close");
  for (unsigned long i = 0; i < count; i++) { free(requests[i].country); free(requests[i].key); }
  free(requests);
  if (!plain) raise(SIGSTOP);
  f = fopen(argv[3], "wb");
  if (!f || fwrite(b.p, 1, b.n, f) != b.n || fclose(f)) fail("output export");
  free(b.p);
  return 0;
}

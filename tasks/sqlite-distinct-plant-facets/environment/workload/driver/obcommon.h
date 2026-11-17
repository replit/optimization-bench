/* Shared helpers for the storage/maintenance prototype adapters (SQLite agent 1).
 * Same shape as the protected facets.c adapter: untimed staging, SIGSTOP at the
 * timed boundaries unless --plain (dev mode prints CLOCK_MONOTONIC window and
 * rusage deltas to stderr instead), length-framed result buffer exported after
 * the timed region. */
#define _GNU_SOURCE
#include <sqlite3.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/resource.h>
#include <sys/stat.h>

typedef struct { unsigned char *p; size_t n, cap; } Buffer;
static void fail(const char *s) { fprintf(stderr, "%s\n", s); exit(2); }
static void failrc(sqlite3 *db, const char *what) {
  fprintf(stderr, "%s: %s\n", what, db ? sqlite3_errmsg(db) : "?"); exit(2);
}
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
static void str(Buffer *b, const char *s) { bytes(b, s, strlen(s)); }

/* Read a whole file into a NUL-terminated heap buffer. */
static char *slurp(const char *path, size_t *len) {
  FILE *f = fopen(path, "rb"); if (!f) { fprintf(stderr, "open %s\n", path); exit(2); }
  struct stat st; if (fstat(fileno(f), &st)) fail("stat");
  char *p = malloc((size_t)st.st_size + 1); if (!p) fail("allocation");
  if (fread(p, 1, (size_t)st.st_size, f) != (size_t)st.st_size || fclose(f)) fail("read");
  p[st.st_size] = 0; if (len) *len = (size_t)st.st_size; return p;
}
static void spill(const char *path, const void *p, size_t n) {
  FILE *f = fopen(path, "wb");
  if (!f || (n && fwrite(p, 1, n, f) != n) || fclose(f)) fail("output export");
}

/* Timed-region boundaries. */
static int ob_plain;
static struct timespec ob_t0; static struct rusage ob_r0;
static double tv(struct timeval t) { return t.tv_sec + t.tv_usec / 1e6; }
static void ob_begin(void) {
  if (!ob_plain) raise(SIGSTOP);
  getrusage(RUSAGE_SELF, &ob_r0); clock_gettime(CLOCK_MONOTONIC, &ob_t0);
}
static void ob_end(void) {
  struct timespec t1; struct rusage r1;
  clock_gettime(CLOCK_MONOTONIC, &t1); getrusage(RUSAGE_SELF, &r1);
  if (!ob_plain) { raise(SIGSTOP); return; }
  long long a = ob_t0.tv_sec * 1000000000LL + ob_t0.tv_nsec, z = t1.tv_sec * 1000000000LL + t1.tv_nsec;
  fprintf(stderr, "OBTIME %lld %lld %.6f %.6f %.6f\n", a, z, (z - a) / 1e9,
          tv(r1.ru_utime) - tv(ob_r0.ru_utime), tv(r1.ru_stime) - tv(ob_r0.ru_stime));
}
static int ob_args(int argc, char **argv) {
  if (argc > 1 && !strcmp(argv[argc - 1], "--plain")) { ob_plain = 1; return argc - 1; }
  return argc;
}
static void exec0(sqlite3 *db, const char *sql) {
  char *err = 0;
  if (sqlite3_exec(db, sql, 0, 0, &err) != SQLITE_OK) {
    fprintf(stderr, "exec failed: %s: %s\n", sql, err ? err : "?"); exit(2);
  }
}
/* Copy an on-disk database into an open :memory: connection (untimed staging). */
static void stage_into_memory(sqlite3 *mem, const char *path) {
  sqlite3 *src = 0;
  char *uri = sqlite3_mprintf("file:%s?immutable=1", path);
  if (!uri || sqlite3_open_v2(uri, &src, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, 0) != SQLITE_OK)
    failrc(src, "stage open");
  sqlite3_free(uri);
  sqlite3_backup *bk = sqlite3_backup_init(mem, "main", src, "main");
  if (!bk) failrc(mem, "backup init");
  if (sqlite3_backup_step(bk, -1) != SQLITE_DONE || sqlite3_backup_finish(bk) != SQLITE_OK)
    failrc(mem, "backup");
  if (sqlite3_close(src) != SQLITE_OK) fail("stage close");
}
/* Serialize schema "main" (or zSchema) into a heap copy inside the timed region. */
static unsigned char *image(sqlite3 *db, const char *zSchema, sqlite3_int64 *sz) {
  unsigned char *p = sqlite3_serialize(db, zSchema, sz, 0);
  if (!p || *sz <= 0) failrc(db, "serialize");
  return p;
}

/* Protected adapter helpers shared by SQLite agent-3 (w2-) candidate drivers (fork of agent 2 qb.h).
 * Same framing and resident protocol as tasks/sqlite-distinct-plant-facets/environment/workload/driver/facets.c:
 * staging before the first SIGSTOP; the timed region covers SQL prepare/bind/step, every column accessor,
 * complete typed result buffering, finalize and connection close; output export after the second SIGSTOP.
 * Connection: file:<db>?immutable=1, SQLITE_OPEN_READONLY|SQLITE_OPEN_URI, busy timeout 5000 ms, PRAGMA cache_size=-262144.
 * argv: <database> <requests.tsv> <output> <count> [--plain|--time]
 *   (no flag) resident protocol (SIGSTOP before/after the timed region, as runner.py expects)
 *   --plain   no SIGSTOP
 *   --time    no SIGSTOP; prints "TIMED wall_s user_s sys_s" for the timed region to stderr (dev VM only) */
#define _POSIX_C_SOURCE 200809L
#include <sqlite3.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/resource.h>

typedef struct { unsigned char *p; size_t n, cap; } Buffer;
typedef struct { int nf; char **f; } Request;
typedef struct { int mode; struct timespec t0; struct rusage r0; } Region;
enum { MODE_RESIDENT = 0, MODE_PLAIN = 1, MODE_TIME = 2 };

static void fail(const char *s) { fprintf(stderr, "%s\n", s); exit(2); }
static void append(Buffer *b, const void *p, size_t n) {
  if (!n) return;
  if (n > SIZE_MAX - b->n) fail("size overflow");
  if (b->n + n > b->cap) {
    size_t cap = b->cap ? b->cap : 65536;
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
static void patch64(Buffer *b, size_t off, uint64_t v) {
  for (int i = 0; i < 8; i++) b->p[off + i] = (unsigned char)(v >> (8 * i));
}
/* One typed value: type tag, then INTEGER int64 / FLOAT IEEE-754 bits / TEXT,BLOB length+bytes / NULL nothing. */
static void put_value(Buffer *b, sqlite3_stmt *s, int i) {
  int t = sqlite3_column_type(s, i);
  u64(b, (uint64_t)t);
  switch (t) {
    case SQLITE_INTEGER: u64(b, (uint64_t)sqlite3_column_int64(s, i)); break;
    case SQLITE_FLOAT: { double d = sqlite3_column_double(s, i); uint64_t x; memcpy(&x, &d, 8); u64(b, x); break; }
    case SQLITE_TEXT: { const unsigned char *p = sqlite3_column_text(s, i); int n = sqlite3_column_bytes(s, i);
                        if (n < 0 || (n && !p)) fail("text accessor"); bytes(b, p, (size_t)n); break; }
    case SQLITE_BLOB: { const void *p = sqlite3_column_blob(s, i); int n = sqlite3_column_bytes(s, i);
                        if (n < 0 || (n && !p)) fail("blob accessor"); bytes(b, p, (size_t)n); break; }
    case SQLITE_NULL: break;
    default: fail("unknown column type");
  }
}
/* Step a bound statement to completion, buffering every row: [rows][columns] then values row-major. */
static void drain(sqlite3 *db, sqlite3_stmt *s, Buffer *b) {
  size_t off = b->n; uint64_t rows = 0; int rc, nc = sqlite3_column_count(s);
  u64(b, 0); u64(b, (uint64_t)nc);
  while ((rc = sqlite3_step(s)) == SQLITE_ROW) {
    for (int i = 0; i < nc; i++) put_value(b, s, i);
    rows++;
  }
  if (rc != SQLITE_DONE) fail(sqlite3_errmsg(db));
  patch64(b, off, rows);
}
static sqlite3_stmt *prepare(sqlite3 *db, const char *sql) {
  sqlite3_stmt *s = 0;
  if (sqlite3_prepare_v2(db, sql, -1, &s, 0) != SQLITE_OK || !s) fail(sqlite3_errmsg(db));
  return s;
}
static int pidx(sqlite3_stmt *s, const char *name) {
  int i = sqlite3_bind_parameter_index(s, name); if (!i) fail("missing parameter"); return i;
}
static void bind_text(sqlite3_stmt *s, const char *name, const char *v) {
  if (sqlite3_bind_text(s, pidx(s, name), v, -1, SQLITE_TRANSIENT) != SQLITE_OK) fail("bind text");
}
static void bind_int(sqlite3_stmt *s, const char *name, const char *v) {
  char *e = 0; long long x = strtoll(v, &e, 10); if (!e || *e) fail("bad integer request field");
  if (sqlite3_bind_int64(s, pidx(s, name), x) != SQLITE_OK) fail("bind int");
}
static void bind_real(sqlite3_stmt *s, const char *name, const char *v) {
  char *e = 0; double x = strtod(v, &e); if (!e || *e) fail("bad real request field");
  if (sqlite3_bind_double(s, pidx(s, name), x) != SQLITE_OK) fail("bind real");
}
static void finish(sqlite3_stmt *s) { if (sqlite3_finalize(s) != SQLITE_OK) fail("finalize"); }
static void reuse(sqlite3_stmt *s) {
  if (sqlite3_reset(s) != SQLITE_OK || sqlite3_clear_bindings(s) != SQLITE_OK) fail("reset");
}
/* Staging: parse argv, read the request TSV (nf fields per line, no duplicate lines in order). */
static Request *load_requests(int argc, char **argv, int nf, unsigned long *count, int *mode) {
  if (argc != 5 && argc != 6) fail("expected database, requests, output, count, optional --plain|--time");
  *mode = MODE_RESIDENT;
  if (argc == 6) {
    if (!strcmp(argv[5], "--plain")) *mode = MODE_PLAIN;
    else if (!strcmp(argv[5], "--time")) *mode = MODE_TIME;
    else fail("unknown mode");
  }
  char *end = 0; *count = strtoul(argv[4], &end, 10);
  if (!end || *end || !*count || *count > 10000000) fail("bad request count");
  Request *r = calloc(*count, sizeof(*r)); if (!r) fail("allocation");
  FILE *f = fopen(argv[2], "rb"); if (!f) fail("requests open");
  char *line = 0; size_t cap = 0; unsigned long n = 0;
  while (getline(&line, &cap, f) >= 0) {
    line[strcspn(line, "\r\n")] = 0;
    if (n >= *count) fail("request inventory");
    r[n].nf = nf; r[n].f = calloc((size_t)nf, sizeof(char *)); if (!r[n].f) fail("allocation");
    char *p = line;
    for (int i = 0; i < nf; i++) {
      char *tab = strchr(p, '\t');
      if ((i < nf - 1) != (tab != 0)) fail("request shape");
      if (tab) *tab = 0;
      r[n].f[i] = strdup(p); if (!r[n].f[i]) fail("allocation");
      p = tab ? tab + 1 : p;
    }
    n++;
  }
  free(line); if (ferror(f) || fclose(f) || n != *count) fail("request inventory");
  return r;
}
static void free_requests(Request *r, unsigned long count) {
  for (unsigned long i = 0; i < count; i++) { for (int j = 0; j < r[i].nf; j++) free(r[i].f[j]); free(r[i].f); }
  free(r);
}
static sqlite3 *open_db(const char *path) {
  sqlite3 *db = 0;
  char *uri = sqlite3_mprintf("file:%s?immutable=1", path);
  if (!uri || sqlite3_open_v2(uri, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, 0) != SQLITE_OK)
    fail("database open");
  sqlite3_free(uri);
  if (sqlite3_busy_timeout(db, 5000) != SQLITE_OK) fail("busy timeout");
  /* Application-configured 256 MiB page cache (Datasette's cache_size_kb setting analog). Staging only:
   * no page is read here; pages are loaded during the timed region on first use. */
  if (sqlite3_exec(db, "PRAGMA cache_size = -262144", 0, 0, 0) != SQLITE_OK) fail("cache_size");
  return db;
}
static double tv(struct timeval t) { return (double)t.tv_sec + (double)t.tv_usec / 1e6; }
static void region_begin(Region *g, int mode) {
  g->mode = mode;
  if (mode == MODE_RESIDENT) raise(SIGSTOP);
  clock_gettime(CLOCK_MONOTONIC, &g->t0); getrusage(RUSAGE_SELF, &g->r0);
}
/* Dev-VM profiling only: W2_WINDOW=<file> appends the timed window (CLOCK_MONOTONIC ns) so perf samples outside
 * the timed region (staging, export) can be excluded from the line profile. */
static void window_note(Region *g, struct timespec *t1) {
  const char *w = getenv("W2_WINDOW"); if (!w) return;
  FILE *f = fopen(w, "a"); if (!f) return;
  fprintf(f, "%lld %lld\n", (long long)g->t0.tv_sec * 1000000000LL + g->t0.tv_nsec, (long long)t1->tv_sec * 1000000000LL + t1->tv_nsec);
  fclose(f);
}
static void region_end(Region *g) {
  struct timespec t1; struct rusage r1;
  clock_gettime(CLOCK_MONOTONIC, &t1); getrusage(RUSAGE_SELF, &r1);
  window_note(g, &t1);
  if (g->mode == MODE_TIME)
    fprintf(stderr, "TIMED %.6f %.6f %.6f\n",
            (double)(t1.tv_sec - g->t0.tv_sec) + (double)(t1.tv_nsec - g->t0.tv_nsec) / 1e9,
            tv(r1.ru_utime) - tv(g->r0.ru_utime), tv(r1.ru_stime) - tv(g->r0.ru_stime));
  if (g->mode == MODE_RESIDENT) raise(SIGSTOP);
}
static void export_buffer(const char *path, Buffer *b) {
  FILE *f = fopen(path, "wb");
  if (!f || fwrite(b->p, 1, b->n, f) != b->n || fclose(f)) fail("output export");
  free(b->p);
}

/* ---- agent-3 additions ---- */
/* Read a whole file (staging). */
static unsigned char *slurp(const char *path, size_t *n) {
  FILE *f = fopen(path, "rb"); if (!f) fail("open input");
  if (fseek(f, 0, SEEK_END)) fail("seek"); long sz = ftell(f); if (sz < 0) fail("tell"); rewind(f);
  unsigned char *p = malloc(sz ? (size_t)sz : 1); if (!p) fail("allocation");
  if ((size_t)sz && fread(p, 1, (size_t)sz, f) != (size_t)sz) fail("read input"); fclose(f);
  *n = (size_t)sz; return p;
}
/* Staging: an application-owned writable in-memory copy of a database image via sqlite3_deserialize
 * (SQLITE_DESERIALIZE_FREEONCLOSE|RESIZEABLE). No file I/O after staging. */
static sqlite3 *open_mem_copy(const char *path) {
  size_t n; unsigned char *img = slurp(path, &n);
  sqlite3 *db = 0;
  if (sqlite3_open_v2(":memory:", &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, 0) != SQLITE_OK) fail("open memory");
  unsigned char *buf = sqlite3_malloc64(n); if (!buf) fail("allocation"); memcpy(buf, img, n); free(img);
  if (sqlite3_deserialize(db, "main", buf, (sqlite3_int64)n, (sqlite3_int64)n,
                          SQLITE_DESERIALIZE_FREEONCLOSE | SQLITE_DESERIALIZE_RESIZEABLE) != SQLITE_OK) fail(sqlite3_errmsg(db));
  if (sqlite3_busy_timeout(db, 5000) != SQLITE_OK) fail("busy timeout");
  return db;
}
static void exec_ok(sqlite3 *db, const char *sql) { char *e = 0; if (sqlite3_exec(db, sql, 0, 0, &e) != SQLITE_OK) fail(e ? e : sqlite3_errmsg(db)); }
/* Unescape a request field written by the input builders: \\n \\t \\\\ \\r. In place. */
static void unescape(char *s) {
  char *w = s;
  for (char *r = s; *r; r++) {
    if (*r == '\\' && r[1]) { r++; *w++ = *r == 'n' ? '\n' : *r == 't' ? '\t' : *r == 'r' ? '\r' : *r; }
    else *w++ = *r;
  }
  *w = 0;
}

/* Verifier-only SQLite differential observer. No verdicts.
 * argv: database path, script path. The script holds NUL-separated SQL
 * statements; "--reopen" closes and reopens the database. stdout: one JSON
 * array with a complete typed record per statement: column names, ordered
 * rows (integers as decimal text, doubles as exact hexadecimal, text and blobs
 * as hex bytes), return codes, extended codes, errmsg, changes and autocommit.
 */
#include <sqlite3.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ROWS 5000

int sqlite3_decimal_init(sqlite3 *db, char **pzErrMsg, const sqlite3_api_routines *pApi);

static void hex(const void *data, int n) {
  const unsigned char *p = data;
  putchar('"');
  for (int i = 0; i < n; i++) printf("%02x", p[i]);
  putchar('"');
}

static void text(const char *value) {
  if (value) hex(value, (int)strlen(value)); else printf("null");
}

static sqlite3 *open_db(const char *path) {
  sqlite3 *db = 0;
  if (sqlite3_open_v2(path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, 0) != SQLITE_OK) {
    fprintf(stderr, "open: %s\n", db ? sqlite3_errmsg(db) : "out of memory");
    exit(3);
  }
  sqlite3_extended_result_codes(db, 1);
  if (sqlite3_decimal_init(db, 0, 0) != SQLITE_OK) {
    fprintf(stderr, "decimal init failed\n");
    exit(3);
  }
  return db;
}

/* Error codes and messages are defined only after a failed call. */
static void error_fields(sqlite3 *db, int rc) {
  printf(",\"rc\":%d", rc);
  if (rc == SQLITE_OK || rc == SQLITE_ROW || rc == SQLITE_DONE) return;
  printf(",\"xrc\":%d,\"msg\":", sqlite3_extended_errcode(db));
  text(sqlite3_errmsg(db));
}

static void value(sqlite3_stmt *s, int i) {
  switch (sqlite3_column_type(s, i)) {
    case SQLITE_INTEGER: printf("{\"i\":\"%lld\"}", (long long)sqlite3_column_int64(s, i)); break;
    case SQLITE_FLOAT: printf("{\"f\":\"%a\"}", sqlite3_column_double(s, i)); break;
    case SQLITE_TEXT: {
      const unsigned char *t = sqlite3_column_text(s, i);
      printf("{\"t\":"); hex(t, sqlite3_column_bytes(s, i)); putchar('}');
      break;
    }
    case SQLITE_BLOB: {
      const void *b = sqlite3_column_blob(s, i);
      printf("{\"b\":"); hex(b, sqlite3_column_bytes(s, i)); putchar('}');
      break;
    }
    default: printf("null");
  }
}

/* Every prepared statement in one script entry, in order. */
static void run(sqlite3 *db, const char *sql) {
  int first = 1;
  printf("[");
  while (sql && *sql) {
    sqlite3_stmt *s = 0;
    const char *tail = 0;
    int rc = sqlite3_prepare_v2(db, sql, -1, &s, &tail);
    if (!first) putchar(',');
    first = 0;
    if (rc != SQLITE_OK) {
      printf("{\"stage\":\"prepare\"");
      error_fields(db, rc);
      printf("}");
      break;
    }
    if (!s) {  /* Only whitespace or comments remained. */
      printf("{\"stage\":\"empty\"}");
      break;
    }
    int columns = sqlite3_column_count(s), rows = 0;
    printf("{\"stage\":\"step\",\"columns\":[");
    for (int i = 0; i < columns; i++) {
      if (i) putchar(',');
      text(sqlite3_column_name(s, i));
    }
    printf("],\"rows\":[");
    while ((rc = sqlite3_step(s)) == SQLITE_ROW && rows < MAX_ROWS) {
      if (rows++) putchar(',');
      putchar('[');
      for (int i = 0; i < columns; i++) {
        if (i) putchar(',');
        value(s, i);
      }
      putchar(']');
    }
    printf("],\"truncated\":%s", rc == SQLITE_ROW ? "true" : "false");
    error_fields(db, rc);
    printf(",\"finalize\":%d,\"changes\":%lld,\"autocommit\":%d}", sqlite3_finalize(s),
           (long long)sqlite3_changes64(db), sqlite3_get_autocommit(db));
    sql = tail;
  }
  printf("]");
}

int main(int argc, char **argv) {
  if (argc != 3) {
    fprintf(stderr, "usage: probe DATABASE SCRIPT\n");
    return 2;
  }
  FILE *f = fopen(argv[2], "rb");
  if (!f) { perror("script"); return 2; }
  fseek(f, 0, SEEK_END);
  long n = ftell(f);
  rewind(f);
  char *script = malloc(n + 1);
  if (!script || fread(script, 1, n, f) != (size_t)n) { perror("read"); return 2; }
  fclose(f);
  script[n] = 0;
  sqlite3 *db = open_db(argv[1]);
  int first = 1;
  printf("[");
  for (char *p = script; p < script + n + 1; p += strlen(p) + 1) {
    if (!*p) continue;
    if (!first) putchar(',');
    first = 0;
    if (!strcmp(p, "--reopen")) {
      printf("{\"reopen\":%d}", sqlite3_close(db));
      db = open_db(argv[1]);
      continue;
    }
    run(db, p);
  }
  printf("%s{\"close\":%d}]\n", first ? "" : ",", sqlite3_close(db));
  free(script);
  return fflush(stdout) ? 4 : 0;
}

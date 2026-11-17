/* Protected native ranked-document workload. Timing belongs to runner.py. */
#define _POSIX_C_SOURCE 200809L
#include <sqlite3.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
typedef struct { unsigned char *p; size_t n, cap; } Buffer;
typedef struct { char *id, *question, *match; } Request;
static const char *SQL =
  "SELECT d.id,d.origin,d.title,d.body,docs_fts.rank "
  "FROM docs AS d JOIN docs_fts ON d.id=docs_fts.rowid "
  "WHERE docs_fts MATCH ?1 ORDER BY docs_fts.rank ASC,d.id ASC LIMIT 20";
static void fail(const char *s) { fprintf(stderr, "%s\n", s); exit(2); }
static void append(Buffer *b, const void *p, size_t n) {
  if (n > SIZE_MAX - b->n) fail("size overflow");
  if (b->n + n > b->cap) {
    size_t cap = b->cap ? b->cap : 4096;
    while (cap < b->n + n) { if (cap > SIZE_MAX/2) fail("size overflow"); cap *= 2; }
    void *q = realloc(b->p, cap); if (!q) fail("allocation");
    b->p = q; b->cap = cap;
  }
  if (n) memcpy(b->p + b->n, p, n);
  b->n += n;
}
static void u64(Buffer *b, uint64_t v) {
  unsigned char p[8];
  for (int i=0; i<8; i++) p[i] = (unsigned char)(v >> (8*i));
  append(b,p,8);
}
static void bytes(Buffer *b, const void *p, size_t n) { u64(b,n); append(b,p,n); }
static void text(Buffer *b, const char *s) { bytes(b,s,strlen(s)); }
static void query(sqlite3 *db, const Request *r, Buffer *b) {
  text(b,r->id); text(b,r->question); text(b,r->match);
  sqlite3_stmt *s = NULL;
  int rc = sqlite3_prepare_v2(db,SQL,-1,&s,NULL);
  if (rc != SQLITE_OK || !sqlite3_stmt_readonly(s)) fail("prepare/read-only");
  if (sqlite3_bind_text(s,1,r->match,-1,SQLITE_TRANSIENT) != SQLITE_OK) fail("bind");
  size_t start=b->n; uint64_t rows=0; u64(b,0);
  while ((rc=sqlite3_step(s)) == SQLITE_ROW) {
    if (sqlite3_column_count(s)!=5 || sqlite3_column_type(s,0)!=SQLITE_INTEGER ||
        sqlite3_column_type(s,4)!=SQLITE_FLOAT) fail("result type");
    u64(b,SQLITE_INTEGER); u64(b,(uint64_t)sqlite3_column_int64(s,0));
    for (int k=1;k<=3;k++) {
      if (sqlite3_column_type(s,k)!=SQLITE_TEXT) fail("text type");
      const void *value=sqlite3_column_text(s,k);
      int n=sqlite3_column_bytes(s,k);
      if (!value || n<0) fail("text accessor");
      u64(b,SQLITE_TEXT); bytes(b,value,(size_t)n);
    }
    double rank=sqlite3_column_double(s,4);
    uint64_t rank_bits;
    _Static_assert(sizeof(rank)==sizeof(rank_bits),"IEEE binary64 size required");
    memcpy(&rank_bits,&rank,sizeof(rank_bits));
    u64(b,SQLITE_FLOAT); u64(b,rank_bits);
    rows++;
  }
  int extended=sqlite3_extended_errcode(db);
  char *message=strdup(sqlite3_errmsg(db)); if (!message) fail("error copy");
  int final_rc=sqlite3_finalize(s);
  for (int i=0;i<8;i++) b->p[start+i]=(unsigned char)(rows>>(8*i));
  u64(b,(uint64_t)rc); u64(b,(uint64_t)extended); u64(b,(uint64_t)final_rc);
  text(b,message); free(message);
  if (rows>20) fail("limit");
}
int main(int argc,char **argv) {
  if (argc==2 && !strcmp(argv[1],"--identity")) {
    printf("version=%s\nsource_id=%s\nfts5=%d\n",sqlite3_libversion(),sqlite3_sourceid(),
           sqlite3_compileoption_used("ENABLE_FTS5"));
    return 0;
  }
  if (argc!=5) fail("expected DB requests output mode(resident|plain)");
  int resident=!strcmp(argv[4],"resident");
  if (!resident && strcmp(argv[4],"plain")) fail("mode");
  FILE *f=fopen(argv[2],"rb"); if (!f) fail("requests open");
  Request *r=NULL; size_t count=0,cap=0; char *line=NULL; size_t linecap=0;
  while (getline(&line,&linecap,f)>=0) {
    char *q=strchr(line,'\t'); if (!q) fail("request shape"); *q++=0;
    char *m=strchr(q,'\t'); if (!m) fail("request shape"); *m++=0;
    m[strcspn(m,"\r\n")]=0;
    if (!*line || !*q || !*m || strchr(m,'\t')) fail("request shape");
    if (count==cap) { cap=cap?cap*2:64; void *p=realloc(r,cap*sizeof(*r)); if (!p) fail("allocation"); r=p; }
    r[count]=(Request){strdup(line),strdup(q),strdup(m)};
    if (!r[count].id || !r[count].question || !r[count].match) fail("allocation");
    count++;
  }
  free(line); if (ferror(f)||fclose(f)||!count) fail("request inventory");
  sqlite3 *db=NULL;
  char *uri=sqlite3_mprintf("file:%s?immutable=1",argv[1]);
  if (!uri || sqlite3_open_v2(uri,&db,SQLITE_OPEN_READONLY|SQLITE_OPEN_URI,NULL)!=SQLITE_OK) fail("open");
  sqlite3_free(uri);
  if (sqlite3_busy_timeout(db,5000)!=SQLITE_OK || sqlite3_db_readonly(db,"main")!=1) fail("connection settings");
  if (resident) raise(SIGSTOP);
  Buffer b={0}; append(&b,"FTSFEAS1",8); u64(&b,count);
  for (size_t i=0;i<count;i++) query(db,&r[i],&b);
  int close_rc=sqlite3_close(db); u64(&b,(uint64_t)close_rc);
  if (close_rc!=SQLITE_OK) fail("close");
  for (size_t i=0;i<count;i++) { free(r[i].id); free(r[i].question); free(r[i].match); }
  free(r);
  if (resident) raise(SIGSTOP);
  /* All SQL, accessors, encoding, cleanup and close are already finished. */
  f=fopen(argv[3],"wb");
  if (!f || fwrite(b.p,1,b.n,f)!=b.n || fclose(f)) fail("export");
  free(b.p);
  return 0;
}

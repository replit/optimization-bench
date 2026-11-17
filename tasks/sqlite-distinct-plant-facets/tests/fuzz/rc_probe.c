/* Verifier-only SQLite public VFS/SQL observer. No verdicts.
 * stdin: operation fault mmap seed; stdout: one complete typed JSON result.
 * The driver samples /proc at the command boundaries of this persistent worker.
 */
#define _GNU_SOURCE
#include <sqlite3.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <sched.h>

typedef struct File { sqlite3_file base; sqlite3_file *real; } File;
static sqlite3_vfs *parent;
static sqlite3_vfs vfs;
static const char *fault = "healthy";
static int armed, hits, reads, fetches;
static sqlite3_syscall_ptr original_pread, original_pread64;
static int inject(const char *name) {
  if (armed && !strcmp(fault,name)) { hits++; return 1; }
  return 0;
}
static ssize_t faulty_pread(int fd, void *buf, size_t n, off_t off) {
  if (off>=4096 && inject("sys_eio")) { errno=EIO; return -1; }
  if (off>=4096 && inject("sys_short")) return 0;
  return ((ssize_t (*)(int,void*,size_t,off_t))original_pread)(fd,buf,n,off);
}
static ssize_t faulty_pread64(int fd, void *buf, size_t n, off64_t off) {
  if (off>=4096 && inject("sys_eio")) { errno=EIO; return -1; }
  if (off>=4096 && inject("sys_short")) return 0;
  return ((ssize_t (*)(int,void*,size_t,off64_t))original_pread64)(fd,buf,n,off);
}
#define R(f) (((File*)(f))->real)
static int close_file(sqlite3_file *f) { return R(f)->pMethods->xClose(R(f)); }
static int read_file(sqlite3_file *f,void *p,int n,sqlite3_int64 o) {
  if (armed) reads++;
  if (o>=4096 && inject("read_eio")) return SQLITE_IOERR_READ;
  if (o>=4096 && inject("short")) { memset(p,0,n); return SQLITE_IOERR_SHORT_READ; }
  return R(f)->pMethods->xRead(R(f),p,n,o);
}
static int write_file(sqlite3_file *f,const void *p,int n,sqlite3_int64 o) {
  if (inject("full")) return SQLITE_FULL;
  return R(f)->pMethods->xWrite(R(f),p,n,o);
}
static int truncate_file(sqlite3_file *f,sqlite3_int64 n) {
  if (inject("truncate")) return SQLITE_IOERR_TRUNCATE;
  return R(f)->pMethods->xTruncate(R(f),n);
}
static int sync_file(sqlite3_file *f,int flags) {
  if (inject("sync")) return SQLITE_IOERR_FSYNC;
  return R(f)->pMethods->xSync(R(f),flags);
}
static int size_file(sqlite3_file *f,sqlite3_int64 *n) { return R(f)->pMethods->xFileSize(R(f),n); }
static int lock_file(sqlite3_file *f,int n) { return R(f)->pMethods->xLock(R(f),n); }
static int unlock_file(sqlite3_file *f,int n) { return R(f)->pMethods->xUnlock(R(f),n); }
static int reserved(sqlite3_file *f,int *n) { return R(f)->pMethods->xCheckReservedLock(R(f),n); }
static int control(sqlite3_file *f,int n,void *p) { return R(f)->pMethods->xFileControl(R(f),n,p); }
static int sector(sqlite3_file *f) { return R(f)->pMethods->xSectorSize(R(f)); }
static int characteristics(sqlite3_file *f) { return R(f)->pMethods->xDeviceCharacteristics(R(f)); }
static int shmmap(sqlite3_file *f,int a,int b,int c,void volatile **p) { return R(f)->pMethods->xShmMap(R(f),a,b,c,p); }
static int shmlock(sqlite3_file *f,int a,int b,int c) { return R(f)->pMethods->xShmLock(R(f),a,b,c); }
static void shmbarrier(sqlite3_file *f) { R(f)->pMethods->xShmBarrier(R(f)); }
static int shmunmap(sqlite3_file *f,int a) { return R(f)->pMethods->xShmUnmap(R(f),a); }
static int fetch(sqlite3_file *f,sqlite3_int64 o,int n,void **p) {
  if (armed) fetches++;
  /* Public xFetch permits success with no page; fallback must preserve errors. */
  if (armed && (!strcmp(fault,"no_page") || !strncmp(fault,"sys_",4)
                || !strcmp(fault,"short") || !strcmp(fault,"read_eio"))) {
    if (!strcmp(fault,"no_page")) hits++;
    *p=0; return SQLITE_OK;
  }
  return R(f)->pMethods->xFetch(R(f),o,n,p);
}
static int unfetch(sqlite3_file *f,sqlite3_int64 o,void *p) { return R(f)->pMethods->xUnfetch(R(f),o,p); }
static const sqlite3_io_methods methods = {
  3,close_file,read_file,write_file,truncate_file,sync_file,size_file,
  lock_file,unlock_file,reserved,control,sector,characteristics,
  shmmap,shmlock,shmbarrier,shmunmap,fetch,unfetch
};
static int open_file(sqlite3_vfs *v,const char *name,sqlite3_file *f,int flags,int *out) {
  File *w=(File*)f;
  int rc;
  (void)v;
  w->real=(sqlite3_file*)(w+1);
  rc=parent->xOpen(parent,name,w->real,flags,out);
  if (rc==SQLITE_OK) w->base.pMethods=&methods;
  return rc;
}
static void setup_vfs(void) {
  parent=sqlite3_vfs_find(0);
  if (!parent || parent->iVersion<3 || !parent->xSetSystemCall) exit(3);
  vfs=*parent;
  vfs.zName="rc-fault";
  vfs.szOsFile=sizeof(File)+parent->szOsFile;
  vfs.xOpen=open_file;
  if (sqlite3_vfs_register(&vfs,0)!=SQLITE_OK) exit(3);
  original_pread=parent->xGetSystemCall(parent,"pread");
  original_pread64=parent->xGetSystemCall(parent,"pread64");
  if (!original_pread && !original_pread64) exit(3);
  if (original_pread && parent->xSetSystemCall(parent,"pread",(sqlite3_syscall_ptr)faulty_pread)) exit(3);
  if (original_pread64 && parent->xSetSystemCall(parent,"pread64",(sqlite3_syscall_ptr)faulty_pread64)) exit(3);
}
static void text(const unsigned char *s,int n) {
  int i;
  putchar('"');
  for (i=0;i<n;i++) printf("\\u%04x",s[i]);
  putchar('"');
}
/* Preserve column types and complete values; no sqlite3_exec string coercion. */
static int query(sqlite3 *db,const char *sql) {
  sqlite3_stmt *s=0;
  int rc=sqlite3_prepare_v2(db,sql,-1,&s,0), first=1, i;
  putchar('[');
  if (rc==SQLITE_OK) {
    while ((rc=sqlite3_step(s))==SQLITE_ROW) {
      printf("%s[",first?"":","); first=0;
      for (i=0;i<sqlite3_column_count(s);i++) {
        int t=sqlite3_column_type(s,i);
        printf("%s{\"type\":%d,\"value\":",i?",":"",t);
        if (t==SQLITE_INTEGER) printf("%lld",(long long)sqlite3_column_int64(s,i));
        else if (t==SQLITE_FLOAT) {
          char b[64]; snprintf(b,sizeof b,"%a",sqlite3_column_double(s,i));
          text((unsigned char*)b,strlen(b));
        } else if (t==SQLITE_NULL) printf("null");
        else {
          const unsigned char *b=sqlite3_column_blob(s,i);
          text(b,sqlite3_column_bytes(s,i));
        }
        putchar('}');
      }
      putchar(']');
    }
    if (rc==SQLITE_DONE) rc=SQLITE_OK;
  }
  putchar(']');
  sqlite3_finalize(s);
  return rc;
}
static void must(sqlite3 *db,const char *sql) {
  if (sqlite3_exec(db,sql,0,0,0)!=SQLITE_OK) {
    fprintf(stderr,"setup: %s\n",sqlite3_errmsg(db)); exit(3);
  }
}
static void alias(sqlite3 *db,int seed) {
  sqlite3_stmt *s=0;
  sqlite3_value *copy;
  const unsigned char *p;
  unsigned char saved[64];
  int n, stable, rc;
  if (sqlite3_prepare_v2(db,"SELECT ?1, x'000102ff', NULL",-1,&s,0)) exit(3);
  sqlite3_bind_text(s,1,seed?"beta":"alpha",-1,SQLITE_TRANSIENT);
  if (sqlite3_step(s)!=SQLITE_ROW) exit(3);
  p=sqlite3_column_text(s,0); n=sqlite3_column_bytes(s,0);
  memcpy(saved,p,n);
  copy=sqlite3_value_dup(sqlite3_column_value(s,0));
  if (!copy) exit(3);
  stable=memcmp(p,saved,n)==0; /* before reset/conversion only */
  printf("{\"stable_before_reset\":%d,\"copied_text\":",stable);
  text(saved,n);
  p=sqlite3_column_blob(s,1); n=sqlite3_column_bytes(s,1);
  printf(",\"blob\":"); text(p,n);
  sqlite3_reset(s);
  sqlite3_clear_bindings(s);
  sqlite3_bind_int(s,1,seed+47);
  rc=sqlite3_step(s);
  printf(",\"rebound_rc\":%d,\"rebound_type\":%d,\"rebound_value\":%d",
         rc,sqlite3_column_type(s,0),sqlite3_column_int(s,0));
  /* Reacquire after conversion. Never dereference an invalidated old pointer. */
  p=sqlite3_column_text(s,0); n=sqlite3_column_bytes(s,0);
  printf(",\"converted_text\":"); text(p,n);
  sqlite3_finalize(s);
  printf(",\"value_dup_after_finalize\":");
  p=sqlite3_value_text(copy); text(p,sqlite3_value_bytes(copy));
  sqlite3_value_free(copy);
  rc=sqlite3_exec(db,"SELECT no_such_column",0,0,0);
  printf(",\"error_code\":%d,\"errmsg_before_next_call\":",rc);
  p=(const unsigned char*)sqlite3_errmsg(db); text(p,strlen((const char*)p));
  printf("}");
}
static void call(const char *op,const char *mode,int mmap,int seed) {
  sqlite3 *db=0;
  int rc, extended, automatic, close_rc;
  char sql[512], message[512];
  const char *statement;
  fault=mode; armed=0; hits=reads=fetches=0;
  unlink("case.db"); unlink("case.db-journal");
  if (sqlite3_open_v2("case.db",&db,SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE,"rc-fault")) exit(3);
  must(db,"PRAGMA page_size=4096; PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL;"
          "CREATE TABLE plants(id INTEGER PRIMARY KEY, family TEXT, n INTEGER, payload BLOB);"
          "CREATE VIRTUAL TABLE docs USING fts5(body);"
          "INSERT INTO docs VALUES('fern green plant'),('green moss'),('fern plant');");
  snprintf(sql,sizeof sql,
    "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<120)"
    " INSERT INTO plants SELECT x,CASE WHEN x%%2=0 THEN 'fern' ELSE 'moss' END,x+%d,zeroblob(512) FROM n;"
    "DELETE FROM plants WHERE id>40;",seed);
  must(db,sql);
  if (sqlite3_close(db)) exit(3);
  if (sqlite3_open_v2("case.db",&db,SQLITE_OPEN_READWRITE,"rc-fault")) exit(3);
  sqlite3_extended_result_codes(db,1);
  snprintf(sql,sizeof sql,"PRAGMA mmap_size=%d; PRAGMA cache_size=2;",mmap?1048576:0);
  must(db,sql);
  printf("{\"result\":");
  if (!strcmp(op,"alias")) {
    alias(db,seed); rc=0; extended=0; strcpy(message,"not an error"); automatic=sqlite3_get_autocommit(db);
  } else {
    if (!strcmp(op,"facets")) statement="SELECT family,count(DISTINCT n),min(n),max(n) FROM plants GROUP BY family ORDER BY family";
    else if (!strcmp(op,"search")) statement="SELECT rowid,body,bm25(docs) FROM docs WHERE docs MATCH 'fern' ORDER BY bm25(docs),rowid";
    else if (!strcmp(op,"upsert")) statement="INSERT INTO plants VALUES(1,'rose',999,x'0001') ON CONFLICT(id) DO UPDATE SET family=excluded.family,n=excluded.n,payload=excluded.payload";
    else statement="VACUUM";
    if (!strcmp(op,"upsert")) must(db,"BEGIN");
    armed=1;
    rc=query(db,statement);
    if (!rc && !strcmp(op,"upsert")) rc=sqlite3_exec(db,"COMMIT",0,0,0);
    extended=sqlite3_extended_errcode(db);
    snprintf(message,sizeof message,"%s",sqlite3_errmsg(db));
    automatic=sqlite3_get_autocommit(db);
    armed=0;
  }
  printf(",\"code\":%d,\"extended_code\":%d,\"message\":",rc,extended);
  text((unsigned char*)message,strlen(message));
  printf(",\"autocommit_after_call\":%d",automatic);
  rc=sqlite3_get_autocommit(db)?SQLITE_OK:sqlite3_exec(db,"ROLLBACK",0,0,0);
  printf(",\"rollback_code\":%d,\"same_connection_integrity\":",rc);
  rc=query(db,"PRAGMA integrity_check");
  printf(",\"same_connection_integrity_code\":%d",rc);
  close_rc=sqlite3_close(db);
  printf(",\"recovery_close_code\":%d",close_rc);
  if (sqlite3_open_v2("case.db",&db,SQLITE_OPEN_READWRITE,"rc-fault")) exit(3);
  printf(",\"integrity\":");
  rc=query(db,"PRAGMA integrity_check");
  printf(",\"integrity_code\":%d,\"contents\":",rc);
  rc=query(db,"SELECT id,family,n,length(payload) FROM plants ORDER BY id");
  close_rc=sqlite3_close(db);
  printf(",\"contents_code\":%d,\"close_code\":%d,\"hits\":%d,\"reads\":%d,\"fetches\":%d,"
         "\"pid\":%d,\"execution_affinity\":[",rc,close_rc,hits,reads,fetches,(int)getpid());
  {
    cpu_set_t set; int i, first=1;
    CPU_ZERO(&set);
    if (sched_getaffinity(0,sizeof set,&set)) exit(3);
    for (i=0;i<CPU_SETSIZE;i++) if (CPU_ISSET(i,&set)) {
      printf("%s%d",first?"":",",i); first=0;
    }
  }
  printf("]}\n"); fflush(stdout);
}
int main(void) {
  char op[32], mode[32]; int mmap,seed;
  setup_vfs();
  puts("ready"); fflush(stdout);
  while (scanf("%31s %31s %d %d",op,mode,&mmap,&seed)==4) call(op,mode,mmap,seed);
  return 0;
}

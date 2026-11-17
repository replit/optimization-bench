#define _POSIX_C_SOURCE 200809L
#include "sqlite3.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define REQUIRE(x) do { if(!(x)){fprintf(stderr,"requirement failed line %d\n",__LINE__);exit(2);} }while(0)
typedef struct { sqlite3_value *value[5]; } Row;

static sqlite3 *open_database(const char *path){
  sqlite3 *db=0;
  REQUIRE(sqlite3_open_v2(path,&db,SQLITE_OPEN_READWRITE,0)==SQLITE_OK);
  REQUIRE(sqlite3_extended_result_codes(db,1)==SQLITE_OK);
  REQUIRE(sqlite3_exec(db,"PRAGMA synchronous=FULL",0,0,0)==SQLITE_OK);
  return db;
}
static void execsql(sqlite3 *db,const char *sql){
  REQUIRE(sqlite3_exec(db,sql,0,0,0)==SQLITE_OK);
}
static Row *load(sqlite3 *db,int *count){
  sqlite3_stmt *st=0;
  REQUIRE(sqlite3_prepare_v2(db,"SELECT record_id,source_tag,source_ordinal,record_json,retain FROM mirror ORDER BY record_id",-1,&st,0)==SQLITE_OK);
  Row *rows=0;
  int capacity=0,n=0,rc;
  while((rc=sqlite3_step(st))==SQLITE_ROW){
    if(n==capacity){
      capacity=capacity?capacity*2:1024;
      Row *next=realloc(rows,(size_t)capacity*sizeof(*rows));
      REQUIRE(next!=0);
      rows=next;
    }
    for(int c=0;c<5;c++){
      rows[n].value[c]=sqlite3_value_dup(sqlite3_column_value(st,c));
      REQUIRE(rows[n].value[c]!=0);
    }
    n++;
  }
  REQUIRE(rc==SQLITE_DONE);
  REQUIRE(sqlite3_finalize(st)==SQLITE_OK);
  *count=n;
  return rows;
}
static void upsert(sqlite3 *db,Row *rows,int n){
  REQUIRE(n>0 && n<=100);
  sqlite3_str *sql=sqlite3_str_new(db);
  REQUIRE(sql!=0);
  sqlite3_str_appendall(sql,"INSERT INTO mirror(record_id,source_tag,source_ordinal,record_json,retain) VALUES ");
  for(int i=0;i<n;i++) sqlite3_str_appendall(sql,i?",(?,?,?,?,?)":"(?,?,?,?,?)");
  sqlite3_str_appendall(sql," ON CONFLICT(record_id) DO UPDATE SET source_tag=excluded.source_tag,source_ordinal=excluded.source_ordinal,record_json=excluded.record_json,retain=excluded.retain");
  char *text=sqlite3_str_finish(sql);
  REQUIRE(text!=0);
  sqlite3_stmt *st=0;
  REQUIRE(sqlite3_prepare_v2(db,text,-1,&st,0)==SQLITE_OK);
  sqlite3_free(text);
  for(int i=0;i<n;i++) for(int c=0;c<5;c++)
    REQUIRE(sqlite3_bind_value(st,i*5+c+1,rows[i].value[c])==SQLITE_OK);
  REQUIRE(sqlite3_step(st)==SQLITE_DONE);
  REQUIRE(sqlite3_changes(db)==n);
  REQUIRE(sqlite3_finalize(st)==SQLITE_OK);
}
int main(int argc,char **argv){
  REQUIRE(argc==6);
  const char *mode=argv[1],*input=argv[2],*target=argv[3],*schema=argv[4];
  REQUIRE(strcmp(sqlite3_libversion(),"3.54.1")==0);
  sqlite3 *db=open_database(target);
  if(strcmp(mode,"upsert")==0){
    sqlite3 *source=open_database(input);
    int count=0;
    Row *rows=load(source,&count);
    REQUIRE(sqlite3_close(source)==SQLITE_OK);
    /* Target is created and independently seeded by the controller outside the operation. */
    REQUIRE(schema[0]!=0);
    REQUIRE(raise(SIGSTOP)==0);
    execsql(db,"BEGIN IMMEDIATE");
    for(int offset=0;offset<count;offset+=100){
      int n=count-offset;
      if(n>100) n=100;
      upsert(db,rows+offset,n);
    }
    execsql(db,"COMMIT");
    REQUIRE(raise(SIGSTOP)==0);
    for(int i=0;i<count;i++) for(int c=0;c<5;c++) sqlite3_value_free(rows[i].value[c]);
    free(rows);
  }else{
    REQUIRE(strcmp(mode,"vacuum")==0);
    REQUIRE(raise(SIGSTOP)==0);
    sqlite3_stmt *st=0;
    REQUIRE(sqlite3_prepare_v2(db,"VACUUM main",-1,&st,0)==SQLITE_OK);
    REQUIRE(sqlite3_step(st)==SQLITE_DONE);
    REQUIRE(sqlite3_finalize(st)==SQLITE_OK);
    REQUIRE(raise(SIGSTOP)==0);
  }
  REQUIRE(sqlite3_get_autocommit(db)==1);
  REQUIRE(sqlite3_close(db)==SQLITE_OK);
  REQUIRE(sqlite3_shutdown()==SQLITE_OK);
  REQUIRE(sqlite3_memory_used()==0);
  FILE *receipt=fopen(argv[5],"wb");
  REQUIRE(receipt!=0);
  REQUIRE(fwrite("SQLALT1\n",1,8,receipt)==8);
  REQUIRE(fclose(receipt)==0);
  return 0;
}

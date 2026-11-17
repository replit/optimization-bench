#define _GNU_SOURCE
#include <jq.h>
#include <jv.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <limits.h>
#include <sys/stat.h>

typedef struct { char *bytes; size_t size; int error; } result;
static char *readall(const char *path, size_t *len) {
  FILE *f = fopen(path, "rb");
  if (!f) { perror(path); exit(2); }
  if (fseek(f, 0, SEEK_END) || ftell(f) < 0) exit(2);
  *len = (size_t)ftell(f);
  rewind(f);
  char *p = malloc(*len + 1);
  if (!p || fread(p, 1, *len, f) != *len || fclose(f)) exit(2);
  p[*len] = 0;
  return p;
}

static void error_cb(void *p, jv error) {
  jv *err = p;
  jv msg = jq_format_error(error);
  *err = jv_string_concat(*err, msg);
  *err = jv_string_append_str(*err, "\n");
}

/* Consume borrowed input bytes; return owned output/error bytes. */
result library_operation(const char *input, size_t size, const char *filter) {
  result r = {0};
  jv errors = jv_string("");
  jv output = jv_string("");
  jq_state *jq = jq_init();
  if (!jq) abort();
  jq_set_error_cb(jq, error_cb, &errors);
  if (!jq_compile(jq, filter)) {
    r.error = 1;
  } else {
    jv value = jv_parse_sized(input, (int)size);
    if (!jv_is_valid(value)) {
      error_cb(&errors, jv_invalid_get_msg(value));
      r.error = 1;
    } else {
      jq_start(jq, value, 0); /* ownership transferred to jq */
      for (;;) {
        jv value_out = jq_next(jq);
        if (!jv_is_valid(value_out)) {
          if (jv_invalid_has_msg(jv_copy(value_out))) {
            error_cb(&errors, jv_invalid_get_msg(value_out));
            r.error = 1;
          } else jv_free(value_out);
          break;
        }
        jv encoded = jv_dump_string(value_out, 0); /* consumes value_out */
        output = jv_string_concat(output, encoded);
        output = jv_string_append_str(output, "\n");
      }
    }
  }
  jq_teardown(&jq);
  jv selected = r.error ? jv_copy(errors) : jv_copy(output);
  r.size = (size_t)jv_string_length_bytes(jv_copy(selected));
  r.bytes = malloc(r.size ? r.size : 1);
  if (!r.bytes) abort();
  memcpy(r.bytes, jv_string_value(selected), r.size);
  jv_free(selected);
  jv_free(output);
  jv_free(errors);
  return r;
}



int main(int argc,char **argv) {
 if(argc!=2)return 2;
 FILE *rows=fopen(argv[1],"r");if(!rows)return 2;
 char *line=NULL;size_t capacity=0;
 while(getline(&line,&capacity,rows)>0) {
  char *id=strtok(line,"\t"),*path=strtok(NULL,"\n");if(!id||!path)return 2;
  size_t n;char *input=readall(path,&n);
  result r=library_operation(input,n,"walk(if type == \"object\" then del(.description) else . end)");
  char target[512];snprintf(target,sizeof(target),"%s.%s",id,r.error?"err":"out");
  FILE *f=fopen(target,"wb");if(!f||fwrite(r.bytes,1,r.size,f)!=r.size||fclose(f))return 2;
  free(input);free(r.bytes);
 }
 free(line);if(ferror(rows)||fclose(rows))return 2;return 0;
}

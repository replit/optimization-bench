#include <bzlib.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int checks;
static void check(int value, const char *name) {
    if (!value) { fprintf(stderr, "API guard failed: %s\n", name); exit(1); }
    checks++;
}
/* A legal single stream with an explicit mid-stream flush, for semantic-checker tests. */
static void flushed(const char *file) {
    bz_stream s = {0}; char raw[20000], out[50000];
    for (unsigned i = 0; i < sizeof(raw); ++i) raw[i] = (char)((i * 13u + i / 117u) % 251u);
    check(BZ2_bzCompressInit(&s, 9, 0, 30) == BZ_OK, "flush init");
    s.next_in = raw; s.avail_in = sizeof(raw) / 2; s.next_out = out; s.avail_out = sizeof(out);
    int r;
    do { r = BZ2_bzCompress(&s, BZ_FLUSH); } while (r == BZ_FLUSH_OK);
    check(r == BZ_RUN_OK, "flush transition");
    s.next_in = raw + sizeof(raw) / 2; s.avail_in = sizeof(raw) / 2;
    do { r = BZ2_bzCompress(&s, BZ_FINISH); } while (r == BZ_FINISH_OK);
    check(r == BZ_STREAM_END, "flush finish");
    unsigned n = sizeof(out) - s.avail_out;
    check(BZ2_bzCompressEnd(&s) == BZ_OK, "flush cleanup");
    FILE *f = fopen(file, "wb"); check(f != NULL, "flush file");
    check(fwrite(out, 1, n, f) == n, "flush write"); check(fclose(f) == 0, "flush close");
}
typedef struct { unsigned active; int fail; } AllocState;
static void *custom_alloc(void *opaque, int items, int size) {
    AllocState *state = opaque;
    if (state->fail) return NULL;
    void *p = calloc((size_t)items, (size_t)size);
    if (p) state->active++;
    return p;
}
static void custom_free(void *opaque, void *p) {
    AllocState *state = opaque;
    if (p) { check(state->active > 0, "callback free ownership"); state->active--; }
    free(p);
}
static void callback_checks(void) {
    for (int encode=0; encode<=1; encode++) {
        AllocState owner={0,0}; bz_stream s={0};
        s.bzalloc=custom_alloc; s.bzfree=custom_free; s.opaque=&owner;
        int status=encode ? BZ2_bzCompressInit(&s,9,0,30) : BZ2_bzDecompressInit(&s,0,1);
        check(status==BZ_OK,"custom allocator init");
        check((encode ? BZ2_bzCompressEnd(&s) : BZ2_bzDecompressEnd(&s))==BZ_OK,"custom allocator end");
        check(owner.active==0,"custom allocations released");
        owner.fail=1; memset(&s,0,sizeof(s));
        s.bzalloc=custom_alloc; s.bzfree=custom_free; s.opaque=&owner;
        status=encode ? BZ2_bzCompressInit(&s,9,0,30) : BZ2_bzDecompressInit(&s,0,0);
        check(status==BZ_MEM_ERROR,"allocation failure status");
        check(owner.active==0,"failed init ownership");
    }
    for (int level=1; level<=9;level++) {
        bz_stream s={0};
        check(BZ2_bzCompressInit(&s,level,0,0)==BZ_OK,"all documented block settings");
        check(BZ2_bzCompressEnd(&s)==BZ_OK,"init/end without data");
    }
}
int main(int argc, char **argv) {
    if (argc == 3 && !strcmp(argv[1], "flush")) { flushed(argv[2]); return 0; }
    bz_stream s = {0};
    check(BZ2_bzCompressInit(NULL, 9, 0, 30) == BZ_PARAM_ERROR, "null compress init");
    check(BZ2_bzCompressInit(&s, 0, 0, 30) == BZ_PARAM_ERROR, "block zero");
    check(BZ2_bzCompressInit(&s, 10, 0, 30) == BZ_PARAM_ERROR, "block ten");
    check(BZ2_bzCompressInit(&s, 9, 0, -1) == BZ_PARAM_ERROR, "negative work factor");
    check(BZ2_bzCompressInit(&s, 9, 0, 251) == BZ_PARAM_ERROR, "large work factor");
    check(BZ2_bzDecompressInit(NULL, 0, 0) == BZ_PARAM_ERROR, "null decode init");
    check(BZ2_bzDecompressInit(&s, 0, 2) == BZ_PARAM_ERROR, "invalid small");
    check(BZ2_bzDecompressInit(&s, 5, 0) == BZ_PARAM_ERROR, "invalid verbosity");
    check(BZ2_bzCompressEnd(&s) == BZ_PARAM_ERROR, "uninitialized compress end");
    check(BZ2_bzDecompressEnd(&s) == BZ_PARAM_ERROR, "uninitialized decode end");
    char encoded[1000], decoded[1000], text[] = "alpha alpha beta beta beta";
    unsigned n = sizeof(encoded), d = sizeof(decoded);
    check(BZ2_bzBuffToBuffCompress(encoded, &n, text, sizeof(text), 9, 0, 30) == BZ_OK, "buffer encode");
    unsigned small_output = 1;
    check(BZ2_bzBuffToBuffDecompress(decoded, &small_output, encoded, n, 0, 0) == BZ_OUTBUFF_FULL, "output shortage");
    for (int small = 0; small <= 1; ++small) {
        d = sizeof(decoded);
        check(BZ2_bzBuffToBuffDecompress(decoded, &d, encoded, n, small, 0) == BZ_OK, "buffer decode");
        check(d == sizeof(text) && !memcmp(decoded, text, d), "decoded bytes");
    }
    check(BZ2_bzCompressInit(&s, 9, 0, 30) == BZ_OK, "stream init");
    s.next_in = text; s.avail_in = sizeof(text); s.next_out = encoded; s.avail_out = sizeof(encoded);
    check(BZ2_bzCompress(&s, 99) == BZ_PARAM_ERROR, "invalid action");
    check(BZ2_bzCompress(&s, BZ_FINISH) == BZ_STREAM_END, "stream finish");
    check(BZ2_bzCompress(&s, BZ_RUN) == BZ_SEQUENCE_ERROR, "run after finish");
    check(BZ2_bzCompressEnd(&s) == BZ_OK, "stream cleanup");
    check(BZ2_bzCompressEnd(&s) == BZ_PARAM_ERROR, "double end");
    callback_checks();
    printf("{\"checks\":%d,\"status\":\"passed\"}\n", checks);
    return 0;
}

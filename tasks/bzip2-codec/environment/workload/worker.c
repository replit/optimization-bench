#define _POSIX_C_SOURCE 200809L
#include <bzlib.h>
#include <errno.h>
#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#define MAX_BYTES (16u * 1024u * 1024u)
#define TRAILING_DATA (-100)

static void die(const char *s) { fprintf(stderr, "%s\n", s); exit(2); }
static int number(const char *s, int lo, int hi) {
    char *end; errno = 0; long n = strtol(s, &end, 10);
    if (errno || !*s || *end || n < lo || n > hi) die("Bad numeric parameter");
    return (int)n;
}
static int safe(const char *s) {
    if (!*s || *s == '/' || strstr(s, "..")) return 0;
    for (; *s; ++s) if (!strchr("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_.-", *s)) return 0;
    return 1;
}
/* A correctness-only request can bound library-requested bytes, not process RSS.
   The original timed path retains the default allocator. Headers are judge
   bookkeeping; all callback allocations must be returned before End completes. */
typedef struct { size_t limit, live; } AllocationLimit;
typedef union { max_align_t alignment; size_t bytes; } AllocationHeader;
static void *limited_alloc(void *opaque, int items, int size) {
    AllocationLimit *cap = opaque;
    if (items <= 0 || size <= 0 ||
        (size_t)items > (SIZE_MAX - sizeof(AllocationHeader)) / (size_t)size) return NULL;
    size_t bytes = (size_t)items * (size_t)size;
    if (bytes > cap->limit - cap->live) return NULL;
    AllocationHeader *header = malloc(sizeof(*header) + bytes);
    if (!header) return NULL;
    header->bytes = bytes;
    cap->live += bytes;
    return header + 1;
}
static void limited_free(void *opaque, void *address) {
    if (!address) return;
    AllocationLimit *cap = opaque;
    AllocationHeader *header = (AllocationHeader *)address - 1;
    if (header->bytes > cap->live) die("Invalid allocator ownership");
    cap->live -= header->bytes;
    free(header);
}
static int codec(FILE *in, FILE *out, int encode, int level, int small, unsigned chunk) {
    bz_stream s; memset(&s, 0, sizeof(s));
    AllocationLimit cap = {0, 0};
    const char *limit = getenv("BZIP2_ALLOCATION_LIMIT");
    if (limit && !encode) {
        cap.limit = (size_t)number(limit, 1, INT_MAX);
        s.bzalloc = limited_alloc; s.bzfree = limited_free; s.opaque = &cap;
    }
    int code = encode ? BZ2_bzCompressInit(&s, level, 0, 30) : BZ2_bzDecompressInit(&s, 0, small);
    if (code != BZ_OK) {
        if (cap.live) die("Initializer leaked callback allocations");
        return code;
    }
    char *ib = malloc(chunk), *ob = malloc(chunk);
    if (!ib || !ob) die("I/O buffer allocation failed");
    unsigned long long input = 0, output = 0;
    int eof = 0;
    for (;;) {
        if (s.avail_in == 0 && !eof) {
            size_t n = fread(ib, 1, chunk, in);
            if (ferror(in)) die("Input read failed");
            s.next_in = ib; s.avail_in = (unsigned)n;
            input += n;
            if (input > MAX_BYTES) die("Input exceeds prototype limit");
            eof = n < chunk;
        }
        unsigned before = s.avail_in;
        s.next_out = ob; s.avail_out = chunk;
        code = encode ? BZ2_bzCompress(&s, eof ? BZ_FINISH : BZ_RUN) : BZ2_bzDecompress(&s);
        unsigned produced = chunk - s.avail_out;
        output += produced;
        if (output > MAX_BYTES) { code = BZ_OUTBUFF_FULL; break; }
        if (produced && fwrite(ob, 1, produced, out) != produced) die("Output write failed");
        if (code == BZ_STREAM_END) {
            code = BZ_OK;
            if (!encode) {
                if (s.avail_in || fgetc(in) != EOF) code = TRAILING_DATA;
                if (ferror(in)) die("Trailing input read failed");
            }
            break;
        }
        if (code < 0) break;
        if (encode && code != BZ_RUN_OK && code != BZ_FINISH_OK) die("Unexpected compression status");
        if (!encode && code != BZ_OK) die("Unexpected decompression status");
        if (eof && produced == 0 && before == s.avail_in) {
            code = encode ? BZ_SEQUENCE_ERROR : BZ_UNEXPECTED_EOF;
            break;
        }
    }
    int end = encode ? BZ2_bzCompressEnd(&s) : BZ2_bzDecompressEnd(&s);
    if (end != BZ_OK) die("Codec teardown failed");
    if (cap.live) die("Codec leaked callback allocations");
    free(ib); free(ob);
    return code;
}
static void operation(const char *mode, const char *input, const char *base, int level, int small, unsigned chunk) {
    int encode = !strcmp(mode, "encode");
    if (!encode && strcmp(mode, "decode")) die("Unknown codec operation");
    if (level != 1 && level != 9) die("Undeclared block-size setting");
    struct stat info;
    if (stat(input, &info) || !S_ISREG(info.st_mode) || info.st_size < 0 || info.st_size > MAX_BYTES) die("Invalid input");
    char file[4096], status[4096];
    if (snprintf(file, sizeof(file), "%s.bin", base) >= (int)sizeof(file) ||
        snprintf(status, sizeof(status), "%s.status.json", base) >= (int)sizeof(status)) die("Output path too long");
    FILE *in = fopen(input, "rb"), *out = fopen(file, "wb");
    if (!in || !out) die("Cannot open operation files");
    int code = codec(in, out, encode, level, small, chunk);
    if (fclose(in) || fclose(out)) die("Operation close failed");
    /* Discard partial data after an error; error output has an explicit stable shape. */
    if (code != BZ_OK) { out = fopen(file, "wb"); if (!out || fclose(out)) die("Cannot clear partial output"); }
    out = fopen(status, "wb"); if (!out) die("Cannot write status");
    if (fprintf(out, "{\"status\":%d}\n", code) < 0 || fclose(out)) die("Status write failed");
}
static void batch(const char *name) {
    const char *input_root = getenv("BZIP2_INPUT_ROOT"), *output_root = getenv("BZIP2_OUTPUT_ROOT");
    if (!input_root) input_root = "/input";
    if (!output_root) output_root = "/output";
    if (!safe(name)) die("Unsafe batch path");
    char path[4096];
    if (snprintf(path, sizeof(path), "%s/%s", input_root, name) >= (int)sizeof(path)) die("Batch path too long");
    FILE *f = fopen(path, "r"); if (!f) die("Missing batch");
    char *line = NULL; size_t capacity = 0;
    while (getline(&line, &capacity, f) >= 0) {
        line[strcspn(line, "\r\n")] = 0;
        char *fields[6], *p = line;
        for (int i = 0; i < 6; ++i) {
            fields[i] = p;
            char *tab = strchr(p, '\t');
            if ((i < 5 && !tab) || (i == 5 && tab)) die("Bad batch row");
            if (tab) { *tab = 0; p = tab + 1; }
        }
        if (!safe(fields[0]) || strchr(fields[0], '/') || !safe(fields[2])) die("Unsafe request path");
        char input[4096], base[4096];
        if (snprintf(input, sizeof(input), "%s/%s", input_root, fields[2]) >= (int)sizeof(input) ||
            snprintf(base, sizeof(base), "%s/%s", output_root, fields[0]) >= (int)sizeof(base)) die("Request path too long");
        operation(fields[1], input, base, number(fields[3], 1, 9), number(fields[4], 0, 1),
                  (unsigned)number(fields[5], 1, 65536));
    }
    free(line); if (ferror(f) || fclose(f)) die("Batch read failed");
}
/* Controller messages and names are generated by the protected evaluator. */
static int field(const char *line, const char *key, char *value, size_t capacity) {
    char pattern[80]; snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    const char *p = strstr(line, pattern); if (!p) return 0;
    p = strchr(p + strlen(pattern), ':'); if (!p) return 0;
    p = strchr(p + 1, '"'); if (!p) return 0;
    const char *end = strchr(++p, '"');
    if (!end || (size_t)(end - p) >= capacity) return 0;
    memcpy(value, p, (size_t)(end - p)); value[end - p] = 0;
    return 1;
}
int main(int argc, char **argv) {
    if (argc == 9 && !strcmp(argv[1], "one")) {
        operation(argv[2], argv[3], argv[4], number(argv[5], 1, 9), number(argv[6], 0, 1),
                  (unsigned)number(argv[7], 1, 65536));
        if (strcmp(argv[8], "v1")) die("Wrong operation protocol");
        return 0;
    }
    if (argc == 3 && !strcmp(argv[1], "once")) { batch(argv[2]); return 0; }
    if (argc != 2 || strcmp(argv[1], "persistent")) die("Expected one, once or persistent mode");
    puts("{\"ready\":true}"); fflush(stdout);
    char *line = NULL; size_t capacity = 0;
    while (getline(&line, &capacity, stdin) >= 0) {
        char op[32], name[1024];
        if (!field(line, "op", op, sizeof(op))) die("Bad control message");
        if (!strcmp(op, "quit")) break;
        if (strcmp(op, "batch") || !field(line, "batch", name, sizeof(name))) die("Bad batch command");
        batch(name);
        printf("{\"done\":\"%s\"}\n", name); fflush(stdout);
    }
    free(line);
    return 0;
}

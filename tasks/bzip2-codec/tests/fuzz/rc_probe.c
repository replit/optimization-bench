/* Verifier-owned bzip2 public-API observer for RC library gates.
 *
 * One JSON line per call on stdout. Complete output bodies are written to
 * OUT_DIR/call-<n>.bin and hashed by the trusted Python driver; the probe never
 * decides a verdict. Each line carries the actual sched_getaffinity CPU set and
 * per-call /proc lifecycle snapshots (fds, live threads with identities,
 * children). Usage: rc_probe CASES_FILE OUT_DIR
 *
 * Case line: ID KIND PATH P1 P2 P3 P4
 *   stream     PATH blocksize workfactor in_chunk out_chunk   compress+decompress round trip
 *   sdecomp    PATH small in_chunk out_chunk -                streaming decompress (may fail)
 *   bufcomp    PATH blocksize workfactor dest_capacity -      BZ2_bzBuffToBuffCompress
 *   bufdecomp  PATH small dest_capacity - -                   BZ2_bzBuffToBuffDecompress
 *   param      -    blocksize verbosity workfactor -          BZ2_bzCompressInit validation
 *   fwrite     PATH blocksize workfactor TARGET -             BZ2_bzWrite* to fopen(TARGET,"wb")
 *   fread      PATH small - MODE -                            BZ2_bzRead* (MODE r or w)
 *   unused     PATH small - - -                               BZ2_bzReadGetUnused multi-stream chain
 *   errstr     PATH - - - -                                   BZ2_bzerror after reading PATH
 */
#define _GNU_SOURCE
#include <bzlib.h>
#include <dirent.h>
#include <errno.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static const char *bz_name(int code) {
    switch (code) {
    case BZ_OK: return "BZ_OK";
    case BZ_RUN_OK: return "BZ_RUN_OK";
    case BZ_FLUSH_OK: return "BZ_FLUSH_OK";
    case BZ_FINISH_OK: return "BZ_FINISH_OK";
    case BZ_STREAM_END: return "BZ_STREAM_END";
    case BZ_SEQUENCE_ERROR: return "BZ_SEQUENCE_ERROR";
    case BZ_PARAM_ERROR: return "BZ_PARAM_ERROR";
    case BZ_MEM_ERROR: return "BZ_MEM_ERROR";
    case BZ_DATA_ERROR: return "BZ_DATA_ERROR";
    case BZ_DATA_ERROR_MAGIC: return "BZ_DATA_ERROR_MAGIC";
    case BZ_IO_ERROR: return "BZ_IO_ERROR";
    case BZ_UNEXPECTED_EOF: return "BZ_UNEXPECTED_EOF";
    case BZ_OUTBUFF_FULL: return "BZ_OUTBUFF_FULL";
    case BZ_CONFIG_ERROR: return "BZ_CONFIG_ERROR";
    default: return "BZ_UNKNOWN";
    }
}

static unsigned char *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    size_t cap = 1 << 16, n = 0;
    unsigned char *data = malloc(cap);
    for (;;) {
        if (n == cap) data = realloc(data, cap *= 2);
        size_t got = fread(data + n, 1, cap - n, f);
        n += got;
        if (got == 0) break;
    }
    fclose(f);
    *size = n;
    return data;
}

static void dump(const char *path, const unsigned char *data, size_t size) {
    FILE *f = fopen(path, "wb");
    if (!f || (size && fwrite(data, 1, size, f) != size) || fclose(f)) {
        fprintf(stderr, "observer cannot retain output %s\n", path);
        exit(3);
    }
}

/* ---- lifecycle and affinity (observation diagnostics) ---- */
static void affinity(void) {
    cpu_set_t set;
    CPU_ZERO(&set);
    printf("\"execution_affinity\":[");
    if (sched_getaffinity(0, sizeof set, &set) == 0) {
        int first = 1;
        for (int cpu = 0; cpu < CPU_SETSIZE; ++cpu)
            if (CPU_ISSET(cpu, &set)) { printf(first ? "%d" : ",%d", cpu); first = 0; }
    }
    printf("]");
}

static int count_dir(const char *path) {
    DIR *d = opendir(path);
    if (!d) return -1;
    int n = 0;
    struct dirent *e;
    while ((e = readdir(d)))
        if (e->d_name[0] != '.') ++n;
    closedir(d);
    return n - 1; /* exclude the directory stream's own descriptor when counting fds */
}

static void snapshot(const char *label) {
    char path[512], buf[4096];
    int fds = count_dir("/proc/self/fd");
    int children = 0, threads = 0;
    DIR *d = opendir("/proc/self/task");
    printf("\"%s\":{\"fds\":%d,\"thread_identities\":[", label, fds);
    struct dirent *e;
    while (d && (e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        snprintf(path, sizeof path, "/proc/self/task/%s/stat", e->d_name);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        size_t n = fread(buf, 1, sizeof buf - 1, f);
        fclose(f);
        buf[n] = 0;
        char *close = strrchr(buf, ')');
        if (!close) continue;
        char state = 0;
        unsigned long long fields[20] = {0};
        /* after ")": state(3) ppid pgrp session tty_nr tpgid flags(9) ... starttime(22) */
        char *p = close + 2;
        state = *p;
        p += 2;
        for (int i = 0; i < 19 && p; ++i) {
            fields[i] = strtoull(p, &p, 10);
            while (*p == ' ') ++p;
        }
        unsigned long long flags = fields[5], start = fields[18];
        printf("%s{\"tid\":%s,\"start_ticks\":%llu,\"state\":\"%c\",\"flags\":%llu,\"scope_pid\":%d}",
               threads ? "," : "", e->d_name, start, state, flags, (int)getpid());
        ++threads;
        snprintf(path, sizeof path, "/proc/self/task/%s/children", e->d_name);
        f = fopen(path, "r");
        if (f) {
            int c;
            int in = 0;
            while ((c = fgetc(f)) != EOF) {
                if (c >= '0' && c <= '9') { if (!in) ++children; in = 1; } else in = 0;
            }
            fclose(f);
        }
    }
    if (d) closedir(d);
    printf("],\"threads\":%d,\"children\":%d}", threads, children);
}

/* ---- operations ---- */
typedef struct {
    int code;
    const char *stage;
    int err;          /* errno, or -1 when not an OS error */
    const char *op;   /* reported OS operation, or NULL */
    long long a, b;   /* operation-specific counters */
    unsigned char *out;
    size_t out_size;
    int canary_ok;
} Result;

static void canary_check(const unsigned char *buf, size_t cap, size_t pad, Result *r) {
    for (size_t i = 0; i < pad; ++i)
        if (buf[cap + i] != 0xA5) { r->canary_ok = 0; return; }
}

static void stream(const unsigned char *in, size_t n, int bs, int wf, int ichunk, int ochunk, Result *r) {
    bz_stream s;
    memset(&s, 0, sizeof s);
    size_t cap = n + n / 100 + 600 + 64, used = 0, pos = 0;
    unsigned char *comp = malloc(cap + 16);
    memset(comp, 0xA5, cap + 16);
    r->canary_ok = 1;
    int ret = BZ2_bzCompressInit(&s, bs, 0, wf);
    if (ret != BZ_OK) { r->code = ret; r->stage = "compress_init"; return; }
    int action = BZ_RUN;
    for (;;) {
        unsigned int give = (unsigned int)((n - pos) < (size_t)ichunk ? (n - pos) : (size_t)ichunk);
        if (pos == n) action = BZ_FINISH;
        unsigned int room = (unsigned int)((cap - used) < (size_t)ochunk ? (cap - used) : (size_t)ochunk);
        s.next_in = (char *)in + pos; s.avail_in = give;
        s.next_out = (char *)comp + used; s.avail_out = room;
        ret = BZ2_bzCompress(&s, action);
        pos += give - s.avail_in;
        used += room - s.avail_out;
        if (ret == BZ_STREAM_END) break;
        if (ret != BZ_RUN_OK && ret != BZ_FINISH_OK) { r->code = ret; r->stage = "compress"; BZ2_bzCompressEnd(&s); return; }
        if (used + 1 >= cap) { r->code = BZ_OUTBUFF_FULL; r->stage = "compress"; BZ2_bzCompressEnd(&s); return; }
    }
    canary_check(comp, cap, 16, r);
    r->a = ((long long)s.total_in_hi32 << 32) | s.total_in_lo32;
    BZ2_bzCompressEnd(&s);
    /* Streaming decompress of the produced stream with the same chunking. */
    bz_stream d;
    memset(&d, 0, sizeof d);
    size_t dcap = n + 16, dused = 0, dpos = 0;
    unsigned char *plain = malloc(dcap + 16);
    memset(plain, 0xA5, dcap + 16);
    ret = BZ2_bzDecompressInit(&d, 0, 0);
    for (;;) {
        unsigned int give = (unsigned int)((used - dpos) < (size_t)ichunk ? (used - dpos) : (size_t)ichunk);
        unsigned int room = (unsigned int)((dcap - dused) < (size_t)ochunk ? (dcap - dused) : (size_t)ochunk);
        d.next_in = (char *)comp + dpos; d.avail_in = give;
        d.next_out = (char *)plain + dused; d.avail_out = room;
        ret = BZ2_bzDecompress(&d);
        dpos += give - d.avail_in;
        dused += room - d.avail_out;
        if (ret == BZ_STREAM_END) break;
        if (ret != BZ_OK) { r->code = ret; r->stage = "decompress"; BZ2_bzDecompressEnd(&d); return; }
        if (dpos == used && give == 0 && room == d.avail_out) { r->code = BZ_UNEXPECTED_EOF; r->stage = "decompress"; BZ2_bzDecompressEnd(&d); return; }
    }
    canary_check(plain, dcap, 16, r);
    BZ2_bzDecompressEnd(&d);
    r->code = BZ_OK; r->stage = "complete";
    r->b = (long long)(dused == n && memcmp(plain, in, n) == 0);
    r->out = comp; r->out_size = used;
    free(plain);
}

static void sdecomp(const unsigned char *in, size_t n, int small, int ichunk, int ochunk, Result *r) {
    bz_stream d;
    memset(&d, 0, sizeof d);
    size_t cap = 1 << 20, used = 0, pos = 0;
    unsigned char *plain = malloc(cap);
    int ret = BZ2_bzDecompressInit(&d, 0, small);
    if (ret != BZ_OK) { r->code = ret; r->stage = "decompress_init"; return; }
    for (;;) {
        if (used == cap) plain = realloc(plain, cap *= 2);
        unsigned int give = (unsigned int)((n - pos) < (size_t)ichunk ? (n - pos) : (size_t)ichunk);
        unsigned int room = (unsigned int)((cap - used) < (size_t)ochunk ? (cap - used) : (size_t)ochunk);
        d.next_in = (char *)in + pos; d.avail_in = give;
        d.next_out = (char *)plain + used; d.avail_out = room;
        ret = BZ2_bzDecompress(&d);
        pos += give - d.avail_in;
        used += room - d.avail_out;
        if (ret == BZ_STREAM_END) { r->code = BZ_OK; r->stage = "complete"; break; }
        if (ret != BZ_OK) { r->code = ret; r->stage = "decompress"; break; }
        if (pos == n && room == d.avail_out) { r->code = BZ_UNEXPECTED_EOF; r->stage = "input_exhausted"; break; }
    }
    r->a = (long long)pos;
    BZ2_bzDecompressEnd(&d);
    r->out = plain; r->out_size = used;
}

static void bufcomp(const unsigned char *in, size_t n, int bs, int wf, unsigned int cap, Result *r) {
    unsigned char *dest = malloc(cap + 16);
    memset(dest, 0xA5, cap + 16);
    unsigned int len = cap;
    r->canary_ok = 1;
    int ret = BZ2_bzBuffToBuffCompress((char *)dest, &len, (char *)in, (unsigned int)n, bs, 0, wf);
    canary_check(dest, cap, 16, r);
    r->code = ret; r->stage = ret == BZ_OK ? "complete" : "compress";
    if (ret == BZ_OK) { r->out = dest; r->out_size = len; r->a = len; } else free(dest);
}

static void bufdecomp(const unsigned char *in, size_t n, int small, unsigned int cap, Result *r) {
    unsigned char *dest = malloc(cap + 16);
    memset(dest, 0xA5, cap + 16);
    unsigned int len = cap;
    r->canary_ok = 1;
    int ret = BZ2_bzBuffToBuffDecompress((char *)dest, &len, (char *)in, (unsigned int)n, small, 0);
    canary_check(dest, cap, 16, r);
    r->code = ret; r->stage = ret == BZ_OK ? "complete" : "decompress";
    if (ret == BZ_OK) { r->out = dest; r->out_size = len; r->a = len; } else free(dest);
}

static void param(int bs, int verbosity, int wf, Result *r) {
    bz_stream s;
    memset(&s, 0, sizeof s);
    int ret = BZ2_bzCompressInit(&s, bs, verbosity, wf);
    if (ret == BZ_OK) BZ2_bzCompressEnd(&s);
    r->code = ret; r->stage = ret == BZ_OK ? "complete" : "compress_init";
}

static void bzfwrite(const unsigned char *in, size_t n, int bs, int wf, const char *target, Result *r) {
    errno = 0;
    FILE *f = fopen(target, "wb");
    if (!f) { r->code = BZ_IO_ERROR; r->stage = "fopen"; r->err = errno; r->op = "fopen"; return; }
    int bzerr = BZ_OK;
    BZFILE *b = BZ2_bzWriteOpen(&bzerr, f, bs, 0, wf);
    if (bzerr != BZ_OK) { r->code = bzerr; r->stage = "write_open"; fclose(f); return; }
    size_t pos = 0;
    while (pos < n && bzerr == BZ_OK) {
        int chunk = (int)((n - pos) < 65536 ? (n - pos) : 65536);
        errno = 0;
        BZ2_bzWrite(&bzerr, b, (void *)(in + pos), chunk);
        if (bzerr == BZ_OK) ++r->a; /* accepted BZ2_bzWrite calls before the first error */
        pos += (size_t)chunk;
    }
    if (bzerr != BZ_OK) {
        r->code = bzerr; r->stage = "write";
        r->err = bzerr == BZ_IO_ERROR ? errno : -1; r->op = bzerr == BZ_IO_ERROR ? "fwrite" : NULL;
        int ignored;
        BZ2_bzWriteClose(&ignored, b, 1, NULL, NULL);
        fclose(f);
        return;
    }
    unsigned int in_lo, in_hi, out_lo, out_hi;
    errno = 0;
    BZ2_bzWriteClose64(&bzerr, b, 0, &in_lo, &in_hi, &out_lo, &out_hi);
    if (bzerr != BZ_OK) {
        r->code = bzerr; r->stage = "write_close";
        r->err = bzerr == BZ_IO_ERROR ? errno : -1; r->op = bzerr == BZ_IO_ERROR ? "fwrite/fflush" : NULL;
        fclose(f);
        return;
    }
    errno = 0;
    if (fclose(f) != 0) { r->code = BZ_IO_ERROR; r->stage = "fclose"; r->err = errno; r->op = "fclose"; return; }
    r->code = BZ_OK; r->stage = "complete";
    r->a = ((long long)in_hi << 32) | in_lo;
    r->b = ((long long)out_hi << 32) | out_lo;
}

static void bzfread(const char *path, int small, const char *mode, Result *r) {
    errno = 0;
    FILE *f = fopen(path, strcmp(mode, "w") == 0 ? "ab" : "rb");
    if (!f) { r->code = BZ_IO_ERROR; r->stage = "fopen"; r->err = errno; r->op = "fopen"; return; }
    int bzerr = BZ_OK;
    BZFILE *b = BZ2_bzReadOpen(&bzerr, f, 0, small, NULL, 0);
    if (bzerr != BZ_OK) { r->code = bzerr; r->stage = "read_open"; fclose(f); return; }
    size_t cap = 1 << 20, used = 0;
    unsigned char *plain = malloc(cap);
    while (bzerr == BZ_OK) {
        if (cap - used < 65536) plain = realloc(plain, cap *= 2);
        errno = 0;
        int got = BZ2_bzRead(&bzerr, b, plain + used, 65536);
        if (got > 0) used += (size_t)got;
    }
    if (bzerr == BZ_STREAM_END) { r->code = BZ_OK; r->stage = "complete"; }
    else {
        r->code = bzerr; r->stage = "read";
        r->err = bzerr == BZ_IO_ERROR ? errno : -1; r->op = bzerr == BZ_IO_ERROR ? "fread" : NULL;
    }
    int ignored;
    BZ2_bzReadClose(&ignored, b);
    fclose(f);
    r->out = plain; r->out_size = used;
}

/* Aliasing: BZ2_bzReadGetUnused exposes a pointer into the BZFILE's library-owned buffer,
 * valid only until BZ2_bzReadClose. The documented multi-stream pattern copies it and
 * reopens on the same FILE. a = whether every exposed unused byte equals the file bytes that
 * follow the consumed position (contract); b = nUnused (buffering latitude, diagnostics only). */
static void bzunused(const char *path, int small, Result *r) {
    errno = 0;
    FILE *f = fopen(path, "rb");
    if (!f) { r->code = BZ_IO_ERROR; r->stage = "fopen"; r->err = errno; r->op = "fopen"; return; }
    size_t file_n = 0;
    unsigned char *file = slurp(path, &file_n);
    size_t cap = 1 << 20, used = 0;
    unsigned char *plain = malloc(cap);
    unsigned char carry[BZ_MAX_UNUSED];
    int carry_n = 0, bzerr = BZ_OK, streams = 0;
    r->a = 1;
    for (;;) {
        BZFILE *b = BZ2_bzReadOpen(&bzerr, f, 0, small, carry_n ? carry : NULL, carry_n);
        if (bzerr != BZ_OK) { r->code = bzerr; r->stage = "read_open"; break; }
        while (bzerr == BZ_OK) {
            if (cap - used < 65536) plain = realloc(plain, cap *= 2);
            int got = BZ2_bzRead(&bzerr, b, plain + used, 65536);
            if (got > 0) used += (size_t)got;
        }
        if (bzerr != BZ_STREAM_END) {
            r->code = bzerr; r->stage = streams ? "read_next_stream" : "read";
            int ignored; BZ2_bzReadClose(&ignored, b);
            break;
        }
        ++streams;
        void *unused = NULL; int n = 0;
        BZ2_bzReadGetUnused(&bzerr, b, &unused, &n);
        if (bzerr != BZ_OK) { r->code = bzerr; r->stage = "get_unused"; int ignored; BZ2_bzReadClose(&ignored, b); break; }
        long position = ftell(f);
        if (n < 0 || n > BZ_MAX_UNUSED || position < n || (size_t)position > file_n
            || memcmp(unused, file + position - n, (size_t)n) != 0) r->a = 0;
        r->b += n;
        memcpy(carry, unused, (size_t)n);  /* copy before the library buffer is released */
        carry_n = n;
        int ignored; BZ2_bzReadClose(&ignored, b);
        if (carry_n == 0) {
            int c = fgetc(f);
            if (c == EOF) { r->code = BZ_OK; r->stage = "complete"; break; }
            ungetc(c, f);
        }
    }
    fclose(f);
    free(file);
    r->out = plain; r->out_size = used;
}

/* Aliasing: BZ2_bzerror returns a library-owned constant string per handle error state. */
static void bzerrstr(const char *path, Result *r) {
    FILE *f = fopen(path, "rb");
    if (!f) { r->code = BZ_IO_ERROR; r->stage = "fopen"; r->err = errno; r->op = "fopen"; return; }
    int bzerr = BZ_OK, code = 0;
    BZFILE *b = BZ2_bzReadOpen(&bzerr, f, 0, 0, NULL, 0);
    char text[256] = "";
    if (bzerr == BZ_OK) {
        unsigned char sink[4096];
        while (bzerr == BZ_OK) BZ2_bzRead(&bzerr, b, sink, sizeof sink);
        const char *message = BZ2_bzerror(b, &code);
        snprintf(text, sizeof text, "%d:%s\n", code, message ? message : "(null)");
        int ignored; BZ2_bzReadClose(&ignored, b);
    }
    fclose(f);
    r->code = BZ_OK; r->stage = "complete";
    r->out_size = strlen(text);
    r->out = malloc(r->out_size + 1);
    memcpy(r->out, text, r->out_size + 1);
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    FILE *cases = fopen(argv[1], "r");
    if (!cases) return 2;
    char line[8192];
    long call = 0;
    while (fgets(line, sizeof line, cases)) {
        char id[80], kind[32], path[4096], p3[4096];
        long p1 = 0, p2 = 0, p4 = 0;
        if (sscanf(line, "%79s %31s %4095s %ld %ld %4095s %ld", id, kind, path, &p1, &p2, p3, &p4) != 7) return 2;
        Result r;
        memset(&r, 0, sizeof r);
        r.err = -1;
        r.canary_ok = 1;
        size_t n = 0;
        unsigned char *in = NULL;
        if (strcmp(path, "-") != 0 && strcmp(kind, "fread") != 0 && strcmp(kind, "unused") != 0 && strcmp(kind, "errstr") != 0) {
            in = slurp(path, &n);
            if (!in) return 2;
        }
        printf("{\"id\":\"%s\",\"pid\":%d,", id, (int)getpid());
        affinity();
        printf(",");
        snapshot("before");
        if (!strcmp(kind, "stream")) stream(in, n, (int)p1, (int)p2, atoi(p3), (int)p4, &r);
        else if (!strcmp(kind, "sdecomp")) sdecomp(in, n, (int)p1, (int)p2, atoi(p3), &r);
        else if (!strcmp(kind, "bufcomp")) bufcomp(in, n, (int)p1, (int)p2, (unsigned int)atol(p3), &r);
        else if (!strcmp(kind, "bufdecomp")) bufdecomp(in, n, (int)p1, (unsigned int)p2, &r);
        else if (!strcmp(kind, "param")) param((int)p1, (int)p2, atoi(p3), &r);
        else if (!strcmp(kind, "fwrite")) bzfwrite(in, n, (int)p1, (int)p2, p3, &r);
        else if (!strcmp(kind, "fread")) bzfread(path, (int)p1, p3, &r);
        else if (!strcmp(kind, "unused")) bzunused(path, (int)p1, &r);
        else if (!strcmp(kind, "errstr")) bzerrstr(path, &r);
        else return 2;
        printf(",");
        snapshot("after");
        char out_path[4200] = "";
        if (r.out) {
            snprintf(out_path, sizeof out_path, "%s/call-%ld.bin", argv[2], call);
            dump(out_path, r.out, r.out_size);
        }
        printf(",\"code\":%d,\"name\":\"%s\",\"stage\":\"%s\",\"errno\":%d,\"operation\":\"%s\","
               "\"a\":%lld,\"b\":%lld,\"canary_ok\":%s,\"output\":\"%s\"}\n",
               r.code, bz_name(r.code), r.stage ? r.stage : "", r.err, r.op ? r.op : "",
               r.a, r.b, r.canary_ok ? "true" : "false", out_path);
        fflush(stdout);
        free(r.out);
        free(in);
        ++call;
    }
    fclose(cases);
    return 0;
}

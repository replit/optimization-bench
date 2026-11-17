/* Verifier-only liblzma allocation accounting. Never linked into timing builds.
 * The ledger records requested payload bytes, excluding its own malloc nodes,
 * malloc metadata, caller buffers and OS pthread stacks. All callbacks lock:
 * the public custom allocator is allowed to be called by MT encoder workers. */
#include <lzma.h>
#include <pthread.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct allocation {
    void *ptr;
    size_t size;
    uint64_t id;
    struct allocation *next;
} allocation;

typedef struct {
    pthread_mutex_t lock;
    allocation *head;
    uint64_t live, peak, count, next_id;
    int error;
} ledger;

static void *record_alloc(void *opaque, size_t nmemb, size_t size) {
    ledger *l = opaque;
    if (size && nmemb > SIZE_MAX / size) return NULL;
    size_t n = nmemb * size;
    void *p = malloc(n ? n : 1);
    allocation *a = malloc(sizeof(*a));
    if (!p || !a) { free(p); free(a); return NULL; }
    pthread_mutex_lock(&l->lock);
    *a = (allocation){p, n, ++l->next_id, l->head};
    l->head = a;
    l->live += n;
    ++l->count;
    if (l->live > l->peak) l->peak = l->live;
    printf("{\"kind\":\"alloc\",\"id\":%" PRIu64 ",\"bytes\":%zu}\n", a->id, n);
    pthread_mutex_unlock(&l->lock);
    return p;
}

static void record_free(void *opaque, void *ptr) {
    if (!ptr) return;
    ledger *l = opaque;
    pthread_mutex_lock(&l->lock);
    allocation **slot = &l->head;
    while (*slot && (*slot)->ptr != ptr) slot = &(*slot)->next;
    if (!*slot) {
        l->error = 1;
        puts("{\"kind\":\"allocator-contract-error\",\"reason\":\"unknown-free\"}");
        pthread_mutex_unlock(&l->lock);
        return;
    }
    allocation *a = *slot;
    *slot = a->next;
    l->live -= a->size;
    --l->count;
    printf("{\"kind\":\"free\",\"id\":%" PRIu64 "}\n", a->id);
    pthread_mutex_unlock(&l->lock);
    free(a);
    free(ptr);
}

static void observe(ledger *l, const char *phase, const char *symbol,
                    uint64_t report, lzma_ret ret, unsigned cycle,
                    uint64_t total_in, uint64_t total_out) {
    pthread_mutex_lock(&l->lock);
    printf("{\"kind\":\"observation\",\"cycle\":%u,\"phase\":\"%s\","
           "\"symbol\":\"%s\",\"reported_bytes\":%" PRIu64 ","
           "\"live_bytes\":%" PRIu64 ",\"peak_bytes\":%" PRIu64 ","
           "\"outstanding\":%" PRIu64 ",\"status\":%u,"
           "\"total_in\":%" PRIu64 ",\"total_out\":%" PRIu64 "}\n",
           cycle, phase, symbol, report, l->live, l->peak, l->count,
           (unsigned)ret, total_in, total_out);
    pthread_mutex_unlock(&l->lock);
}

static unsigned number(const char *s, unsigned low, unsigned high) {
    char *end;
    unsigned long n = strtoul(s, &end, 10);
    if (!*s || *end || n < low || n > high) exit(64);
    return (unsigned)n;
}

int main(int argc, char **argv) {
    if (argc != 7) {
        fputs("usage: probe MODE PRESET THREADS INPUT_BYTES INPUT_CHUNK OUTPUT_CHUNK\n", stderr);
        return 64;
    }
    const char *mode = argv[1];
    int raw_enc = !strcmp(mode, "raw-encode");
    int easy_enc = !strcmp(mode, "easy-encode");
    int stream_enc = !strcmp(mode, "stream-encode");
    int mt_enc = !strcmp(mode, "mt-encode");
    int raw_dec = !strcmp(mode, "raw-decode");
    int stream_dec = !strcmp(mode, "stream-decode");
    int easy_dec = !strcmp(mode, "easy-decode");
    if (!(raw_enc || easy_enc || stream_enc || mt_enc || raw_dec || stream_dec || easy_dec))
        return 64;
    unsigned preset = number(argv[2], 0, 6);
    unsigned threads = number(argv[3], 1, 8);
    size_t input_size = number(argv[4], 0, 2097152);
    size_t in_chunk = number(argv[5], 1, 2097152);
    size_t out_chunk = number(argv[6], 1, 2097152);
    uint8_t *input = malloc(input_size ? input_size : 1);
    uint8_t *output = malloc(out_chunk);
    size_t encoded_cap = lzma_stream_buffer_bound(input_size) + 1024;
    uint8_t *encoded = malloc(encoded_cap);
    if (!input || !output || !encoded) return 70;
    uint32_t rng = 1729;
    for (size_t i = 0; i < input_size; ++i) {
        rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
        input[i] = i % 3 ? (uint8_t)rng : (uint8_t)(i % 251);
    }
    lzma_options_lzma opt;
    if (lzma_lzma_preset(&opt, preset)) return 70;
    lzma_filter filters[] = {{LZMA_FILTER_LZMA2, &opt}, {LZMA_VLI_UNKNOWN, NULL}};
    lzma_mt mt = {0};
    mt.threads = threads;
    mt.block_size = 65536;
    mt.filters = filters;
    mt.check = LZMA_CHECK_CRC32;
    size_t encoded_size = 0;
    if (raw_dec || stream_dec || easy_dec) {
        lzma_ret ret = raw_dec
            ? lzma_raw_buffer_encode(filters, NULL, input, input_size, encoded, &encoded_size, encoded_cap)
            : lzma_stream_buffer_encode(filters, LZMA_CHECK_CRC32, NULL, input, input_size,
                                       encoded, &encoded_size, encoded_cap);
        if (ret != LZMA_OK) return 71; /* Preparation failure is invalid_run. */
    }
    ledger l = {.lock = PTHREAD_MUTEX_INITIALIZER};
    lzma_allocator allocator = {record_alloc, record_free, &l};
    lzma_stream strm = LZMA_STREAM_INIT;
    strm.allocator = &allocator;
    printf("{\"kind\":\"header\",\"schema\":\"xz-allocation-ledger-v1\","
           "\"library_version\":\"%s\",\"mode\":\"%s\",\"preset\":%u,"
           "\"threads\":%u,\"input_bytes\":%zu,\"input_chunk\":%zu,"
           "\"output_chunk\":%zu}\n", lzma_version_string(), mode, preset,
           threads, input_size, in_chunk, out_chunk);
    const char *symbol = raw_enc || stream_enc ? "lzma_raw_encoder_memusage"
        : easy_enc ? "lzma_easy_encoder_memusage"
        : mt_enc ? "lzma_stream_encoder_mt_memusage"
        : raw_dec ? "lzma_raw_decoder_memusage"
        : easy_dec ? "lzma_easy_decoder_memusage" : "lzma_memusage";
    uint64_t estimate = raw_enc || stream_enc ? lzma_raw_encoder_memusage(filters)
        : easy_enc ? lzma_easy_encoder_memusage(preset)
        : mt_enc ? lzma_stream_encoder_mt_memusage(&mt)
        : raw_dec ? lzma_raw_decoder_memusage(filters)
        : easy_dec ? lzma_easy_decoder_memusage(preset) : 0;
    /* init -> updates -> finish -> end; init -> update -> early-end;
     * reinit the same public stream -> full finish -> end. */
    for (unsigned cycle = 0; cycle < 3; ++cycle) {
        l.peak = l.live;
        /* lzma_end releases internal state, not caller-owned I/O cursors.
         * Reinitialization must not feed the previous early-end suffix. */
        strm.next_in = NULL;
        strm.avail_in = 0;
        strm.next_out = NULL;
        strm.avail_out = 0;
        lzma_ret ret = raw_enc ? lzma_raw_encoder(&strm, filters)
            : easy_enc ? lzma_easy_encoder(&strm, preset, LZMA_CHECK_CRC32)
            : stream_enc ? lzma_stream_encoder(&strm, filters, LZMA_CHECK_CRC32)
            : mt_enc ? lzma_stream_encoder_mt(&strm, &mt)
            : raw_dec ? lzma_raw_decoder(&strm, filters)
            : lzma_stream_decoder(&strm, UINT64_MAX, 0);
        observe(&l, "init", symbol, stream_dec ? lzma_memusage(&strm) : estimate,
                ret, cycle, strm.total_in, strm.total_out);
        if (ret != LZMA_OK) {
            lzma_end(&strm);
            observe(&l, "error-end", symbol, 0, ret, cycle, strm.total_in, strm.total_out);
            continue;
        }
        const uint8_t *data = raw_dec || stream_dec || easy_dec ? encoded : input;
        size_t size = raw_dec || stream_dec || easy_dec ? encoded_size : input_size;
        size_t supplied = 0;
        unsigned step = 0;
        do {
            if (!strm.avail_in && supplied < size) {
                size_t n = size - supplied;
                if (n > in_chunk) n = in_chunk;
                strm.next_in = data + supplied;
                strm.avail_in = n;
                supplied += n;
            }
            strm.next_out = output;
            strm.avail_out = out_chunk;
            lzma_action action = supplied == size ? LZMA_FINISH : LZMA_RUN;
            ret = lzma_code(&strm, action);
            observe(&l, ret == LZMA_STREAM_END ? "finish"
                        : ret == LZMA_OK ? "update" : "api-error", symbol,
                    stream_dec ? lzma_memusage(&strm) : estimate, ret, cycle,
                    strm.total_in, strm.total_out);
            if (++step > 5000000) { lzma_end(&strm); return 73; }
            if (cycle == 1) break;
        } while (ret == LZMA_OK);
        lzma_end(&strm);
        observe(&l, ret != LZMA_OK && ret != LZMA_STREAM_END ? "error-end"
                    : cycle == 1 ? "early-end" : "end", symbol, 0, ret, cycle,
                strm.total_in, strm.total_out);
    }
    free(input); free(output); free(encoded);
    pthread_mutex_destroy(&l.lock);
    return 0;
}

#include <lzma.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static void bytes(const uint8_t *p, size_t n) {
    for (size_t i = 0; i < n; ++i) printf("%02x", p[i]);
}

int main(int argc, char **argv) {
    if (argc != 10) return 64;
    FILE *f = fopen("/scratch/input", "rb");
    if (!f || fseek(f, 0, SEEK_END)) return 65;
    long size = ftell(f);
    if (size < 0 || size > 65536 || fseek(f, 0, SEEK_SET)) return 65;
    uint8_t *input = malloc((size_t)size + 1);
    if (!input || fread(input, 1, size, f) != (size_t)size) return 65;
    fclose(f);
    uint32_t preset = strtoul(argv[2], NULL, 10);
    lzma_check check = (lzma_check)atoi(argv[3]);
    unsigned filter = atoi(argv[4]), threads = atoi(argv[5]);
    size_t chunk = atoi(argv[6]), out_chunk = atoi(argv[7]);
    int invalid = atoi(argv[8]);
    lzma_options_lzma opt;
    lzma_options_delta delta = { .type = LZMA_DELTA_TYPE_BYTE, .dist = 1 };
    if (lzma_lzma_preset(&opt, preset)) return 66;
    lzma_filter filters[3];
    size_t count = 0;
    if (filter) {
        filters[count++] = (lzma_filter){filter, filter == LZMA_FILTER_DELTA ? &delta : NULL};
    }
    filters[count++] = (lzma_filter){LZMA_FILTER_LZMA2, &opt};
    filters[count] = (lzma_filter){LZMA_VLI_UNKNOWN, NULL};
    if (invalid) { opt.lc = 5; opt.lp = 4; }
    size_t bound = lzma_stream_buffer_bound(size);
    // MT worker scheduling is not a semantic result. A blocking FINISH with a
    // complete input and ample output gives a deterministic public transcript.
    if (!strcmp(argv[1], "mt")) { chunk = (size_t)size + 1; out_chunk = bound; }
    uint8_t *output = malloc(bound + out_chunk + 1);
    if (!output) return 67;
    if (!strcmp(argv[1], "buffer")) {
        if (!strcmp(argv[9], "-1")) {
            size_t pos = 0;
            lzma_ret r = lzma_stream_buffer_encode(filters, check, NULL, input, size, output, &pos, bound);
            // Invalid option cases have no successful encoded size.
            printf("%zu ", r == LZMA_OK ? pos : 64);
            pos = 0;
            r = lzma_easy_buffer_encode(invalid ? 99 : preset, check, NULL, input, size, output, &pos, bound);
            printf("%zu\n", r == LZMA_OK ? pos : 64);
        } else {
            char *next = argv[9], *end;
            do {
                size_t cap = strtoul(next, &end, 10), pos = 0;
                memset(output, 0xA5, bound + out_chunk + 1);
                lzma_ret r = lzma_stream_buffer_encode(filters, check, NULL, input, size, output, &pos, cap);
                printf("buffer %zu %u %zu ", cap, r, pos);
                bytes(output, pos);
                // Also observe writes within the caller's entire capacity on error.
                printf(" capacity "); bytes(output, cap); puts("");
                memset(output, 0xA5, bound + out_chunk + 1);
                pos = 0;
                r = lzma_easy_buffer_encode(invalid ? 99 : preset, check, NULL,
                                           input, size, output, &pos, cap);
                printf("easy %zu %u %zu ", cap, r, pos);
                bytes(output, pos); printf(" capacity "); bytes(output, cap); puts("");
                next = *end ? end + 1 : end;
            } while (*next);
        }
    } else {
        lzma_stream s[2] = {LZMA_STREAM_INIT, LZMA_STREAM_INIT};
        uint8_t *data[2] = {input, malloc((size_t)size + 1)};
        if (!data[1]) return 67;
        for (long i = 0; i < size; ++i) data[1][i] = input[i] ^ 0x5A;
        // Decode independent identical frames, rather than manufacturing an
        // unrelated error by XORing the second frame.
        if (!strcmp(argv[1], "decode")) memcpy(data[1], input, size);
        int done[2] = {0, 0};
        size_t supplied[2] = {0, 0};
        for (int k = 0; k < 2; ++k) {
            lzma_ret r;
            if (!strcmp(argv[1], "decode")) {
                r = lzma_stream_decoder(&s[k], invalid ? 1 : UINT64_MAX, LZMA_CONCATENATED);
            } else if (!strcmp(argv[1], "mt")) {
                lzma_mt mt = {0};
                mt.threads = threads; mt.block_size = 1024; mt.check = check; mt.filters = filters;
                if (invalid) mt.threads = 0;
                r = lzma_stream_encoder_mt(&s[k], &mt);
            } else r = lzma_stream_encoder(&s[k], filters, check);
            printf("init %d %u\n", k, r);
            done[k] = r != LZMA_OK;
        }
        // Round-robin public lzma_streams: state must survive calls on the other
        // instance. Record ALL output and all observable statuses/counters.
        for (unsigned step = 0; !done[0] || !done[1]; ++step) {
            if (step > 200000) return 68;
            for (int k = 0; k < 2; ++k) {
                if (done[k]) continue;
                if (!s[k].avail_in && supplied[k] < (size_t)size) {
                    size_t n = (size_t)size - supplied[k];
                    if (n > chunk) n = chunk;
                    s[k].next_in = data[k] + supplied[k]; s[k].avail_in = n; supplied[k] += n;
                }
                s[k].next_out = output; s[k].avail_out = out_chunk;
                lzma_action action = supplied[k] == (size_t)size ? LZMA_FINISH : LZMA_RUN;
                lzma_ret r = lzma_code(&s[k], action);
                printf("step %d %u %u %zu %llu %llu ", k, action, r, s[k].avail_in,
                       (unsigned long long)s[k].total_in, (unsigned long long)s[k].total_out);
                bytes(output, out_chunk - s[k].avail_out); puts("");
                done[k] = r != LZMA_OK;
            }
        }
        for (int k = 0; k < 2; ++k) lzma_end(&s[k]);
        // Reinitialization after error/success in the same process is public.
        for (int k = 0; k < 2; ++k) {
            lzma_ret r = lzma_easy_encoder(&s[k], 0, LZMA_CHECK_CRC32);
            printf("reuse %d %u\n", k, r);
            lzma_end(&s[k]);
        }
        free(data[1]);
    }
    free(output); free(input);
    return 0;
}

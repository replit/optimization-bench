/* Streaming .xz decode driver (libarchive pattern).
 * archive_read_support_filter_xz.c (libarchive v3.8.1) L513-515 initializes
 * lzma_stream_decoder(&s, UINT64_MAX, LZMA_CONCATENATED), feeds read-ahead
 * input, drains 64 KiB output blocks (L474) with LZMA_RUN and switches to
 * LZMA_FINISH once input is exhausted (L690-691). Integrity checks stay on.
 * One fresh process per input. Every decoded byte is folded into a protected
 * XXH64 digest and the driver prints "<decoded bytes> <xxh64>\n"; this commits
 * to the complete decoded stream without copying it into the page cache. */
#include <inttypes.h>
#include <lzma.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "xxh64.h"

#define IN_CHUNK (64u * 1024u)
#define OUT_CHUNK (64u * 1024u)

__attribute__((noinline))
static lzma_ret decode_operation(const uint8_t *input, size_t size, xxh64_state *digest)
{
    lzma_stream s = LZMA_STREAM_INIT;
    lzma_ret ret = lzma_stream_decoder(&s, UINT64_MAX, LZMA_CONCATENATED);
    if (ret != LZMA_OK)
        return ret;
    static uint8_t outbuf[OUT_CHUNK];
    size_t fed = 0;
    s.next_out = outbuf;
    s.avail_out = sizeof(outbuf);
    for (;;) {
        if (s.avail_in == 0 && fed < size) {
            size_t n = size - fed < IN_CHUNK ? size - fed : IN_CHUNK;
            s.next_in = input + fed;
            s.avail_in = n;
            fed += n;
        }
        lzma_action action = (s.avail_in == 0 && fed == size) ? LZMA_FINISH : LZMA_RUN;
        ret = lzma_code(&s, action);
        if (s.avail_out == 0 || ret != LZMA_OK) {
            /* Drain full blocks, the final block, and any data decoded before an error. */
            size_t n = sizeof(outbuf) - s.avail_out;
            if (n)
                xxh64_update(digest, outbuf, n);
            s.next_out = outbuf;
            s.avail_out = sizeof(outbuf);
        }
        if (ret != LZMA_OK)
            break;
    }
    lzma_end(&s);
    return ret == LZMA_STREAM_END ? LZMA_OK : ret;
}

int main(int argc, char **argv)
{
    if (argc != 2)
        return 64;
    FILE *file = fopen(argv[1], "rb");
    if (file == NULL || fseek(file, 0, SEEK_END) != 0)
        return 65;
    long length = ftell(file);
    if (length < 0 || fseek(file, 0, SEEK_SET) != 0)
        return 65;
    size_t size = (size_t)length;
    uint8_t *input = malloc(size ? size : 1);
    if (input == NULL || fread(input, 1, size, file) != size)
        return 65;
    if (fclose(file) != 0)
        return 65;
    xxh64_state digest;
    xxh64_init(&digest);
    lzma_ret result = decode_operation(input, size, &digest);
    printf("%" PRIu64 " %016" PRIx64 "\n", digest.total, xxh64_final(&digest));
    int failed = fclose(stdout) != 0;
    free(input);
    if (result != LZMA_OK) {
        fprintf(stderr, "liblzma returned %u\n", (unsigned)result);
        return 2;
    }
    return failed ? 66 : 0;
}

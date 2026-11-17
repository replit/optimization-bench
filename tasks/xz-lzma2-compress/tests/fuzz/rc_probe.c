#define _GNU_SOURCE
#include <lzma.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sched.h>

static unsigned char plain[4096], frame[8192], truncated[8192];
static unsigned char corrupt[8192], concatenated[16384];
static size_t plain_size, frame_size, truncated_size, corrupt_size, concatenated_size;

static size_t read_file(const char *root, const char *name, unsigned char *data, size_t cap)
{
    char path[4096];
    snprintf(path, sizeof(path), "%s/%s", root, name);
    FILE *file = fopen(path, "rb");
    if (!file) exit(2);
    size_t size = fread(data, 1, cap, file);
    if (!feof(file) || ferror(file)) exit(2);
    fclose(file);
    return size;
}

static void *fail_alloc(void *opaque, size_t count, size_t size)
{
    (void)opaque; (void)count; (void)size;
    return NULL;
}

static void fail_free(void *opaque, void *ptr)
{
    (void)opaque;
    free(ptr);
}

static void hex(const unsigned char *data, size_t size)
{
    putchar('"');
    for (size_t i = 0; i < size; ++i) printf("%02x", data[i]);
    putchar('"');
}

static size_t code(lzma_stream *stream, const unsigned char *input, size_t size,
                   unsigned char *output, size_t in_chunk, size_t out_chunk, int zero_output)
{
    size_t supplied = 0, produced = 0;
    lzma_ret ret = LZMA_OK;
    printf("\"trace\":[");
    for (unsigned call = 0; call < 8192; ++call) {
        if (stream->avail_in == 0 && supplied < size) {
            size_t count = size - supplied;
            if (count > in_chunk) count = in_chunk;
            stream->next_in = input + supplied;
            stream->avail_in = count;
            supplied += count;
        }
        size_t capacity = zero_output && call < 2 ? 0 : out_chunk;
        if (produced + capacity > 32768) exit(3);
        stream->next_out = output + produced;
        stream->avail_out = capacity;
        lzma_action action = supplied == size ? LZMA_FINISH : LZMA_RUN;
        ret = lzma_code(stream, action);
        produced += capacity - stream->avail_out;
        printf("%s[%u,%u,%llu,%llu,%zu,%zu]", call ? "," : "",
               (unsigned)action, (unsigned)ret,
               (unsigned long long)stream->total_in,
               (unsigned long long)stream->total_out, stream->avail_in, stream->avail_out);
        if (ret != LZMA_OK && !(zero_output && call < 2 && ret == LZMA_BUF_ERROR)) break;
        if (call == 8191) exit(3);
    }
    printf("],\"ret\":%u,\"output\":", (unsigned)ret);
    hex(output, produced);
    return produced;
}

static void request(int operation, size_t in_chunk, size_t out_chunk)
{
    unsigned char output[32768], other[32768], retained[32768];
    lzma_stream stream = LZMA_STREAM_INIT, second = LZMA_STREAM_INIT;
    lzma_allocator allocator = { fail_alloc, fail_free, NULL };
    const unsigned char *input = plain;
    size_t size = plain_size;
    int encode = operation == 0 || operation >= 5;
    if (operation == 1) { input = frame; size = frame_size; }
    if (operation == 2) { input = truncated; size = truncated_size; }
    if (operation == 3) { input = corrupt; size = corrupt_size; }
    if (operation == 4) { input = concatenated; size = concatenated_size; }
    unsigned char original[16384];
    memcpy(original, input, size);
    printf("{\"effects\":{");
    if (operation == 7) stream.allocator = &allocator;
    lzma_ret ret = encode ? lzma_easy_encoder(&stream, 0, LZMA_CHECK_CRC64)
                         : lzma_stream_decoder(&stream, UINT64_MAX, LZMA_CONCATENATED);
    printf("\"init\":%u", (unsigned)ret);
    if (operation == 6) {
        lzma_ret second_ret = lzma_easy_encoder(&second, 0, LZMA_CHECK_CRC64);
        printf(",\"second_init\":%u", (unsigned)second_ret);
        if (second_ret != LZMA_OK) { lzma_end(&stream); exit(3); }
    }
    if (ret == LZMA_OK) {
        if (operation == 5) {
            stream.next_in = plain;
            stream.avail_in = plain_size / 2;
            stream.next_out = output;
            stream.avail_out = 1;
            lzma_ret partial = lzma_code(&stream, LZMA_RUN);
            printf(",\"partial\":%u", (unsigned)partial);
            ret = lzma_easy_encoder(&stream, 0, LZMA_CHECK_CRC64);
            printf(",\"reinit\":%u", (unsigned)ret);
        }
        printf(",\"first\":{");
        size_t produced = ret == LZMA_OK
            ? code(&stream, input, size, output, in_chunk, out_chunk, operation == 8) : 0;
        printf("}");
        if (operation == 6) {
            memcpy(retained, output, produced);
            printf(",\"second\":{");
            code(&second, input, size, other, in_chunk, out_chunk, 0);
            printf("},\"retained_output_unchanged\":%s",
                   memcmp(retained, output, produced) == 0 ? "true" : "false");
        }
    }
    lzma_end(&stream);
    lzma_end(&second);
    printf(",\"input_unchanged\":%s,\"internal_released\":%s},\"pid\":%ld,\"affinity\":[",
           memcmp(original, input, size) == 0 ? "true" : "false",
           stream.internal == NULL && second.internal == NULL ? "true" : "false", (long)getpid());
    cpu_set_t cpus;
    CPU_ZERO(&cpus);
    if (sched_getaffinity(0, sizeof(cpus), &cpus) != 0) exit(3);
    int first = 1;
    for (int cpu = 0; cpu < CPU_SETSIZE; ++cpu)
        if (CPU_ISSET(cpu, &cpus)) { printf("%s%d", first ? "" : ",", cpu); first = 0; }
    puts("]}");
    fflush(stdout);
}

int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    plain_size = read_file(argv[1], "plain", plain, sizeof(plain));
    frame_size = read_file(argv[1], "frame", frame, sizeof(frame));
    truncated_size = read_file(argv[1], "truncated", truncated, sizeof(truncated));
    corrupt_size = read_file(argv[1], "corrupt", corrupt, sizeof(corrupt));
    concatenated_size = read_file(argv[1], "concatenated", concatenated, sizeof(concatenated));
    puts("{\"ready\":true}");
    fflush(stdout);
    int operation;
    size_t in_chunk, out_chunk;
    while (scanf("%d %zu %zu", &operation, &in_chunk, &out_chunk) == 3) {
        if (operation < 0 || operation > 8 || !in_chunk || in_chunk > 4096
                || !out_chunk || out_chunk > 4096) return 2;
        request(operation, in_chunk, out_chunk);
    }
    return ferror(stdin) ? 2 : 0;
}

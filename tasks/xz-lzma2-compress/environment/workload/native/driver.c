#include <lzma.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

__attribute__((noinline))
static lzma_ret encode_operation(const uint8_t *input, size_t size,
                                uint8_t **output, size_t *output_size)
{
    const size_t bound = lzma_stream_buffer_bound(size);
    *output = malloc(bound);
    if (*output == NULL)
        return LZMA_MEM_ERROR;
    *output_size = 0;
    return lzma_easy_buffer_encode(6, LZMA_CHECK_CRC64, NULL,
                                  input, size, *output, output_size, bound);
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
    uint8_t *output = NULL;
    size_t output_size = 0;
    lzma_ret result = encode_operation(input, size, &output, &output_size);
    if (result != LZMA_OK) {
        fprintf(stderr, "liblzma returned %u\n", (unsigned)result);
        free(output);
        free(input);
        return 2;
    }
    int failed = fwrite(output, 1, output_size, stdout) != output_size;
    if (fclose(stdout) != 0)
        failed = 1;
    free(output);
    free(input);
    return failed ? 66 : 0;
}

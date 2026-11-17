/* Verifier-owned public API probe. JSON transport uses libc, never libjq. */
#include <jq.h>
#include <jv.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int comma;
static jv input_failure(jq_state *state, void *opaque) {
    int *fired = (int *)opaque;
    (void)state;
    *fired = 1;
    return jv_invalid_with_msg(jv_string("controlled input callback failure"));
}
static void hex_bytes(const char *data, int length) {
    static const char digits[] = "0123456789abcdef";
    int i;
    putchar('"');
    for (i = 0; i < length; i++) {
        unsigned char byte = (unsigned char)data[i];
        putchar(digits[byte >> 4]);
        putchar(digits[byte & 15]);
    }
    putchar('"');
}

static void event(const char *label, jv value) {
    int kind = (int)jv_get_kind(value);
    jv observed;
    if (comma) putchar(',');
    comma = 1;
    printf("{\"operation\":\"%s\",\"kind\":%d,\"dump_encoding\":\"hex\",\"dump\":", label, kind);
    if (!jv_is_valid(value)) {
        int has_message = jv_invalid_has_msg(jv_copy(value));
        if (has_message)
            value = jv_invalid_get_msg(value);
        else {
            jv_free(value);
            value = jv_null();
        }
    }
    /* Dump is an observed public operation, not the JSON envelope serializer. */
    observed = jv_dump_string(value, 0);
    if (jv_get_kind(observed) != JV_KIND_STRING) exit(91);
    hex_bytes(jv_string_value(observed), jv_string_length_bytes(jv_copy(observed)));
    putchar('}');
    jv_free(observed);
}

static void error_callback(void *opaque, jv value) {
    (void)opaque;
    event("jq-error-callback", value);
}

static char *read_input(const char *path, int *length) {
    FILE *file = fopen(path, "rb");
    char *data;
    long size;
    if (!file || fseek(file, 0, SEEK_END)) exit(92);
    size = ftell(file);
    if (size < 0 || size > 32 * 1024 * 1024 || fseek(file, 0, SEEK_SET)) exit(93);
    data = (char *)malloc((size_t)size + 1);
    if (!data || fread(data, 1, (size_t)size, file) != (size_t)size) exit(94);
    fclose(file);
    data[size] = 0;
    *length = (int)size;
    return data;
}

int main(int argc, char **argv) {
    char *data;
    int length;
    if (argc < 3) return 90;
    data = read_input(argv[2], &length);
    printf("{\"format\":\"jq-public-native-events-v1\",\"events\":[");
    if (!strcmp(argv[1], "parse")) {
        event("jv_parse_sized", jv_parse_sized(data, length));
    } else if (!strcmp(argv[1], "parser")) {
        jv_parser *parser = jv_parser_new(0);
        int at = 0;
        while (at < length) {
            int take = length - at > 31 ? 31 : length - at;
            jv value;
            jv_parser_set_buf(parser, data + at, take, at + take < length);
            at += take;
            while (1) {
                value = jv_parser_next(parser);
                if (!jv_is_valid(value)) {
                    if (jv_invalid_has_msg(jv_copy(value)))
                        event("jv_parser_next-error", value);
                    else jv_free(value);
                    break;
                }
                event("jv_parser_next-value", value);
            }
        }
        jv_parser_free(parser);
    } else if (!strcmp(argv[1], "pipeline")) {
        jq_state *state = jq_init();
        jv value;
        int compiled, count = 0;
        if (!state || argc != 4) return 95;
        jq_set_error_cb(state, error_callback, NULL);
        compiled = jq_compile(state, argv[3]);
        event("jq_compile", jv_number(compiled));
        if (compiled) {
            value = jv_parse_sized(data, length);
            if (jv_is_valid(value)) {
                jq_start(state, value, 0);
                while (count++ < 65536) {
                    value = jq_next(state);
                    if (!jv_is_valid(value)) {
                        if (jv_invalid_has_msg(jv_copy(value)))
                            event("jq_next-error", value);
                        else jv_free(value);
                        break;
                    }
                    event("jq_next-value", value);
                }
                if (count >= 65536) return 96;
            } else event("input-parse-error", value);
        }
        jq_teardown(&state);
    } else if (!strcmp(argv[1], "input-fault")) {
        jq_state *state = jq_init();
        int fired = 0;
        if (!state) return 95;
        jq_set_error_cb(state, error_callback, NULL);
        jq_set_input_cb(state, input_failure, &fired);
        if (!jq_compile(state, "input")) return 95;
        jq_start(state, jv_null(), 0);
        event("input-callback-result", jq_next(state));
        event("input-callback-fired", jv_bool(fired));
        jq_teardown(&state);
        if (!fired) return 95;
    } else if (!strcmp(argv[1], "ownership")) {
        jv first = jv_object_set(jv_object(), jv_string("value"), jv_number(1));
        jv second = jv_copy(first);
        first = jv_object_set(first, jv_string("value"), jv_number(9));
        event("original-after-update", first);
        event("independent-copy", second);
    } else return 97;
    free(data);
    printf("]}\n");
    return 0;
}

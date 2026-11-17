#include <jq.h>
extern int second_jq(void);
static jq_state *(*volatile init_reference)(void) = &jq_init;
static jv_kind (*volatile kind_reference)(jv) = &jv_get_kind;
static jv (*volatile copy_reference)(jv) = &jv_copy;
int main(void) {
    jq_state *state = init_reference();
    jv input = jv_number(7);
    jv output;
    int ok;
    if (!state) return 2;
    if (!jq_compile(state, ".")) { jq_teardown(&state); jv_free(input); return 3; }
    jq_start(state, copy_reference(input), 0);
    output = jq_next(state);
    ok = kind_reference(output) == JV_KIND_NUMBER && second_jq();
    jv_free(input);
    jv_free(output);
    jq_teardown(&state);
    return ok ? 0 : 1;
}

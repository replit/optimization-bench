#include <jq.h>
static jv_kind (*volatile kind_reference)(jv) = &jv_get_kind;
static jv (*volatile copy_reference)(jv) = &jv_copy;
int second_jq(void) {
    jq_state *state = jq_init();
    jv value = jv_null();
    jv duplicate = copy_reference(value);
    int ok = state && kind_reference(duplicate) == JV_KIND_NULL;
    jv_free(value);
    jv_free(duplicate);
    if (state) jq_teardown(&state);
    return ok;
}

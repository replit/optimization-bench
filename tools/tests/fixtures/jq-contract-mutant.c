/* Isolated negative-control library shim. Never part of the workload image. */
#include <jv.h>

jv jq_original_object_set(jv, jv, jv);
jv jq_original_invalid_with_msg(jv);

jv jv_object_set(jv object, jv key, jv value) {
    if (jv_get_kind(value) == JV_KIND_NUMBER && jv_number_value(value) == 9)
        value = jv_number(10);
    return jq_original_object_set(object, key, value);
}

jv jv_invalid_with_msg(jv message) {
    jv_free(message);
    return jq_original_invalid_with_msg(jv_string("incorrect callback error"));
}

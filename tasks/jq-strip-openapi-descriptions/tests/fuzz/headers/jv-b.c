#include <jv.h>
static jv_kind (*volatile kind_reference)(jv) = &jv_get_kind;
static jv (*volatile copy_reference)(jv) = &jv_copy;
int second_jv(void) {
    jv value = jv_number(7);
    jv duplicate = copy_reference(value);
    int ok = kind_reference(duplicate) == JV_KIND_NUMBER;
    jv_free(value);
    jv_free(duplicate);
    return ok;
}

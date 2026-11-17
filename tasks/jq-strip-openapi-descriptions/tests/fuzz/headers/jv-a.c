#include <jv.h>
extern int second_jv(void);
static jv_kind (*volatile kind_reference)(jv) = &jv_get_kind;
static jv (*volatile copy_reference)(jv) = &jv_copy;
int main(void) {
    jv original = jv_string("two distinct translation units");
    jv duplicate = copy_reference(original);
    int ok = kind_reference(duplicate) == JV_KIND_STRING && second_jv();
    jv_free(original);
    jv_free(duplicate);
    return ok ? 0 : 1;
}

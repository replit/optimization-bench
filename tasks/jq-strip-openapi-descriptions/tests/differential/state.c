#include "jv.h"
#include <stdio.h>
int main(void) {
    jv first = jv_object_set(jv_object(), jv_string("value"), jv_number(1));
    jv independent = jv_copy(first);
    first = jv_object_set(first, jv_string("value"), jv_number(9));
    int copied = (int)jv_number_value(jv_object_get(jv_copy(independent), jv_string("value")));
    int changed = (int)jv_number_value(jv_object_get(jv_copy(first), jv_string("value")));
    jv_free(first); jv_free(independent);
    printf("{\"independent\":%d,\"mutated\":%d}\n", copied, changed);
    return 0;
}

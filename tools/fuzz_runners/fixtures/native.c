#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <limits.h>

static int classify(unsigned char n) {
    if (n == 0) return 0;
    if (n < 128) return 1;
    return 2;
}

#ifdef FUZZ
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size) {
        int answer = classify(data[0]);
        if (answer < 0 || answer > 2) abort();
    }
    return 0;
}
#else
#ifdef THREAD
static int shared;
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static void *worker(void *unused) {
    (void)unused;
    for (int i = 0; i < 10000; i++) {
#ifndef NEGATIVE
        pthread_mutex_lock(&mutex);
#endif
        shared++;
#ifndef NEGATIVE
        pthread_mutex_unlock(&mutex);
#endif
    }
    return NULL;
}
#endif
int main(int argc, char **argv) {
#ifdef THREAD
    pthread_t one, two;
    pthread_create(&one, NULL, worker, NULL);
    pthread_create(&two, NULL, worker, NULL);
    pthread_join(one, NULL);
    pthread_join(two, NULL);
#elif defined(ASAN_BAD)
    volatile char *data = malloc(1);
    data[argc] = 1;
    free((void *)data);
#elif defined(UBSAN_BAD)
    volatile int data = INT_MAX;
    printf("%d\n", data + argc);
#elif defined(MSAN_BAD)
    int *data = malloc(sizeof(int));
    if (*data == argc) puts("uninitialized");
    free(data);
#else
    if (argc > 1) {
        FILE *input = fopen(argv[1], "rb");
        if (!input) return 2;
        int n = fgetc(input);
        fclose(input);
        return classify((unsigned char)n) < 0;
    }
    for (int i = 0; i < 256; i++) {
        if (classify((unsigned char)i) < 0) abort();
    }
#endif
    return 0;
}
#endif

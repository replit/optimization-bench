/* Native per-process snapshots at normal workload exit, not parent-shell state. */
#define _GNU_SOURCE
#include <dirent.h>
#include <fenv.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <errno.h>
#if defined(__SSE__)
#include <xmmintrin.h>
#endif
extern char **environ;
static int live_children(void) {
    DIR *d = opendir("/proc");
    if (!d) return -1;
    int count = 0, self_seen = 0, unavailable = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] < '0' || e->d_name[0] > '9') continue;
        char path[300], raw[4096];
        snprintf(path, sizeof(path), "/proc/%s/stat", e->d_name);
        FILE *s = fopen(path, "r");
        if (!s) {
            if (errno != ENOENT && errno != ESRCH) unavailable = 1;
            continue;
        }
        size_t n = fread(raw, 1, sizeof(raw)-1, s);
        fclose(s); raw[n] = 0;
        char *end = strrchr(raw, ')'), *save = NULL;
        if (!end || end[1] != ' ') { unavailable = 1; continue; }
        char *state = strtok_r(end+2, " ", &save);
        char *parent = strtok_r(NULL, " ", &save);
        if (!state || !parent) { unavailable = 1; continue; }
        if (atoi(e->d_name) == getpid()) self_seen = 1;
        if (state[0] != 'Z' && state[0] != 'X' && atoi(parent) == getpid()) ++count;
    }
    closedir(d);
    return self_seen && !unavailable ? count : -1;
}
static int thread_identities(FILE *f) {
    DIR *d = opendir("/proc/self/task");
    int count = 0;
    struct exiting_task { long tid; unsigned long long start, flags; char state; } exiting[1024];
    int exiting_count = 0;
    fputc('[', f);
    if (d) {
        struct dirent *e;
        while ((e = readdir(d))) {
            if (e->d_name[0] < '0' || e->d_name[0] > '9') continue;
            char path[300], raw[4096];
            snprintf(path, sizeof(path), "/proc/self/task/%s/stat", e->d_name);
            FILE *s = fopen(path, "r");
            if (!s) continue;
            size_t n = fread(raw, 1, sizeof(raw)-1, s);
            fclose(s);
            raw[n] = 0;
            char *end = strrchr(raw, ')'), *save = NULL;
            if (!end || end[1] != ' ') continue;
            char *field = strtok_r(end+2, " ", &save);
            if (!field || field[0] == 'Z' || field[0] == 'X') continue;
            const char state = field[0];
            unsigned long long flags = 0;
            for (int i = 1; i <= 19 && field; ++i) {
                field = strtok_r(NULL, " ", &save);
                if (i == 6 && field) flags = strtoull(field, NULL, 10);
            }
            if (!field) continue;
            /* Linux PF_EXITING: userspace has ended even when clear_child_tid
               has woken pthread_join before /proc removal/state changes. */
            if (flags & 0x00000004ULL) {
                if (exiting_count < 1024) exiting[exiting_count++] = (struct exiting_task){
                    strtol(e->d_name, NULL, 10), strtoull(field, NULL, 10), flags, state};
                continue;
            }
            if (count++) fputc(',', f);
            fprintf(f, "{\"tid\":%s,\"tgid\":%d,\"start_ticks\":%llu,\"state\":\"%c\",\"flags\":%llu}",
                    e->d_name, getpid(), strtoull(field, NULL, 10), state, flags);
        }
        closedir(d);
    }
    fputc(']', f);
    fputs(",\"kernel_exiting_threads\":[", f);
    for (int i = 0; i < exiting_count; ++i) {
        if (i) fputc(',', f);
        fprintf(f, "{\"tid\":%ld,\"start_ticks\":%llu,\"flags\":%llu,\"state\":\"%c\"}",
                exiting[i].tid, exiting[i].start, exiting[i].flags, exiting[i].state);
    }
    fputc(']', f);
    return count ? count : -1; /* No fabricated zero when self metadata is absent. */
}
static void quoted(FILE *f, const char *s) {
    fputc('"', f);
    for (; *s; ++s) {
        unsigned char c = *s;
        if (c == '"' || c == '\\') fprintf(f, "\\%c", c);
        else if (c < 32) fprintf(f, "\\u%04x", c);
        else fputc(c, f);
    }
    fputc('"', f);
}
__attribute__((destructor)) static void final_state(void) {
    const int rounding = fegetround();
#if defined(__SSE__)
    /* Exclude sticky exception flags: observe rounding, FTZ and DAZ controls. */
    const unsigned int mxcsr_control = _mm_getcsr() & 0xe040u;
#endif
    const char *file = getenv("OPTIMBENCH_STATE_FILE");
    if (!file) return;
    FILE *f = fopen(file, "a");
    if (!f) return;
    fprintf(f, "{\"fp_rounding_mode\":%d,\"mxcsr_control\":", rounding);
#if defined(__SSE__)
    fprintf(f, "%u", mxcsr_control);
#else
    fprintf(f, "null");
#endif
    fprintf(f, ",\"thread_identity\":{\"pid\":%d,\"boundary\":\"native-destructor\",\"step\":", getpid());
    const char *step = getenv("OPTIMBENCH_STATE_STEP");
    if (step) quoted(f, step); else fputs("null", f);
    struct timespec sample;
    fprintf(f, ",\"sampled_monotonic_ns\":");
    if (clock_gettime(CLOCK_MONOTONIC, &sample) == 0)
        fprintf(f, "%llu", (unsigned long long)sample.tv_sec * 1000000000ULL + sample.tv_nsec);
    else fputs("null", f);
    fprintf(f, ",\"threads\":");
    const int count = thread_identities(f);
    fprintf(f, "},\"threads\":");
    if (count < 0) fputs("null", f); else fprintf(f, "%d", count);
    fprintf(f, ",\"environment\":[");
    for (int i=0; environ[i]; i++) { if (i) fputc(',', f); quoted(f, environ[i]); }
    fprintf(f, "],\"children\":");
    const int children = live_children();
    if (children < 0) fputs("null", f); else fprintf(f, "%d", children);
    fprintf(f, ",\"children_method\":\"private-proc-ppid-live-scan\"}\n");
    fclose(f);
}

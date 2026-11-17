/* Authored CLI fault calibration, not task/candidate source or a task oracle.
 * This fixture promises exit 2 + stderr on write/flush failure and deliberately
 * ignores SIGPIPE. Actual CLIs must retain their own documented signal policy. */
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#ifndef IGNORE_OUTPUT_ERRORS
#define IGNORE_OUTPUT_ERRORS 0
#endif
#ifndef REWRITE_BEFORE_FLUSH
#define REWRITE_BEFORE_FLUSH 0
#endif

static int rewrite(const char *path)
{
    FILE *file = fopen(path, "wb");
    if (!file)
        return 3;
    if (fputs("rewritten\n", file) == EOF) {
        fclose(file);
        return 3;
    }
    return fclose(file) == 0 ? 0 : 3;
}

int main(int argc, char **argv)
{
    static char buffer[8192];
    char payload[65536];
    size_t length = argc > 1 && strcmp(argv[1], "large") == 0 ? sizeof(payload) : 7;
    int write_failed;
    int flush_failed;
    signal(SIGPIPE, SIG_IGN);
    if (argc > 1 && strcmp(argv[1], "closed") == 0)
        close(STDOUT_FILENO);
    if (setvbuf(stdout, buffer, _IOFBF, sizeof(buffer)) != 0)
        return 3;
    memset(payload, 'x', sizeof(payload));
    write_failed = fwrite(payload, 1, length, stdout) != length;
    if (write_failed && !IGNORE_OUTPUT_ERRORS) {
        perror("write");
        return 2;
    }
    if (argc > 2 && REWRITE_BEFORE_FLUSH && rewrite(argv[2]) != 0)
        return 3;
    flush_failed = fflush(stdout) != 0;
    if (flush_failed && !IGNORE_OUTPUT_ERRORS) {
        perror("flush");
        return 2;
    }
    if (argc > 2 && !REWRITE_BEFORE_FLUSH && rewrite(argv[2]) != 0)
        return 3;
    return 0;
}

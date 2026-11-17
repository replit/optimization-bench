#include <sched.h>
#include <unistd.h>
#include <string>
#include <vector>
#include "/verifier-native-lifecycle.hpp"

#define main jq_probe_main
#include "probe.c"
#undef main

int main(int argc, char **argv) {
    if (argc < 4 || (argc - 1) % 3) return 98;
    for (int i = 1; i < argc; i += 3) {
        std::string before = optimbench_lifecycle::snapshot();
        FILE *capture = tmpfile();
        int saved = dup(STDOUT_FILENO);
        if (!capture || saved < 0) return 99;
        fflush(stdout);
        if (dup2(fileno(capture), STDOUT_FILENO) < 0) return 99;
        char *args[] = {argv[0], argv[i], argv[i + 1], argv[i + 2], NULL};
        comma = 0;
        int code = jq_probe_main(4, args);
        fflush(stdout);
        if (dup2(saved, STDOUT_FILENO) < 0 || close(saved)) return 99;
        rewind(capture);
        std::string events;
        char buffer[4096];
        size_t n;
        while ((n = fread(buffer, 1, sizeof(buffer), capture))) {
            events.append(buffer, n);
            if (events.size() > 1024 * 1024) return 99;
        }
        if (ferror(capture) || fclose(capture) || code) return 99;
        while (!events.empty() && (events.back() == '\n' || events.back() == '\r'))
            events.pop_back();
        std::string after = optimbench_lifecycle::snapshot();
        cpu_set_t affinity;
        CPU_ZERO(&affinity);
        if (sched_getaffinity(0, sizeof(affinity), &affinity)) return 99;
        printf("{\"pid\":%ld,\"execution_affinity\":[", (long)getpid());
        bool separator = false;
        for (int cpu = 0; cpu < CPU_SETSIZE; ++cpu) {
            if (!CPU_ISSET(cpu, &affinity)) continue;
            printf("%s%d", separator ? "," : "", cpu);
            separator = true;
        }
        printf("],\"before\":%s,\"after\":%s,\"observation\":%s}\n",
               before.c_str(), after.c_str(), events.c_str());
        fflush(stdout);
    }
    return 0;
}

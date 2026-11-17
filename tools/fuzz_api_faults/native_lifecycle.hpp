// Verifier-only per-call Linux facts. Missing/racing proc evidence is unavailable,
// never an invented zero or a task-specific lifecycle exemption.
#pragma once
#include <dirent.h>
#include <unistd.h>
#include <fstream>
#include <sstream>
#include <set>
#include <string>
#include <vector>
#include <map>

namespace optimbench_lifecycle {
inline std::string snapshot_once() {
    bool available = true;
    int fds = -1;
    if (DIR* directory = opendir("/proc/self/fd")) {
        fds = -1; // The sampler's own directory descriptor is not worker state.
        while (auto* entry = readdir(directory))
            if (entry->d_name[0] != '.') ++fds;
        closedir(directory);
    } else available = false;
    std::vector<std::string> identities;
    std::set<unsigned long long> children;
    if (DIR* directory = opendir("/proc/self/task")) {
        while (auto* entry = readdir(directory)) {
            if (entry->d_name[0] == '.') continue;
            try {
                const std::string base = std::string("/proc/self/task/") + entry->d_name;
                std::ifstream stat(base + "/stat");
                std::string text;
                std::getline(stat, text);
                auto close = text.rfind(')');
                if (!stat || close == std::string::npos) { available = false; continue; }
                std::istringstream fields(text.substr(close + 2));
                std::vector<std::string> values;
                while (fields >> text) values.push_back(text);
                if (values.size() < 20) { available = false; continue; }
                auto flags = std::stoull(values[6]);
                auto ticks = std::stoull(values[19]);
                if (values[0].size() != 1) { available = false; continue; }
                // Kernel-exiting/dead tasks are not live worker leaks.
                if (values[0] == "Z" || values[0] == "X" ||
                    values[0] == "x" || (flags & 4)) continue;
                identities.push_back("{\"tid\":" + std::to_string(std::stoull(entry->d_name)) +
                    ",\"scope_pid\":" + std::to_string(getpid()) +
                    ",\"start_ticks\":" + std::to_string(ticks) +
                    ",\"flags\":" + std::to_string(flags) + ",\"state\":\"" + values[0] + "\"}");
            } catch (...) { available = false; }
        }
        closedir(directory);
    } else available = false;
    // PPID closure is measured from real proc identities, including kernels
    // without /proc/<pid>/task/<tid>/children (not a fabricated empty list).
    std::map<unsigned long long, unsigned long long> parents;
    if (DIR* directory = opendir("/proc")) {
        while (auto* entry = readdir(directory)) {
            if (entry->d_name[0] < '0' || entry->d_name[0] > '9') continue;
            try {
                std::ifstream stat(std::string("/proc/") + entry->d_name + "/stat");
                std::string text;
                if (!std::getline(stat, text)) {
                    if (access((std::string("/proc/") + entry->d_name).c_str(), F_OK) == 0)
                        available = false;
                    continue;
                }
                auto close = text.rfind(')');
                if (close == std::string::npos) { available = false; continue; }
                std::istringstream fields(text.substr(close + 2));
                std::string state;
                unsigned long long parent;
                if (!(fields >> state >> parent)) { available = false; continue; }
                if (state != "Z" && state != "X" && state != "x")
                    parents[std::stoull(entry->d_name)] = parent;
            } catch (...) { available = false; }
        }
        closedir(directory);
    } else available = false;
    bool changed = true;
    while (changed && children.size() < 4096) {
        changed = false;
        for (auto row : parents)
            if (row.first != static_cast<unsigned long long>(getpid()) &&
                (row.second == static_cast<unsigned long long>(getpid()) || children.count(row.second)))
                changed = children.insert(row.first).second || changed;
    }
    if (children.size() >= 4096) available = false;
    if (identities.empty()) available = false;
    std::string threads = "[";
    for (const auto& row : identities) { if (threads.size() > 1) threads += ','; threads += row; }
    return "{\"available\":" + std::string(available ? "true" : "false") +
        ",\"fds\":" + std::to_string(fds) + ",\"threads\":" + std::to_string(identities.size()) +
        ",\"children\":" + std::to_string(children.size()) +
        ",\"children_source\":\"proc-ppid-closure\",\"thread_identities\":" + threads + "]}";
}
inline std::string snapshot() {
    std::string observed;
    for (int attempt = 0; attempt < 3; ++attempt) {
        observed = snapshot_once();
        if (observed.find("\"available\":true") != std::string::npos) return observed;
        // Retry a racing proc inventory, never synthesize missing identities.
        if (attempt < 2) usleep(1000);
    }
    return observed;
}
}

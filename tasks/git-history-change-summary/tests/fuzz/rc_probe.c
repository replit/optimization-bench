/* Verifier observer: calls the admitted build's xdiff code in this process. */
#define _GNU_SOURCE 1
#include "git-compat-util.h"
#include "xdiff/xdiff.h"
#include <dirent.h>
#include <sched.h>
#include <unistd.h>

struct sink { char data[65536]; size_t used; int fail; };

static int emit(void *opaque, mmbuffer_t *buffers, int count)
{
	struct sink *s = opaque;
	int i;
	if (s->fail)
		return -1;
	for (i = 0; i < count; i++) {
		if (buffers[i].size < 0 ||
		    (size_t)buffers[i].size > sizeof(s->data) - s->used)
			exit(70);
		memcpy(s->data + s->used, buffers[i].ptr, buffers[i].size);
		s->used += buffers[i].size;
	}
	return 0;
}

static void hex(const char *data, size_t size)
{
	size_t i;
	putchar('"');
	for (i = 0; i < size; i++)
		printf("%02x", (unsigned char)data[i]);
	putchar('"');
}

static int entries(const char *path)
{
	DIR *d = opendir(path);
	struct dirent *e;
	int count = 0;
	if (!d)
		exit(71);
	while ((e = readdir(d)))
		if (e->d_name[0] != '.')
			count++;
	closedir(d);
	return count;
}

static void snapshot(void)
{
	DIR *d;
	struct dirent *e;
	char path[160], line[8192], *tail, *p;
	int first = 1, threads = entries("/proc/self/task"), children = 0;
	FILE *f;
	/* Exclude the observer's transient directory descriptor from fd counts. */
	int fds = entries("/proc/self/fd") - 1;
	printf("{\"fds\":%d,\"threads\":%d,\"thread_identities\":[", fds, threads);
	d = opendir("/proc/self/task");
	if (!d)
		exit(71);
	while ((e = readdir(d))) {
		unsigned long long start = 0, flags = 0;
		long tid;
		char state = 0;
		int field;
		if (e->d_name[0] == '.')
			continue;
		tid = strtol(e->d_name, NULL, 10);
		snprintf(path, sizeof(path), "/proc/self/task/%ld/stat", tid);
		f = fopen(path, "r");
		if (!f || !fgets(line, sizeof(line), f))
			exit(71);
		fclose(f);
		tail = strrchr(line, ')');
		if (!tail)
			exit(71);
		for (field = 3, p = tail + 2; *p; field++) {
			if (field == 3) state = p[0];
			if (field == 9) flags = strtoull(p, NULL, 10);
			if (field == 22) start = strtoull(p, NULL, 10);
			while (*p && *p != ' ')
				p++;
			while (*p == ' ')
				p++;
		}
		printf("%s{\"tid\":%ld,\"start_ticks\":%llu,\"state\":\"%c\","
		       "\"flags\":%llu,\"scope_pid\":%ld,\"tgid\":%ld}",
		       first ? "" : ",", tid, start, state, flags,
		       (long)getpid(), (long)getpid());
		first = 0;
	}
	closedir(d);
	/* Some isolated kernels omit task/children; enumerate visible PPids. */
	d = opendir("/proc");
	if (!d)
		exit(71);
	while ((e = readdir(d))) {
		long parent;
		char state;
		if (e->d_name[0] < '0' || e->d_name[0] > '9')
			continue;
		snprintf(path, sizeof(path), "/proc/%s/stat", e->d_name);
		f = fopen(path, "r");
		if (!f)
			continue; /* Another process may have exited during enumeration. */
		if (fgets(line, sizeof(line), f)) {
			tail = strrchr(line, ')');
			if (!tail || sscanf(tail + 2, "%c %ld", &state, &parent) != 2)
				exit(71);
			if (parent == (long)getpid())
				children++;
		}
		fclose(f);
	}
	closedir(d);
	printf("],\"children\":%d}", children);
}

static void affinity(void)
{
	cpu_set_t mask;
	int cpu, first = 1;
	if (sched_getaffinity(0, sizeof(mask), &mask))
		exit(71);
	printf("[");
	for (cpu = 0; cpu < CPU_SETSIZE; cpu++)
		if (CPU_ISSET(cpu, &mask)) {
			printf("%s%d", first ? "" : ",", cpu);
			first = 0;
		}
	printf("]");
}

int main(void)
{
	int scenario, fail;
	unsigned long flags;
	/* A/B/A requests are consumed in one native target process. */
	while (scanf("%d %lu %d", &scenario, &flags, &fail) == 3) {
		char a[] = "alpha\nbeta\nsame\nomega\n";
		char b[] = "alpha\nBETA\nsame\nlast\n";
		char base[] = "alpha\nbeta\nsame\nomega\n";
		char original_a[sizeof(a)], original_b[sizeof(b)];
		mmfile_t left = { a, sizeof(a) - 1 }, right = { b, sizeof(b) - 1 };
		mmfile_t ancestor = { base, sizeof(base) - 1 };
		struct sink s = {{0}, 0, fail};
		xpparam_t params = {0};
		xdemitconf_t config = {0};
		xdemitcb_t callback = {0};
		int rc, immutable, retained = 1, second_rc = 0;
		mmbuffer_t merged = {0}, other = {0};
		char *saved = NULL;
		if (scenario < 0 || scenario > 3 || (fail != 0 && fail != 1))
			return 72;
		if (scenario == 1)
			left.size = 0;
		if (scenario == 2)
			right.size = sizeof(b) - 2; /* legal missing final newline */
		memcpy(original_a, a, sizeof(a));
		memcpy(original_b, b, sizeof(b));
		params.flags = flags;
		config.ctxlen = 3;
		callback.priv = &s;
		callback.out_line = emit;
		printf("{\"pid\":%ld,\"before\":", (long)getpid());
		snapshot();
		if (scenario == 3) {
			xmparam_t mp = {0};
			mp.xpp = params;
			mp.marker_size = DEFAULT_CONFLICT_MARKER_SIZE;
			mp.level = XDL_MERGE_ZEALOUS;
			mp.ancestor = "base";
			mp.file1 = "left";
			mp.file2 = "right";
			rc = xdl_merge(&ancestor, &left, &right, &mp, &merged);
			if (merged.size < 0 || merged.size > 65536)
				exit(70);
			saved = malloc(merged.size ? (size_t)merged.size : 1);
			if (!saved)
				exit(70);
			if (merged.size)
				memcpy(saved, merged.ptr, merged.size);
			second_rc = xdl_merge(&ancestor, &right, &left, &mp, &other);
			retained = !merged.size || !memcmp(saved, merged.ptr, merged.size);
			if (merged.size)
				memcpy(s.data, saved, merged.size);
			s.used = merged.size;
			free(other.ptr);
			free(merged.ptr);
			free(saved);
		} else {
			rc = xdl_diff(&left, &right, &params, &config, &callback);
		}
		immutable = !memcmp(a, original_a, sizeof(a)) &&
			    !memcmp(b, original_b, sizeof(b));
		printf(",\"after\":");
		snapshot();
		printf(",\"execution_affinity\":");
		affinity();
		printf(",\"return_code\":%d,\"second_return_code\":%d,"
		       "\"inputs_unchanged\":%s,\"retained_output_unchanged\":%s,"
		       "\"output_hex\":", rc, second_rc,
		       immutable ? "true" : "false", retained ? "true" : "false");
		hex(s.data, s.used);
		printf("}\n");
		fflush(stdout);
	}
	return ferror(stdin) ? 73 : 0;
}

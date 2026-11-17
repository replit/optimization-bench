// Protected public-library observer. No task-side verdicts or corpus answers.
package main

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"runtime/pprof"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/dimitri/sqlfmt/format"
)

type fault struct {
	Kind  string `json:"kind"`
	After int    `json:"after"`
}
type inputCase struct {
	Task, SurfaceID, CaseSHA string
	Input                   struct{ SQL string `json:"sql_utf8"` } `json:"input"`
	Options                 struct {
		Mode  string `json:"mode"`
		Chunk int    `json:"chunk"`
	} `json:"options"`
	Faults []fault `json:"faults"`
}

func (c *inputCase) UnmarshalJSON(data []byte) error {
	type plain inputCase
	var v struct {
		*plain
		Task       string `json:"task"`
		SurfaceID  string `json:"surface_id"`
		CaseSHA256 string `json:"case_sha256"`
	}
	v.plain = (*plain)(c)
	if err := json.Unmarshal(data, &v); err != nil { return err }
	c.Task, c.SurfaceID, c.CaseSHA = v.Task, v.SurfaceID, v.CaseSHA256
	return nil
}

func digest(data []byte) string {
	value := sha256.Sum256(data)
	return hex.EncodeToString(value[:])
}

// procfs reports affinity of THIS native API worker, not its Python parent.
func affinity() map[string]any {
	raw, err := os.ReadFile("/proc/self/status")
	if err != nil { return map[string]any{"available": false, "method": "procfs Cpus_allowed_list"} }
	cpus := []int{}
	for _, line := range strings.Split(string(raw), "\n") {
		if !strings.HasPrefix(line, "Cpus_allowed_list:") { continue }
		for _, item := range strings.Split(strings.TrimSpace(strings.SplitN(line, ":", 2)[1]), ",") {
			pair := strings.SplitN(item, "-", 2)
			first, e := strconv.Atoi(pair[0]); if e != nil { continue }
			last := first
			if len(pair) == 2 { last, _ = strconv.Atoi(pair[1]) }
			for cpu := first; cpu <= last; cpu++ { cpus = append(cpus, cpu) }
		}
	}
	return map[string]any{"available": len(cpus) > 0, "method": "procfs Cpus_allowed_list", "cpus": cpus, "pid": os.Getpid()}
}

func snapshot(phase string) map[string]any {
	threads, children := []map[string]any{}, []map[string]any{}
	fds := []map[string]any{}
	entries, err := os.ReadDir("/proc/self/task")
	available := err == nil
	for _, entry := range entries {
		tid, e := strconv.Atoi(entry.Name()); if e != nil { continue }
		raw, e := os.ReadFile("/proc/self/task/"+entry.Name()+"/stat"); if e != nil { available = false; continue }
		end := strings.LastIndex(string(raw), ")"); if end < 0 { available = false; continue }
		fields := strings.Fields(string(raw)[end+1:])
		if len(fields) < 20 { available = false; continue }
		if fields[0] == "Z" || fields[0] == "X" || fields[0] == "x" { continue }
		ticks, e := strconv.ParseInt(fields[19], 10, 64); if e != nil { available = false; continue }
		flags, e := strconv.ParseUint(fields[6], 10, 64); if e != nil { available = false; continue }
		threads = append(threads, map[string]any{"tid": tid, "start_ticks": ticks, "flags": flags,
			"state": fields[0], "tgid": os.Getpid(), "scope_pid": os.Getpid()})
	}
	entries, err = os.ReadDir("/proc/self/fd"); available = available && err == nil
	for _, entry := range entries {
		target, e := os.Readlink("/proc/self/fd/"+entry.Name()); if e != nil { continue }
		number, _ := strconv.Atoi(entry.Name())
		fds = append(fds, map[string]any{"fd": number, "target_sha256": digest([]byte(target))})
	}
	entries, err = os.ReadDir("/proc"); available = available && err == nil
	for _, entry := range entries {
		pid, e := strconv.Atoi(entry.Name()); if e != nil { continue }
		raw, e := os.ReadFile(filepath.Join("/proc", entry.Name(), "stat")); if e != nil { continue }
		end := strings.LastIndex(string(raw), ")"); if end < 0 { continue }
		fields := strings.Fields(string(raw)[end+1:]); if len(fields) < 20 { continue }
		parent, _ := strconv.Atoi(fields[1]); if parent != os.Getpid() { continue }
		ticks, _ := strconv.ParseInt(fields[19], 10, 64)
		children = append(children, map[string]any{"pid": pid, "start_ticks": ticks, "state": fields[0]})
	}
	result := map[string]any{"available": available, "phase": phase, "scope_pid": os.Getpid(),
		"sample_unix_ns": time.Now().UnixNano(), "fds": len(fds), "fd_identities": fds,
		"threads": len(threads), "thread_identities": threads, "children": len(children), "child_identities": children,
		"goroutines": runtime.NumGoroutine(), "gomaxprocs": runtime.GOMAXPROCS(0)}
	if phase == "after-call" {
		var profile bytes.Buffer
		err := pprof.Lookup("goroutine").WriteTo(&profile, 2)
		result["goroutine_profile_available"] = err == nil
		result["goroutine_profile_raw"] = profile.String()
		result["goroutine_profile_sha256"] = digest(profile.Bytes())
		result["goroutine_profile_method"] = "runtime/pprof.Lookup(goroutine).WriteTo(debug=2)"
	}
	return result
}

type reader struct {
	data []byte
	offset, chunk int
	faults []fault
}
func (r *reader) Read(out []byte) (int, error) {
	if len(r.faults) > 0 && r.offset >= r.faults[0].After {
		if r.faults[0].Kind == "EINTR" { return 0, syscall.EINTR }
		return 0, syscall.EIO
	}
	if r.offset == len(r.data) { return 0, io.EOF }
	size := len(out)
	if r.chunk > 0 && size > r.chunk { size = r.chunk }
	if size > len(r.data)-r.offset { size = len(r.data)-r.offset }
	if len(r.faults) > 0 && size > r.faults[0].After-r.offset { size = r.faults[0].After-r.offset }
	copy(out, r.data[r.offset:r.offset+size]); r.offset += size
	return size, nil
}

func execute(c inputCase) (obs map[string]any) {
	before := snapshot("before-call")
	obs = map[string]any{"format": "optimbench-fuzz-observation-v1",
		"identity": map[string]any{"task": c.Task, "surface_id": c.SurfaceID, "case_sha256": c.CaseSHA,
			"build_sha256": os.Getenv("OPTIMBENCH_ADMITTED_BUILD_SHA256")},
		"termination": map[string]any{"kind": "return", "exit_code": 0}}
	defer func() {
		if value := recover(); value != nil {
			obs["termination"] = map[string]any{"kind": "panic", "exit_code": nil}
			obs["outcome"] = map[string]any{"kind": "error", "class": "panic", "message": fmt.Sprint(value)}
			obs["effects"] = map[string]any{}
		}
		obs["diagnostics"] = map[string]any{"execution_affinity": affinity(),
			"call_lifecycle": map[string]any{"before": before, "after": snapshot("after-call")}}
	}()
	if c.Options.Mode == "lex_alias" {
		source := []byte(c.Input.SQL)
		tokens, err := format.Lex(source)
		if err != nil {
			obs["outcome"] = map[string]any{"kind": "error", "class": fmt.Sprintf("%T", err), "message": err.Error()}
			obs["effects"] = map[string]any{}
			return
		}
		initial, _ := json.Marshal(tokens)
		for i := range source { source[i] = 'x' }
		other, otherErr := format.Lex([]byte("select independent from other_table;"))
		retained, _ := json.Marshal(tokens)
		if len(other) > 0 { other[0].Text = "caller-mutated-token" }
		fresh, freshErr := format.Lex([]byte(c.Input.SQL))
		obs["outcome"] = map[string]any{"kind": "ok", "value": nil}
		obs["effects"] = map[string]any{"initial_tokens_json": string(initial), "retained_tokens_json": string(retained),
			"fresh_tokens": fresh, "other_error": otherErr != nil, "fresh_error": freshErr != nil}
		return
	}
	r := &reader{data: []byte(c.Input.SQL), chunk: c.Options.Chunk, faults: c.Faults}
	output, err := format.Format(r)
	outcome := map[string]any{"kind": "ok", "value": nil}
	if err != nil {
		var errno syscall.Errno
		if errors.As(err, &errno) {
			outcome = map[string]any{"kind": "error", "class": "os-error", "errno": int(errno), "operation": "read"}
		} else {
			outcome = map[string]any{"kind": "error", "class": fmt.Sprintf("%T", err), "message": err.Error()}
		}
	}
	obs["outcome"] = outcome
	obs["effects"] = map[string]any{"returned_utf8": output, "reader_bytes_accepted": r.offset}
	return
}

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 4096), 4<<20)
	encoder := json.NewEncoder(os.Stdout)
	for scanner.Scan() {
		var c inputCase
		if err := json.Unmarshal(scanner.Bytes(), &c); err != nil { panic(err) }
		if err := encoder.Encode(execute(c)); err != nil { panic(err) }
	}
	if err := scanner.Err(); err != nil { panic(err) }
}

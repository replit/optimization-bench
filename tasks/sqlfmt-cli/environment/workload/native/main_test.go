package main

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestResultPreservesBytesAndNilError(t *testing.T) {
	raw, err := json.Marshal(result{ID: "invalid-utf8", Output: []byte{0xff, 0, 0xfe}})
	if err != nil {
		t.Fatal(err)
	}
	want := `{"id":"invalid-utf8","output_base64":"/wD+","error_base64":null}`
	if string(raw) != want {
		t.Fatalf("lossy output or error identity: got %s", raw)
	}
	raw, err = json.Marshal(result{ID: "empty-error", Output: []byte{}, Error: []byte{}})
	if err != nil {
		t.Fatal(err)
	}
	want = `{"id":"empty-error","output_base64":"","error_base64":""}`
	if string(raw) != want {
		t.Fatalf("empty error collapsed into nil: got %s", raw)
	}
}

func executable(t *testing.T) string {
	t.Helper()
	binary := filepath.Join(t.TempDir(), "native")
	cmd := exec.Command("go", "build", "-o", binary, ".")
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("build native test caller: %v: %s", err, output)
	}
	return binary
}

func TestMalformedRequestsAreRejected(t *testing.T) {
	binary := executable(t)
	for _, request := range []string{
		`[]`,
		`null`,
		`invalid`,
		`[{"id":"q","sql":"c2VsZWN0IDE7","extra":true}]`,
		`[{"id":"q","sql":"c2VsZWN0IDE7"}] {}`,
		`[{"id":"q","sql":"c2VsZWN0IDE7"},{"id":"q","sql":"c2VsZWN0IDI7"}]`,
	} {
		cmd := exec.Command(binary)
		cmd.Stdin = strings.NewReader(request)
		var out, stderr bytes.Buffer
		cmd.Stdout, cmd.Stderr = &out, &stderr
		if err := cmd.Run(); err == nil || cmd.ProcessState.ExitCode() != 2 {
			t.Fatalf("malformed request was not rejected: %q", request)
		}
		if out.Len() != 0 || stderr.Len() == 0 {
			t.Fatalf("partial success or missing error for %q", request)
		}
	}
}

func TestOutputWriteFailureIsRejected(t *testing.T) {
	full, err := os.OpenFile("/dev/full", os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer full.Close()
	cmd := exec.Command(executable(t))
	cmd.Stdin = strings.NewReader(`[{"id":"q","sql":"c2VsZWN0IDE7"}]`)
	cmd.Stdout = full
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err == nil || cmd.ProcessState.ExitCode() != 2 || stderr.Len() == 0 {
		t.Fatalf("write failure escaped: exit=%v stderr=%s", err, stderr.String())
	}
}

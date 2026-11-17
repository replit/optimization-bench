package branchprobe

import (
	"bytes"
	"encoding/json"
	"sync"
	"testing"
)

func TestConcurrentCountersAndZeroEdges(t *testing.T) {
	c, err := New([]Branch{{File:"target.go", Line:2, ID:"if2-true"}, {File:"target.go", Line:2, ID:"if2-false"}})
	if err != nil { t.Fatal(err) }
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); for j := 0; j < 100; j++ { c.Hit("target.go", "if2-true") } }()
	}
	wg.Wait()
	var out bytes.Buffer
	if err := c.Write(&out); err != nil { t.Fatal(err) }
	var result struct { Schema string; Branches []Branch }
	if err := json.Unmarshal(out.Bytes(), &result); err != nil { t.Fatal(err) }
	if result.Schema != "optimbench-go-branches-v1" || result.Branches[0].Count != 800 || result.Branches[1].Count != 0 { t.Fatal(result) }
}

func TestRejectInvalidInventory(t *testing.T) {
	for _, input := range [][]Branch{
		{{File:"target.go", Line:2, ID:"same"}, {File:"target.go", Line:2, ID:"same"}},
		{{File:"target.go", Line:0, ID:"edge"}},
		{{File:"target.go", Line:2, ID:"edge", Count:1}},
	} {
		if _, err := New(input); err == nil { t.Fatal("accepted invalid inventory") }
	}
}

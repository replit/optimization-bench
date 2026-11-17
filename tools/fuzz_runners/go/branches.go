// Package branchprobe is an opt-in verifier build dependency.
//
// A task-owned AST-aware instrumenter must declare all branch outcome identities,
// bind the instrumented source to its pristine/candidate input hashes, and insert
// Hit calls on every edge (including false/no-else/loop-zero edges). Hit alone
// does not establish inventory completeness. No production/timing source changes.
package branchprobe

import (
	"encoding/json"
	"fmt"
	"io"
	"sync"
)

type Branch struct {
	File  string `json:"file"`
	Line  int    `json:"line"`
	ID    string `json:"id"`
	Count uint64 `json:"count"`
}

type Counters struct {
	mu sync.Mutex
	branches []Branch
	index map[string]int
}

func New(inventory []Branch) (*Counters, error) {
	c := &Counters{branches: append([]Branch(nil), inventory...), index: map[string]int{}}
	for i, b := range c.branches {
		key := b.File + "\x00" + b.ID
		if b.File == "" || b.ID == "" || b.Line < 1 || b.Count != 0 {
			return nil, fmt.Errorf("invalid branch inventory")
		}
		if _, found := c.index[key]; found {
			return nil, fmt.Errorf("duplicate branch identity")
		}
		c.index[key] = i
	}
	return c, nil
}

func (c *Counters) Hit(file, id string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	i, found := c.index[file + "\x00" + id]
	if !found { panic("uninventoried verifier branch") }
	c.branches[i].Count++
}

func (c *Counters) Write(w io.Writer) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	return json.NewEncoder(w).Encode(struct {
		Schema string `json:"schema"`
		Branches []Branch `json:"branches"`
	}{"optimbench-go-branches-v1", c.branches})
}

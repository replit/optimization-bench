// Verifier-only documented API exerciser; never copied to the agent image.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"

	"github.com/yuin/goldmark/v2/ast"
	"github.com/yuin/goldmark/v2/extension"
	"github.com/yuin/goldmark/v2/parser"
	"github.com/yuin/goldmark/v2/renderer/html"
)

type request struct {
	Sources []string `json:"sources"`
}
type observation struct {
	Mode      int    `json:"mode"`
	Operation string `json:"operation"`
	Output    []byte `json:"output"`
	ErrorType string `json:"error_type"`
	Error     string `json:"error"`
}
type failingWriter struct{}

func (failingWriter) Write(p []byte) (int, error) {
	return 0, errors.New("verifier writer failure")
}
func render(r html.Renderer, source []byte, doc ast.Node, mode int, operation string) observation {
	var out bytes.Buffer
	err := r.Render(&out, source, doc)
	result := observation{Mode: mode, Operation: operation, Output: out.Bytes()}
	if err != nil {
		result.ErrorType, result.Error = fmt.Sprintf("%T", err), err.Error()
	}
	return result
}
func main() {
	var req request
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	if err = json.Unmarshal(data, &req); err != nil {
		panic(err)
	}
	results := []observation{}
	for mode := 0; mode < 32; mode++ {
		popts := []parser.Option{}
		ropts := []html.Option{}
		if mode&1 != 0 {
			popts = append(popts, parser.WithExtensions(extension.NewGFMParser()))
			ropts = append(ropts, html.WithExtensions(extension.NewGFMHTMLRenderer()))
		}
		if mode&2 != 0 {
			ropts = append(ropts, html.WithUnsafe())
		}
		if mode&4 != 0 {
			ropts = append(ropts, html.WithHardWraps())
		}
		if mode&8 != 0 {
			ropts = append(ropts, html.WithXHTML())
		}
		if mode&16 != 0 {
			popts = append(popts, parser.WithAutoHeadingID(), parser.WithAttribute())
		}
		p, r := parser.New(popts...), html.New(ropts...)
		ctx := parser.NewContext()
		for i, text := range req.Sources {
			source := []byte(text)
			doc := p.Parse(source, parser.WithContext(ctx))
			results = append(results, render(r, source, doc, mode, fmt.Sprintf("reuse-%d", i)))
			independent := parser.New(popts...).Parse(source, parser.WithContext(parser.NewContext()))
			results = append(results, render(html.New(ropts...), source, independent, mode, fmt.Sprintf("fresh-%d", i)))
			// Returned AST is caller-owned. Mutate a heading, parse an unrelated
			// document, then render the original AST again: no cached-node aliasing.
			_ = ast.Walk(doc, func(node ast.Node, entering bool) (ast.WalkStatus, error) {
				if entering {
					if heading, ok := node.(*ast.Heading); ok {
						heading.Level = 2
						heading.RemoveAttributes()
					}
				}
				return ast.WalkContinue, nil
			})
			results = append(results, render(r, source, doc, mode, "mutated"))
			_ = p.Parse([]byte("# independent\n\nother\n"), parser.WithContext(parser.NewContext()))
			results = append(results, render(r, source, doc, mode, "mutated-after-interleave"))
			err := r.Render(failingWriter{}, source, doc)
			result := observation{Mode: mode, Operation: "writer-error"}
			if err != nil {
				result.ErrorType, result.Error = fmt.Sprintf("%T", err), err.Error()
			}
			results = append(results, result)
		}
	}
	if err := json.NewEncoder(os.Stdout).Encode(results); err != nil {
		panic(err)
	}
}

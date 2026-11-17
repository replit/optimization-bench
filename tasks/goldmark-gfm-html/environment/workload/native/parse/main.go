package main

// Parse-only workload: a documentation site's metadata pass (table of contents,
// link check, image and code-block inventory). It parses each complete document
// with heading IDs, GFM and footnotes and walks the AST, without HTML rendering.
// Records per document, one per line, in document order:
//   H level id pos | L pos dest | I pos dest | A pos dest | C pos lang lines | F refs defs
import (
	"bufio"
	"bytes"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/yuin/goldmark/v2/ast"
	"github.com/yuin/goldmark/v2/extension"
	east "github.com/yuin/goldmark/v2/extension/ast"
	"github.com/yuin/goldmark/v2/parser"
)

func extract(p parser.Parser, source []byte) ([]byte, error) {
	var out bytes.Buffer
	doc := p.Parse(source)
	refs, defs := 0, 0
	err := ast.Walk(doc, func(n ast.Node, entering bool) (ast.WalkStatus, error) {
		if !entering {
			return ast.WalkContinue, nil
		}
		switch v := n.(type) {
		case *ast.Heading:
			id := ""
			if a, ok := v.Attribute("id"); ok {
				id = a.Value(source)
			}
			out.WriteString("H\t")
			out.WriteString(strconv.Itoa(v.Level))
			out.WriteByte('\t')
			out.WriteString(id)
			out.WriteByte('\t')
			out.WriteString(strconv.Itoa(v.Pos()))
			out.WriteByte('\n')
		case *ast.Link:
			out.WriteString("L\t")
			out.WriteString(strconv.Itoa(v.Pos()))
			out.WriteByte('\t')
			out.WriteString(v.Destination.Value(source))
			out.WriteByte('\n')
		case *ast.Image:
			out.WriteString("I\t")
			out.WriteString(strconv.Itoa(v.Pos()))
			out.WriteByte('\t')
			out.WriteString(v.Destination.Value(source))
			out.WriteByte('\n')
		case *ast.AutoLink:
			out.WriteString("A\t")
			out.WriteString(strconv.Itoa(v.Pos()))
			out.WriteByte('\t')
			out.WriteString(v.Destination.Value(source))
			out.WriteByte('\n')
		case *ast.CodeBlock:
			if v.CodeBlockKind == ast.CodeBlockKindFenced {
				lang, _ := v.Language(source)
				out.WriteString("C\t")
				out.WriteString(strconv.Itoa(v.Pos()))
				out.WriteByte('\t')
				out.WriteString(lang)
				out.WriteByte('\t')
				out.WriteString(strconv.Itoa(len(v.Value.Segments())))
				out.WriteByte('\n')
			}
		case *east.FootnoteReference:
			refs++
		case *east.FootnoteDefinition:
			defs++
		}
		return ast.WalkContinue, nil
	})
	fmt.Fprintf(&out, "F\t%d\t%d\n", refs, defs)
	return out.Bytes(), err
}

func main() {
	if len(os.Args) != 2 {
		os.Exit(2)
	}
	f, e := os.Open(os.Args[1])
	if e != nil {
		panic(e)
	}
	defer f.Close()
	// Configure once per unit; Parse creates each document's context and AST.
	p := parser.New(parser.WithAutoHeadingID(),
		parser.WithExtensions(extension.NewGFMParser(), extension.NewFootnoteParser()))
	s := bufio.NewScanner(f)
	for s.Scan() {
		row := strings.Split(s.Text(), "\t")
		if len(row) != 2 {
			os.Exit(2)
		}
		source, e := os.ReadFile(row[1])
		if e != nil {
			panic(e)
		}
		output, e := extract(p, source)
		suffix := ".out"
		if e != nil {
			output = []byte(e.Error())
			suffix = ".err"
		}
		if e = os.WriteFile(row[0]+suffix, output, 0644); e != nil {
			panic(e)
		}
	}
	if e = s.Err(); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(2)
	}
}

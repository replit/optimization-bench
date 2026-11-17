package main
import ("bytes"; "bufio"; "fmt"; "os"; "strings"
"github.com/yuin/goldmark/v2/extension"
"github.com/yuin/goldmark/v2/parser"
"github.com/yuin/goldmark/v2/renderer/html")
func convert(p parser.Parser, r html.Renderer, source []byte) ([]byte, error) {
	var output bytes.Buffer
	doc := p.Parse(source)
	err := r.Render(&output, source, doc)
	return output.Bytes(), err
}


func main() {
 if len(os.Args)!=2 { os.Exit(2) }
 f,e:=os.Open(os.Args[1]); if e!=nil { panic(e) }; defer f.Close()
 // Goldmark v2 exposes parser/renderer separately, rather than goldmark.Markdown.
 // Configure once per unit, as in a real document pipeline; Parse creates each
 // document's context/AST and convert retains a fresh per-document output buffer.
 p := parser.New(parser.WithExtensions(extension.NewGFMParser()))
 r := html.New(html.WithExtensions(extension.NewGFMHTMLRenderer()))
 s:=bufio.NewScanner(f)
 for s.Scan() {
  row:=strings.Split(s.Text(),"\t"); if len(row)!=2 { os.Exit(2) }
  source,e:=os.ReadFile(row[1]); if e!=nil { panic(e) }
  output,e:=convert(p,r,source); suffix:=".out"
  if e!=nil { output=[]byte(e.Error()); suffix=".err" }
  if e=os.WriteFile(row[0]+suffix,output,0644); e!=nil { panic(e) }
 }
 if e=s.Err(); e!=nil { fmt.Fprintln(os.Stderr,e); os.Exit(2) }
}

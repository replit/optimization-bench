// Public API worker. Reporting uses Go's standard library, never Goldmark.
package main

import (
 "bytes"
 "crypto/sha256"
 "encoding/hex"
 "encoding/base64"
 "encoding/json"
 "errors"
 "fmt"
 "io"
 "os"
 "runtime"
 "runtime/pprof"
 "strconv"
 "strings"
 "syscall"
 "time"
 "unsafe"

 "github.com/yuin/goldmark/v2/ast"
 "github.com/yuin/goldmark/v2/extension"
 "github.com/yuin/goldmark/v2/parser"
 "github.com/yuin/goldmark/v2/renderer"
 "github.com/yuin/goldmark/v2/renderer/html"
 "github.com/yuin/goldmark/v2/text"
 "github.com/yuin/goldmark/v2/util"
)
type inputCase struct {
 Task string `json:"task"`
 SurfaceID string `json:"surface_id"`
 ContractID string `json:"contract_id"`
 PreconditionID string `json:"precondition_id"`
 CaseSHA string `json:"case_sha256"`
 Input struct{ Markdown string `json:"markdown_utf8"`; MetadataValue string `json:"metadata_value"` } `json:"input"`
 Options struct{ Mode string `json:"mode"` } `json:"options"`
 Sequence []string `json:"sequence"`
 Faults []struct{ Kind string `json:"kind"`; Limit int `json:"limit"` } `json:"faults"`
}
type customParser struct{ calls *int }
func (p *customParser) Trigger()[]byte{return []byte{'@'}}
func (p *customParser) Parse(_ ast.Node,_ text.Reader,_ parser.Context)ast.Node{*p.calls++;return nil}
func constructors(mode string,pcalls,rcalls *int)(parser.Parser,html.Renderer){
 po:=[]parser.Option{};ro:=[]html.Option{html.WithUnsafe()}
 if mode!="commonmark"{po=append(po,parser.WithExtensions(extension.NewGFMParser()));ro=append(ro,html.WithExtensions(extension.NewGFMHTMLRenderer()))}
 if mode=="footnotes"{po=append(po,parser.WithExtensions(extension.NewFootnoteParser()));ro=append(ro,html.WithExtensions(extension.NewFootnoteHTMLRenderer()))}
 if mode=="custom"{
  po=append(po,parser.WithInlineParsers(util.Prioritized[parser.InlineParser](&customParser{pcalls},1)))
  ro=append(ro,html.WithNodeRenderer(ast.KindParagraph,html.NodeRendererFunc(func(w io.Writer,_ []byte,_ ast.Node,enter bool,_ renderer.Context)(ast.WalkStatus,error){
   if enter{*rcalls++;_,e:=fmt.Fprintf(w,"<custom n=\"%d\">",*rcalls);return ast.WalkContinue,e}
   _,e:=io.WriteString(w,"</custom>\n");return ast.WalkContinue,e
  })))
 }
 return parser.New(po...),html.New(ro...)
}
var sentinel=errors.New("optimbench public writer fault")
type faultWriter struct{ data bytes.Buffer; limit int; err error }
func(w *faultWriter)Write(p []byte)(int,error){
 left:=w.limit-w.data.Len();if left<=0{return 0,w.err}
 if len(p)>left{n,_:=w.data.Write(p[:left]);return n,w.err};return w.data.Write(p)
}
func(w *faultWriter)WriteString(s string)(int,error){return w.Write([]byte(s))}
func(w *faultWriter)WriteByte(b byte)error{_,e:=w.Write([]byte{b});return e}
func fdCount()int{f,e:=os.ReadDir("/proc/self/fd");if e!=nil{return -1};return len(f)}
func affinity() map[string]any {
 var mask [128]byte
 _,_,err:=syscall.RawSyscall(syscall.SYS_SCHED_GETAFFINITY,0,uintptr(len(mask)),uintptr(unsafe.Pointer(&mask[0])))
 cpus:=[]int{}
 if err!=0{return map[string]any{"available":false,"method":"sched_getaffinity","errno":int(err)}}
 for i,b:=range mask{for j:=0;j<8;j++{if b&(1<<j)!=0{cpus=append(cpus,8*i+j)}}}
 return map[string]any{"available":true,"method":"sched_getaffinity","cpus":cpus,"pid":os.Getpid()}
}
func snapshot(phase string) map[string]any {
 threads:=[]map[string]any{};fds:=[]map[string]any{};children:=[]map[string]any{}
 entries,err:=os.ReadDir("/proc/self/task");available:=err==nil
 for _,entry:=range entries{
  tid,e:=strconv.Atoi(entry.Name());if e!=nil{continue}
  raw,e:=os.ReadFile("/proc/self/task/"+entry.Name()+"/stat");if e!=nil{continue}
  s:=string(raw);end:=strings.LastIndex(s,")");if end<0{available=false;continue}
  fields:=strings.Fields(s[end+1:]);if len(fields)<20{available=false;continue}
  if fields[0]=="Z"||fields[0]=="X"||fields[0]=="x"{continue}
  ticks,e:=strconv.ParseInt(fields[19],10,64);if e!=nil{available=false;continue}
  threads=append(threads,map[string]any{"tid":tid,"start_ticks":ticks,"state":fields[0],"tgid":os.Getpid(),"scope_pid":os.Getpid()})
 }
 entries,err=os.ReadDir("/proc/self/fd");available=available&&err==nil
 for _,entry:=range entries{target,e:=os.Readlink("/proc/self/fd/"+entry.Name());if e!=nil{continue};sum:=sha256.Sum256([]byte(target));n,_:=strconv.Atoi(entry.Name());fds=append(fds,map[string]any{"fd":n,"target_sha256":hex.EncodeToString(sum[:])})}
 // Scan concrete PPid relations: this kernel does not expose task/*/children.
 procEntries,e:=os.ReadDir("/proc");available=available&&e==nil
 for _,entry:=range procEntries{
   value:=entry.Name();pid,e:=strconv.Atoi(value);if e!=nil{continue}
   raw,e:=os.ReadFile("/proc/"+value+"/stat");if e!=nil{continue}
   s:=string(raw);end:=strings.LastIndex(s,")");if end<0{available=false;continue}
   fields:=strings.Fields(s[end+1:]);if len(fields)<20{available=false;continue}
   ppid,e:=strconv.Atoi(fields[1]);if e!=nil{available=false;continue};if ppid!=os.Getpid(){continue}
   if fields[0]=="Z"||fields[0]=="X"||fields[0]=="x"{continue}
   ticks,e:=strconv.ParseInt(fields[19],10,64);if e!=nil{available=false;continue}
   children=append(children,map[string]any{"pid":pid,"start_ticks":ticks,"state":fields[0],"parent_scope_pid":os.Getpid()})
 }
 result:=map[string]any{"available":available,"phase":phase,"clock":"CLOCK_REALTIME UnixNano","sample_unix_ns":time.Now().UnixNano(),"raw_method":"procfs stat field22; proc PPid direct-child scan; fd readlink hashes","scope_pid":os.Getpid(),"fds":len(fds),"fd_identities":fds,"threads":len(threads),"thread_identities":threads,"children":len(children),"child_identities":children,"goroutines":runtime.NumGoroutine(),"gomaxprocs":runtime.GOMAXPROCS(0)}
 if phase=="after-call"{
  var profile bytes.Buffer
  err:=pprof.Lookup("goroutine").WriteTo(&profile,2)
  raw:=profile.Bytes();sum:=sha256.Sum256(raw)
  frames:=[]string{}
  // Retain whole matching goroutine blocks, including all created-by frames.
  // An empty list means no pinned Goldmark module prefix in the actual profile,
  // not a count-based assertion about goroutine ownership.
  for _,block:=range strings.Split(string(raw),"\n\n"){
   if strings.Contains(block,"github.com/yuin/goldmark/v2.")||strings.Contains(block,"github.com/yuin/goldmark/v2/"){frames=append(frames,block)}
  }
  result["goroutine_profile_available"]=err==nil
  result["goroutine_profile_raw"]=string(raw)
  result["goroutine_profile_sha256"]=hex.EncodeToString(sum[:])
  result["goroutine_profile_method"]="runtime/pprof.Lookup(goroutine).WriteTo(debug=2)"
  result["candidate_goroutine_frames"]=frames
 }
 return result
}
func execute(c inputCase)(obs map[string]any){
 exe,_:=os.Executable();binary,_:=os.ReadFile(exe);digest:=sha256.Sum256(binary)
 build:=os.Getenv("OPTIMBENCH_ADMITTED_BUILD_SHA256")
 if build==""{build=hex.EncodeToString(digest[:])}
 before:=snapshot("before-call")
 obs=map[string]any{"format":"optimbench-fuzz-observation-v1",
  "identity":map[string]any{"task":c.Task,"surface_id":c.SurfaceID,"case_sha256":c.CaseSHA,"build_sha256":build},
  "termination":map[string]any{"kind":"return","exit_code":0},
  "outcome":map[string]any{"kind":"ok","value":nil},"effects":map[string]any{},
  "diagnostics":map[string]any{}}
 defer func(){if p:=recover();p!=nil{obs["termination"]=map[string]any{"kind":"panic","exit_code":0};obs["outcome"]=map[string]any{"kind":"error","class":"panic"};obs["diagnostics"]=map[string]any{"panic_text":fmt.Sprint(p)}}}()
 if c.SurfaceID=="ast.NewDocument.Metadata+ast.NewParagraph"{
  copyMetadata:=func(d *ast.Document)map[string]any{result:=map[string]any{};for k,v:=range d.Metadata(){result[k]=v};return result}
  a,b:=ast.NewDocument(),ast.NewDocument()
  pa,pb:=ast.NewParagraph(),ast.NewParagraph()
  steps:=[]map[string]any{
   {"operation":"factory","documents_distinct":a!=b,"paragraphs_distinct":pa!=pb,
    "a_metadata":copyMetadata(a),"b_metadata":copyMetadata(b),"a_children":a.ChildCount(),"b_children":b.ChildCount()}}
  a.AddMeta("value",c.Input.MetadataValue)
  getter:=a.Metadata();getter["via_getter"]="mutated"
  a.AppendChild(pa);b.AppendChild(pb)
  steps=append(steps,map[string]any{"operation":"mutate","a_metadata":copyMetadata(a),"b_metadata":copyMetadata(b),
   "a_children":a.ChildCount(),"b_children":b.ChildCount(),"a_parent_matches":pa.Parent()==a,"b_parent_matches":pb.Parent()==b})
  b.AddMeta("value","interleaved")
  a.RemoveChildren()
  steps=append(steps,map[string]any{"operation":"interleave","a_metadata":copyMetadata(a),"b_metadata":copyMetadata(b),
   "a_children":a.ChildCount(),"b_children":b.ChildCount(),"a_child_detached":pa.Parent()==nil,"b_parent_matches":pb.Parent()==b})
  fresh,paragraph:=ast.NewDocument(),ast.NewParagraph()
  steps=append(steps,map[string]any{"operation":"fresh","metadata":copyMetadata(fresh),"children":fresh.ChildCount(),
   "document_distinct":fresh!=a&&fresh!=b,"paragraph_detached":paragraph.Parent()==nil,"paragraph_distinct":paragraph!=pa&&paragraph!=pb})
  obs["effects"]=map[string]any{"aliasing_steps":steps}
  obs["diagnostics"]=map[string]any{"call_lifecycle":map[string]any{"before":before,"after":snapshot("after-call")},
   "execution_affinity":affinity(),"probe_binary_sha256":hex.EncodeToString(digest[:])}
  return obs
 }
 source:=[]byte(c.Input.Markdown);pcalls,rcalls:=0,0
 var p parser.Parser;var r html.Renderer;var doc ast.Node;var ctx parser.Context
 steps:=[]map[string]any{};lifecycle:=[]map[string]any{}
 for index,op:=range c.Sequence{
  beforeG,beforeFD:=runtime.NumGoroutine(),fdCount()
  switch op{
  case "init":
   p,r=constructors(c.Options.Mode,&pcalls,&rcalls);ctx=parser.NewContext()
  case "parse":
   if c.Options.Mode=="context"{doc=p.Parse(source,parser.WithContext(ctx))}else{doc=p.Parse(source)}
  case "render":
   var b bytes.Buffer;var w io.Writer=&b;var fw *faultWriter
   if len(c.Faults)>0{
    f:=c.Faults[0];e:=sentinel;if f.Kind=="eintr_after"{e=syscall.EINTR}
    fw=&faultWriter{limit:f.Limit,err:e};w=fw
   }
   err:=r.Render(w,source,doc);data:=b.Bytes();if fw!=nil{data=fw.data.Bytes()}
   outcome:=map[string]any{"kind":"ok","value":nil}
   if err!=nil{class:="other_error";if errors.Is(err,sentinel){class="sentinel_writer_error"};if errors.Is(err,syscall.EINTR){class="EINTR"};outcome=map[string]any{"kind":"error","class":class};obs["outcome"]=outcome}
   steps=append(steps,map[string]any{"step":index,"html_utf8":string(data),
    "html_base64":base64.StdEncoding.EncodeToString(data),"output_encoding":"base64",
    "accepted_bytes":len(data),"outcome":outcome})
  case "reuse","finish":
  }
  lifecycle=append(lifecycle,map[string]any{"step":index,"operation":op,"goroutines_before":beforeG,"goroutines_after":runtime.NumGoroutine(),"fds_before":beforeFD,"fds_after":fdCount()})
 }
 obs["effects"]=map[string]any{"render_steps":steps}
 obs["diagnostics"]=map[string]any{"gomaxprocs":runtime.GOMAXPROCS(0),"lifecycle":lifecycle,"call_lifecycle":map[string]any{"before":before,"after":snapshot("after-call")},"execution_affinity":affinity(),"probe_binary_sha256":hex.EncodeToString(digest[:]),"custom_parser_calls":pcalls,"custom_renderer_calls":rcalls,"write_chunking":"unconstrained; not compared"}
 return obs
}
func main(){
 d:=json.NewDecoder(os.Stdin)
 for {
 var c inputCase
 if err:=d.Decode(&c);err==io.EOF{return}else if err!=nil{fmt.Fprintln(os.Stderr,"invalid transport:",err);os.Exit(2)}
 alias:=c.SurfaceID=="ast.NewDocument.Metadata+ast.NewParagraph"
 if c.Task!="goldmark-gfm-html"||(!alias&&c.SurfaceID!="parser.Parse+html.Render")||len(c.CaseSHA)!=64||len(c.Input.Markdown)>2*1024*1024{fmt.Fprintln(os.Stderr,"invalid case identity/size");os.Exit(2)}
 modes:=map[string]bool{"gfm":true,"commonmark":true,"footnotes":true,"custom":true,"context":true}
 if alias{if c.Options.Mode!="ast_metadata"||len(c.Input.MetadataValue)==0||len(c.Input.MetadataValue)>1024||len(c.Faults)!=0{fmt.Fprintln(os.Stderr,"invalid aliasing case");os.Exit(2)}}else if !modes[c.Options.Mode]{fmt.Fprintln(os.Stderr,"unsupported public mode");os.Exit(2)}
 expected:=[]string{"init","parse","render","reuse","parse","render","finish"}
 if alias{expected=[]string{"factory","getter","mutate","interleave","fresh","finish"}}
 if len(c.Sequence)!=len(expected){fmt.Fprintln(os.Stderr,"unsupported sequence");os.Exit(2)}
 for i,s:=range expected{if c.Sequence[i]!=s{fmt.Fprintln(os.Stderr,"unsupported sequence");os.Exit(2)}}
 if len(c.Faults)>1{fmt.Fprintln(os.Stderr,"unsupported fault schedule");os.Exit(2)}
 for _,f:=range c.Faults{if(f.Kind!="fail_after"&&f.Kind!="eintr_after")||f.Limit<0||f.Limit>2*1024*1024{fmt.Fprintln(os.Stderr,"invalid writer fault");os.Exit(2)}}
 if err:=json.NewEncoder(os.Stdout).Encode(execute(c));err!=nil{fmt.Fprintln(os.Stderr,"report serialization:",err);os.Exit(2)}
 }
}

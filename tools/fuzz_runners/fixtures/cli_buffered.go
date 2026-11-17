// Verifier-owned calibration program, not a task or optimizer patch.
// Default "correct" checks errors from both bufio.Write and bufio.Flush.
package main

import (
	"bufio"
	"fmt"
	"os"
)

var behavior = "correct"

func main() {
	if len(os.Args) != 2 { os.Exit(2) }
	path := os.Args[1]
	data, err := os.ReadFile(path)
	if err != nil { fmt.Fprintln(os.Stderr, err); os.Exit(2) }
	writer := bufio.NewWriterSize(os.Stdout, 4096)
	_, err = writer.Write(data)
	if err == nil {
		flushed := writer.Flush()
		if behavior != "ignore_flush" { err = flushed }
	}
	if err != nil {
		if behavior == "rewrite_on_error" {
			if rewrite := os.WriteFile(path, []byte("rewritten after output error\n"), 0644); rewrite != nil {
				fmt.Fprintln(os.Stderr, rewrite)
			}
		}
		fmt.Fprintln(os.Stderr, err)
		if behavior == "success_on_error" { os.Exit(0) }
		os.Exit(1)
	}
}

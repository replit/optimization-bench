// Protected in-process batch caller. Complete results leave on stdout.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/dimitri/sqlfmt/format"
)

type input struct {
	ID  string `json:"id"`
	SQL []byte `json:"sql"`
}

type result struct {
	ID     string `json:"id"`
	Output []byte `json:"output_base64"`
	Error  []byte `json:"error_base64"`
}

func main() {
	if len(os.Args) != 1 {
		fmt.Fprintln(os.Stderr, "native batch accepts only its stdin request")
		os.Exit(2)
	}
	var inputs []input
	decoder := json.NewDecoder(os.Stdin)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&inputs); err != nil || len(inputs) == 0 {
		fmt.Fprintln(os.Stderr, "invalid native batch")
		os.Exit(2)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		fmt.Fprintln(os.Stderr, "trailing native request data")
		os.Exit(2)
	}
	seen := make(map[string]bool)
	outputs := make([]result, 0, len(inputs))
	for _, in := range inputs {
		if in.ID == "" || seen[in.ID] {
			fmt.Fprintln(os.Stderr, "invalid native input identity")
			os.Exit(2)
		}
		seen[in.ID] = true
		output, err := format.Format(bytes.NewReader(in.SQL))
		item := result{ID: in.ID, Output: []byte(output)}
		if err != nil {
			item.Error = []byte(err.Error())
		}
		outputs = append(outputs, item)
	}
	if err := json.NewEncoder(os.Stdout).Encode(outputs); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
}

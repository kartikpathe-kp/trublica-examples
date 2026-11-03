// Any language works: ship a Dockerfile, listen on $PORT, answer GET /healthz. Trublica builds and runs it as-is.
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"time"
)

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte(`{"status":"ok"}`)) })
	mux.HandleFunc("/runs", func(w http.ResponseWriter, r *http.Request) {
		var in map[string]any
		_ = json.NewDecoder(r.Body).Decode(&in)
		w.Header().Set("content-type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"status": "succeeded", "output": map[string]any{"reply": "hello from Go", "input": in, "at": time.Now().UTC()}})
	})
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	log.Fatal(http.ListenAndServe(":"+port, mux))
}

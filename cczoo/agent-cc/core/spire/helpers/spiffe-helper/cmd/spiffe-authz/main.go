package main

import (
	"context"
	"flag"
	"github.com/spiffe/go-spiffe/v2/spiffeid"
	"github.com/spiffe/spiffe-helper/pkg/authz"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

func main() {
	socket := flag.String("socket", "", "required protected local UDS")
	client := flag.String("client-id", "", "required exact allowed client SPIFFE ID")
	flag.Parse()
	if !filepath.IsAbs(*socket) || filepath.Clean(*socket) != *socket {
		log.Fatal("socket must be a clean absolute path")
	}
	id, err := spiffeid.FromString(*client)
	if err != nil {
		log.Fatal(err)
	}
	if st, err := os.Lstat(*socket); err == nil {
		if st.Mode()&os.ModeSocket == 0 {
			log.Fatal("socket path is not a socket")
		}
		if err = os.Remove(*socket); err != nil {
			log.Fatal(err)
		}
	} else if !os.IsNotExist(err) {
		log.Fatal(err)
	}
	listener, err := net.Listen("unix", *socket)
	if err != nil {
		log.Fatal(err)
	}
	defer listener.Close()
	if err = os.Chmod(*socket, 0660); err != nil {
		log.Fatal(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	server := &http.Server{Handler: authz.Handler(id), ReadHeaderTimeout: 2 * time.Second, ReadTimeout: 2 * time.Second, WriteTimeout: 2 * time.Second, IdleTimeout: 5 * time.Second, MaxHeaderBytes: 64 << 10}
	go func() { <-ctx.Done(); _ = server.Close() }()
	if err = server.Serve(listener); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}

package main

import (
	"log"
	"net"

	"github.com/gin-gonic/gin"
	creditv1 "github.com/bank/credit-scoring/gen/bank/credit/v1"
	"google.golang.org/grpc"
)

func main() {
	lis, err := net.Listen("tcp", ":9090")
	if err != nil {
		log.Fatal(err)
	}
	s := grpc.NewServer()
	creditv1.RegisterCreditScoringServer(s, &scoringServer{})
	go s.Serve(lis)

	r := gin.Default()
	ops := r.Group("/ops")
	ops.GET("/health", health)
	r.Run(":8080")
}

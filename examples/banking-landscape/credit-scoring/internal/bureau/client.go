package bureau

import "net/http"

const bureauURL = "https://api.credit-bureau.example.com/v1/scores"

func Fetch(document string) (*http.Response, error) {
	return http.Get(bureauURL + "/" + document)
}

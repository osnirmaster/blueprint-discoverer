package com.bank.loans.client

import org.springframework.cloud.openfeign.FeignClient
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable

@FeignClient(name = "customers", url = "\${clients.customer.url}")
interface CustomerClient {
    @GetMapping("/customers/{customerId}")
    fun getCustomer(@PathVariable("customerId") customerId: String): CustomerDto
}

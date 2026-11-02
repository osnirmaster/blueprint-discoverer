package com.bank.accounts.client;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.*;

@FeignClient(name = "customer-service")
public interface CustomerClient {
    @GetMapping("/customers/{customerId}")
    CustomerDto getCustomer(@PathVariable("customerId") String customerId);
}

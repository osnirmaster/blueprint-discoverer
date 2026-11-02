package com.bank.accounts.api;

import org.springframework.web.bind.annotation.*;
import com.bank.accounts.domain.*;

@RestController
@RequestMapping("/accounts")
public class CurrentAccountController {
    private final CurrentAccountService service;

    public CurrentAccountController(CurrentAccountService service) { this.service = service; }

    @PostMapping
    public CurrentAccount openAccount(@RequestBody OpenAccountRequest request) { return service.open(request); }

    @GetMapping("/{accountId}")
    public CurrentAccount getAccount(@PathVariable String accountId) { return service.find(accountId); }

    @GetMapping("/{accountId}/balance")
    public Balance getBalance(@PathVariable String accountId) { return service.balance(accountId); }

    @RequestMapping(value = "/{accountId}/block", method = RequestMethod.POST)
    public void blockAccount(@PathVariable String accountId) { service.block(accountId); }
}

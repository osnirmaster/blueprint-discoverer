package com.bank.accounts.domain;

import org.springframework.stereotype.Service;
import com.bank.accounts.client.CustomerClient;
import com.bank.accounts.messaging.AccountEventsPublisher;

@Service
public class CurrentAccountService {
    private final CustomerClient customers;
    private final AccountEventsPublisher events;

    public CurrentAccountService(CustomerClient customers, AccountEventsPublisher events) {
        this.customers = customers; this.events = events;
    }

    public CurrentAccount open(OpenAccountRequest request) {
        customers.getCustomer(request.customerId());
        CurrentAccount account = new CurrentAccount();
        events.accountOpened(account);
        return account;
    }
    public CurrentAccount find(String id) { return null; }
    public Balance balance(String id) { return null; }
    public void block(String id) { events.accountBlocked(id); }
}

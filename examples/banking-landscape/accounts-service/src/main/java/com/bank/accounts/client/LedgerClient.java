package com.bank.accounts.client;

import org.springframework.ws.client.core.WebServiceTemplate;

public class LedgerClient {
    private static final String CORE_BANKING = "http://core-banking-legacy:8080/ws/core";
    private final WebServiceTemplate ws;

    public LedgerClient(WebServiceTemplate ws) { this.ws = ws; }

    public Object ledgerBalance(Object request) {
        return ws.marshalSendAndReceive(CORE_BANKING, request);
    }
}

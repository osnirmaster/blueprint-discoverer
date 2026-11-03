package com.bank.core;

import javax.jws.WebMethod;
import javax.jws.WebService;

@WebService(serviceName = "CoreBankingService")
public class CoreBankingServiceImpl {

    @WebMethod(operationName = "GetLedgerBalance")
    public LedgerBalance getLedgerBalance(String accountNumber) {
        return ledger.balance(accountNumber);
    }

    @WebMethod(operationName = "PostLedgerEntry")
    public LedgerEntry postLedgerEntry(LedgerEntry entry) {
        return ledger.post(entry);
    }
}

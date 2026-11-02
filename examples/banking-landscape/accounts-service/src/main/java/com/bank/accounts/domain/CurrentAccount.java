package com.bank.accounts.domain;

import jakarta.persistence.*;

@Entity
@Table(name = "current_account")
public class CurrentAccount {
    @Id private String accountId;
    private String customerId;
    private java.math.BigDecimal balance;
    private String status;
}

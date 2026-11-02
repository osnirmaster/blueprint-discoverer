package com.bank.accounts.messaging;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class AccountEventsPublisher {
    private final KafkaTemplate<String, Object> kafkaTemplate;

    public AccountEventsPublisher(KafkaTemplate<String, Object> kafkaTemplate) { this.kafkaTemplate = kafkaTemplate; }

    public void accountOpened(Object account) { kafkaTemplate.send("account-events", account); }
    public void accountBlocked(String id) { kafkaTemplate.send("account-events", id); }
}

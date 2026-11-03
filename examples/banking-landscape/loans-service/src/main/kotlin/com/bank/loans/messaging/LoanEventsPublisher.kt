package com.bank.loans.messaging

import software.amazon.awssdk.services.sns.SnsClient
import software.amazon.awssdk.services.sns.model.PublishRequest

const val LOAN_EVENTS_TOPIC = "arn:aws:sns:sa-east-1:123456789012:loan-events"

class LoanEventsPublisher(private val sns: SnsClient) {
    fun disbursed(loanId: String) {
        sns.publish(PublishRequest.builder().topicArn(LOAN_EVENTS_TOPIC).message("""{"loanId":"$loanId"}""").build())
    }
}

package com.bank.loans.scoring

import bank.credit.v1.CreditScoringGrpcKt
import net.devh.boot.grpc.client.inject.GrpcClient
import org.springframework.stereotype.Service

@Service
class CreditCheck {
    @GrpcClient("credit-scoring")
    private lateinit var scoring: CreditScoringGrpcKt.CreditScoringCoroutineStub

    suspend fun approve(customerId: String): Boolean = scoring.scoreCustomer(request(customerId)).approved
}

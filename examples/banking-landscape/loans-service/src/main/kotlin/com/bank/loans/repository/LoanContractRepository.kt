package com.bank.loans.repository

import software.amazon.awssdk.services.dynamodb.DynamoDbClient
import software.amazon.awssdk.services.dynamodb.model.GetItemRequest
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest

const val LOANS_TABLE = "loan-contracts"

class LoanContractRepository(private val ddb: DynamoDbClient) {
    fun save(contract: Map<String, Any>) {
        ddb.putItem(PutItemRequest.builder().tableName(LOANS_TABLE).item(toItem(contract)).build())
    }

    fun find(loanId: String) = ddb.getItem(GetItemRequest.builder().tableName(LOANS_TABLE).key(key(loanId)).build())
}

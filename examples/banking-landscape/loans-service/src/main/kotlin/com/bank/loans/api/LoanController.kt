package com.bank.loans.api

import com.bank.loans.repository.LoanContractRepository
import org.springframework.web.bind.annotation.*

@RestController
@RequestMapping("/loans")
class LoanController(private val contracts: LoanContractRepository) {

    @GetMapping("/{loanId}")
    fun getLoan(@PathVariable loanId: String): LoanContract = contracts.find(loanId)

    @PostMapping
    fun applyForLoan(@RequestBody application: LoanApplication): LoanContract = contracts.create(application)

    @PostMapping("/{loanId}/installments/{number}/repayment")
    fun repayInstallment(@PathVariable loanId: String, @PathVariable number: Int): Installment =
        contracts.repay(loanId, number)
}

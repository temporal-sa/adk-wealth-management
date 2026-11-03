package io.temporal.demo.wealthmanagement.model;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class OpenInvestmentAccountInput {
    @JsonProperty("client_id")
    private String clientId;
    @JsonProperty("account_name")
    private String accountName;
    @JsonProperty("initial_amount")
    private double initialAmount;
    // Set when a promotion is running for the new account (e.g. "WELCOME-BONUS")
    @JsonProperty("promo_code")
    private String promoCode;
}

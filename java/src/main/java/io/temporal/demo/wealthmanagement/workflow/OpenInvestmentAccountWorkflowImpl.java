package io.temporal.demo.wealthmanagement.workflow;

import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.demo.wealthmanagement.common.ServerInfo;
import io.temporal.demo.wealthmanagement.model.InvestmentAccount;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountInput;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountOutput;
import io.temporal.demo.wealthmanagement.model.UpdateAccountOpeningStateInput;
import io.temporal.demo.wealthmanagement.model.WealthManagementClient;
import io.temporal.failure.ActivityFailure;
import io.temporal.failure.ApplicationFailure;
import io.temporal.workflow.ActivityStub;
import io.temporal.workflow.Saga;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;

import java.time.Duration;
import java.util.Map;
import java.util.Optional;

public class OpenInvestmentAccountWorkflowImpl implements OpenInvestmentAccountWorkflow {
    private static final Logger log = Workflow.getLogger(OpenInvestmentAccountWorkflowImpl.class);

    // Welcome-bonus promotion: a percentage of the initial deposit, up to MAX_BONUS.
    // *** Bug-In-Workflow-Code ***
    // Intentional bug for the "Bug in workflow code" demo: the promo code is "WELCOME-BONUS" (hyphen),
    // but BONUS_RATES is keyed by "WELCOME_BONUS" (underscore), so the lookup in welcomeBonus returns
    // null and unboxing it throws a NullPointerException.
    // To fix it, uncomment the first line below and comment out the second. See README "Failure Scenarios".
    // private static final Map<String, Double> BONUS_RATES = Map.of("WELCOME-BONUS", 0.01);
    private static final Map<String, Double> BONUS_RATES = Map.of("WELCOME_BONUS", 0.01);
    private static final double MAX_BONUS = 500.0;

    // Matches TOOL_RETRY_POLICY in retry_policies.py: retry until the dependency recovers,
    // with a short maximum interval so recovery shows up within seconds.
    private static final RetryOptions RETRY_OPTIONS = RetryOptions.newBuilder()
            .setInitialInterval(Duration.ofSeconds(1))
            .setBackoffCoefficient(2)
            .setMaximumInterval(Duration.ofSeconds(5))
            .build();

    // The activities are implemented in Python, so they are called by name on the Python task queue.
    // The start-to-close timeout bounds a single attempt only, so an outage is retried for as long as it lasts.
    private final ActivityStub activities = Workflow.newUntypedActivityStub(
            ActivityOptions.newBuilder()
                    .setTaskQueue(ServerInfo.getTaskqueue())
                    .setStartToCloseTimeout(Duration.ofSeconds(30))
                    .setRetryOptions(RETRY_OPTIONS)
                    .build());

    private WealthManagementClient client;
    private OpenInvestmentAccountInput inputs;
    private boolean initialized = false;
    private boolean kycVerified = false;
    private boolean complianceReviewed = false;
    private String currentState = "Initializing";

    @Override
    public OpenInvestmentAccountOutput run(OpenInvestmentAccountInput inputs) {
        log.info("started workflow {}", inputs);
        this.inputs = inputs;

        this.client = activities.execute("get_client", WealthManagementClient.class, inputs.getClientId());
        this.initialized = true;
        setState("Waiting KYC");

        Workflow.await(() -> this.kycVerified);
        setState("Waiting Compliance Review");

        Workflow.await(() -> this.complianceReviewed);
        setState("Compliance review has been approved. Creating Investment Account");
        return openAccount();
    }

    /**
     * Saga: create, fund, and register the account.
     *
     * Each completed step registers how to undo it. If a later step fails with a non-retryable
     * error, the compensations run in reverse order. Transient failures never get here:
     * activities retry until they succeed.
     */
    private OpenInvestmentAccountOutput openAccount() {
        String clientId = inputs.getClientId();
        double amount = inputs.getInitialAmount();
        double bonus = inputs.getPromoCode() != null ? welcomeBonus(inputs) : 0.0;
        // Idempotency key: stable across activity retries and worker restarts. It includes the
        // run ID because the workflow ID is reused when the customer opens an account with the
        // same name again (e.g. after a rollback).
        String transferId = Workflow.getInfo().getWorkflowId() + "-" + Workflow.getInfo().getRunId() + "-initial-deposit";

        Saga saga = new Saga(new Saga.Options.Builder().setParallelCompensation(false).build());
        try {
            setState("Creating the account record.");
            Map<?, ?> account = activities.execute("open_investment", Map.class,
                    new InvestmentAccount(clientId, inputs.getAccountName(), amount + bonus));
            String investmentId = (String) account.get("investment_id");
            saga.addCompensation(() -> compensate("removing the account record",
                    "remove_investment", clientId, investmentId));

            setState(String.format("Transferring $%,.2f from the linked bank account.", amount));
            activities.execute("debit_linked_account", Map.class, clientId, amount, transferId);
            saga.addCompensation(() -> compensate(
                    String.format("refunding $%,.2f to the linked bank account", amount),
                    "credit_linked_account", clientId, amount, transferId + "-refund"));

            setState("Registering the account with the custodian.");
            activities.execute("register_account", String.class, clientId, investmentId, amount);
        } catch (ActivityFailure e) {
            String reason = reason(e);
            setState("Failed: " + reason + ". Rolling back.");
            saga.compensate();
            setState("Rolled back. The account was not opened: " + reason + ".");
            return new OpenInvestmentAccountOutput(false, "The account was not opened: " + reason);
        }

        setState("Complete");
        return new OpenInvestmentAccountOutput(true, "investment account created");
    }

    private static double welcomeBonus(OpenInvestmentAccountInput inputs) {
        double rate = BONUS_RATES.get(inputs.getPromoCode());
        return Math.min(inputs.getInitialAmount() * rate, MAX_BONUS);
    }

    private void compensate(String description, String activityName, Object... args) {
        setState("Rolling back: " + description + ".");
        // Compensations are activities too, so they are retried until they succeed
        activities.execute(activityName, Object.class, args);
    }

    // The business reason for a non-retryable activity failure, such as the custodian rejecting the account
    private static String reason(ActivityFailure e) {
        Throwable cause = e.getCause();
        if (cause instanceof ApplicationFailure) {
            return ((ApplicationFailure) cause).getOriginalMessage();
        }
        return cause != null ? cause.getMessage() : e.getMessage();
    }

    @Override
    public String getCurrentState() {
        return this.currentState;
    }

    @Override
    public WealthManagementClient getClientDetails() {
        Workflow.await(() -> this.initialized);
        return this.client;
    }

    @Override
    public String updateClientDetails(Map<String, Object> client) {
        String result = activities.execute("update_client", String.class, inputs.getClientId(), client);
        this.kycVerified = true;
        return result;
    }

    @Override
    public void verifyKyc() {
        Workflow.await(() -> this.initialized);
        this.kycVerified = true;
    }

    @Override
    public void complianceApproved() {
        Workflow.await(() -> this.initialized && this.kycVerified);
        this.complianceReviewed = true;
    }

    private void setState(String state) {
        this.currentState = state;
        updateParentState(state);
    }

    // Signals the supervisor workflow directly. Signals are routed by workflow ID, not task queue,
    // so this works even though the parent runs in Python on a different task queue.
    private void updateParentState(String state) {
        Optional<String> parentWorkflowId = Workflow.getInfo().getParentWorkflowId();
        if (parentWorkflowId.isEmpty()) {
            return;
        }
        Workflow.newUntypedExternalWorkflowStub(parentWorkflowId.get())
                .signal("update_account_opening_state",
                        new UpdateAccountOpeningStateInput(inputs.getAccountName(), state,
                                Workflow.getInfo().getWorkflowId()));
    }
}

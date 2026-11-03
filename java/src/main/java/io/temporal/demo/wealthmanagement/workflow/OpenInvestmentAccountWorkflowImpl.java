package io.temporal.demo.wealthmanagement.workflow;

import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.demo.wealthmanagement.common.ServerInfo;
import io.temporal.demo.wealthmanagement.model.InvestmentAccount;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountInput;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountOutput;
import io.temporal.demo.wealthmanagement.model.UpdateAccountOpeningStateInput;
import io.temporal.demo.wealthmanagement.model.WealthManagementClient;
import io.temporal.workflow.ActivityStub;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;

import java.time.Duration;
import java.util.Map;
import java.util.Optional;

public class OpenInvestmentAccountWorkflowImpl implements OpenInvestmentAccountWorkflow {
    private static final Logger log = Workflow.getLogger(OpenInvestmentAccountWorkflowImpl.class);

    // Matches ClientActivities.retry_policy in the Python version
    private static final RetryOptions RETRY_OPTIONS = RetryOptions.newBuilder()
            .setInitialInterval(Duration.ofSeconds(1))
            .setBackoffCoefficient(2)
            .setMaximumInterval(Duration.ofSeconds(30))
            .build();

    // The activities are implemented in Python, so they are called by name on the Python task queue.
    private final ActivityStub activities = Workflow.newUntypedActivityStub(
            ActivityOptions.newBuilder()
                    .setTaskQueue(ServerInfo.getTaskqueue())
                    .setScheduleToCloseTimeout(Duration.ofSeconds(5))
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

        InvestmentAccount newAccount = new InvestmentAccount(
                inputs.getClientId(), inputs.getAccountName(), inputs.getInitialAmount());
        Map<?, ?> investmentAccount = activities.execute("open_investment", Map.class, newAccount);
        setState("Complete");

        OpenInvestmentAccountOutput result = new OpenInvestmentAccountOutput();
        result.setAccountCreated(investmentAccount != null);
        result.setMessage(investmentAccount != null
                ? "investment account created"
                : "An unexpected error occurred creating investment account");
        return result;
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
                        new UpdateAccountOpeningStateInput(inputs.getAccountName(), state));
    }
}

package io.temporal.demo.wealthmanagement.workflow;

import io.temporal.activity.Activity;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;
import io.temporal.api.enums.v1.EventType;
import io.temporal.api.enums.v1.WorkflowExecutionStatus;
import io.temporal.api.history.v1.HistoryEvent;
import io.temporal.api.workflowservice.v1.DescribeWorkflowExecutionRequest;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import io.temporal.client.WorkflowStub;
import io.temporal.demo.wealthmanagement.common.ServerInfo;
import io.temporal.demo.wealthmanagement.model.InvestmentAccount;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountInput;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountOutput;
import io.temporal.demo.wealthmanagement.model.UpdateAccountOpeningStateInput;
import io.temporal.demo.wealthmanagement.model.WealthManagementClient;
import io.temporal.failure.ApplicationFailure;
import io.temporal.testing.TestWorkflowEnvironment;
import io.temporal.worker.Worker;
import io.temporal.workflow.ChildWorkflowOptions;
import io.temporal.workflow.QueryMethod;
import io.temporal.workflow.SignalMethod;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Runs the workflow against fake versions of the Python activities, registered under the real
 * activity names on the Python task queue. They record the order they were called in and fail
 * on demand, so the saga can be tested without the Python worker, Redis or data files.
 */
class OpenInvestmentAccountWorkflowTest {
    private static final String OPEN_ACCOUNT_QUEUE = "OpenAccount-test";
    private static final String CLIENT_ID = "123";
    private static final String ACCOUNT_NAME = "College Fund";
    private static final String INVESTMENT_ID = "i-test";
    private static final String CHILD_ID = "OpenAccount-parent-" + CLIENT_ID + "-" + ACCOUNT_NAME;

    private TestWorkflowEnvironment testEnv;
    private WorkflowClient client;
    private FakePythonActivities pythonActivities;

    @BeforeEach
    void setUp() {
        testEnv = TestWorkflowEnvironment.newInstance();
        client = testEnv.getWorkflowClient();

        Worker openAccountWorker = testEnv.newWorker(OPEN_ACCOUNT_QUEUE);
        openAccountWorker.registerWorkflowImplementationTypes(
                OpenInvestmentAccountWorkflowImpl.class, TestParentWorkflowImpl.class);

        // Stands in for the Python worker, which hosts the activities
        pythonActivities = new FakePythonActivities();
        Worker pythonWorker = testEnv.newWorker(ServerInfo.getTaskqueue());
        pythonWorker.registerActivitiesImplementations(pythonActivities);

        testEnv.start();
    }

    @AfterEach
    void tearDown() {
        testEnv.close();
    }

    @Test
    void opensAccountAfterKycAndComplianceApproval() {
        TestParentWorkflow parent = startParent();
        OpenInvestmentAccountWorkflow child = waitForChild();

        WealthManagementClient details = child.getClientDetails();
        assertEquals("Jane", details.getFirstName());
        assertEquals("Waiting KYC", child.getCurrentState());

        child.verifyKyc();
        child.complianceApproved();

        OpenInvestmentAccountOutput result = WorkflowStub.fromTyped(child).getResult(OpenInvestmentAccountOutput.class);
        assertTrue(result.isAccountCreated());
        assertEquals("investment account created", result.getMessage());

        assertEquals(CLIENT_ID, pythonActivities.openedAccount.getClientId());
        assertEquals(ACCOUNT_NAME, pythonActivities.openedAccount.getName());
        assertEquals(1000.0, pythonActivities.openedAccount.getBalance());
        assertEquals(List.of("get_client", "open_investment#1", "debit:1000.0", "register"), pythonActivities.calls);

        assertEquals(List.of(
                ACCOUNT_NAME + ": Waiting KYC",
                ACCOUNT_NAME + ": Waiting Compliance Review",
                ACCOUNT_NAME + ": Compliance review has been approved. Creating Investment Account",
                ACCOUNT_NAME + ": Creating the account record.",
                ACCOUNT_NAME + ": Transferring $1,000.00 from the linked bank account.",
                ACCOUNT_NAME + ": Registering the account with the custodian.",
                ACCOUNT_NAME + ": Complete"), parent.getStates());
    }

    @Test
    void updatingClientDetailsCompletesKyc() {
        startParent();
        OpenInvestmentAccountWorkflow child = waitForChild();
        child.getClientDetails();

        String updateResult = child.updateClientDetails(Map.of("phone", "555-0100"));
        assertEquals("updated", updateResult);
        assertEquals(Map.of("phone", "555-0100"), pythonActivities.updatedFields);
        assertEquals("Waiting Compliance Review", child.getCurrentState());

        child.complianceApproved();
        OpenInvestmentAccountOutput result = WorkflowStub.fromTyped(child).getResult(OpenInvestmentAccountOutput.class);
        assertTrue(result.isAccountCreated());
    }

    @Test
    void sagaRollsBackInReverseWhenCustodianRejects() {
        pythonActivities.failures.put("register_account", "reject");
        OpenInvestmentAccountWorkflow workflow = startAndApprove(null);

        OpenInvestmentAccountOutput result = WorkflowStub.fromTyped(workflow).getResult(OpenInvestmentAccountOutput.class);
        assertFalse(result.isAccountCreated());
        assertTrue(result.getMessage().contains("register_account rejected"), result.getMessage());
        assertTrue(workflow.getCurrentState().startsWith("Rolled back"), workflow.getCurrentState());
        assertEquals(List.of("get_client", "open_investment#1", "debit:1000.0", "register",
                "credit:1000.0", "remove_investment:" + INVESTMENT_ID), pythonActivities.calls);
    }

    @Test
    void sagaUndoesOnlyCompletedSteps() {
        pythonActivities.failures.put("debit_linked_account", "reject");
        OpenInvestmentAccountWorkflow workflow = startAndApprove(null);

        OpenInvestmentAccountOutput result = WorkflowStub.fromTyped(workflow).getResult(OpenInvestmentAccountOutput.class);
        assertFalse(result.isAccountCreated());
        // The debit never happened, so there is nothing to refund
        assertEquals(List.of("get_client", "open_investment#1", "debit:1000.0",
                "remove_investment:" + INVESTMENT_ID), pythonActivities.calls);
    }

    @Test
    void transientFailuresAreRetriedNotRolledBack() {
        pythonActivities.failures.put("open_investment", 2);
        OpenInvestmentAccountWorkflow workflow = startAndApprove(null);

        OpenInvestmentAccountOutput result = WorkflowStub.fromTyped(workflow).getResult(OpenInvestmentAccountOutput.class);
        assertTrue(result.isAccountCreated());
        assertEquals(List.of("get_client", "open_investment#1", "open_investment#2", "open_investment#3",
                "debit:1000.0", "register"), pythonActivities.calls);
    }

    @Test
    void promoCodeBugPausesTheWorkflowInsteadOfFailingIt() throws InterruptedException {
        OpenInvestmentAccountWorkflow workflow = startAndApprove("WELCOME-BONUS");
        String workflowId = WorkflowStub.fromTyped(workflow).getExecution().getWorkflowId();

        String failure = null;
        for (int i = 0; i < 40 && failure == null; i++) {
            for (HistoryEvent event : client.fetchHistory(workflowId).getEvents()) {
                if (event.getEventType() == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED) {
                    failure = event.getWorkflowTaskFailedEventAttributes().getFailure().getMessage();
                }
            }
            Thread.sleep(250);
        }

        assertNotNull(failure, "the demo bug should fail the workflow task");
        // Paused, not failed: still running, and the saga never started
        WorkflowExecutionStatus status = testEnv.getWorkflowServiceStubs().blockingStub()
                .describeWorkflowExecution(DescribeWorkflowExecutionRequest.newBuilder()
                        .setNamespace(testEnv.getNamespace())
                        .setExecution(WorkflowStub.fromTyped(workflow).getExecution())
                        .build())
                .getWorkflowExecutionInfo().getStatus();
        assertEquals(WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING, status);
        assertFalse(pythonActivities.calls.contains("open_investment#1"));
        WorkflowStub.fromTyped(workflow).terminate("test finished");
    }

    // Starts the workflow on its own (no parent) and approves KYC and compliance
    private OpenInvestmentAccountWorkflow startAndApprove(String promoCode) {
        OpenInvestmentAccountWorkflow workflow = client.newWorkflowStub(OpenInvestmentAccountWorkflow.class,
                WorkflowOptions.newBuilder()
                        .setWorkflowId("open-account-test-" + UUID.randomUUID())
                        .setTaskQueue(OPEN_ACCOUNT_QUEUE)
                        .build());
        WorkflowClient.start(workflow::run, new OpenInvestmentAccountInput(CLIENT_ID, ACCOUNT_NAME, 1000.0, promoCode));
        workflow.verifyKyc();
        workflow.complianceApproved();
        return workflow;
    }

    private TestParentWorkflow startParent() {
        TestParentWorkflow parent = client.newWorkflowStub(TestParentWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId("parent").setTaskQueue(OPEN_ACCOUNT_QUEUE).build());
        WorkflowClient.start(parent::run);
        return parent;
    }

    // The parent starts the child asynchronously, so poll until the child answers a query
    private OpenInvestmentAccountWorkflow waitForChild() {
        OpenInvestmentAccountWorkflow child = client.newWorkflowStub(OpenInvestmentAccountWorkflow.class, CHILD_ID);
        long deadline = System.currentTimeMillis() + 10_000;
        while (true) {
            try {
                child.getCurrentState();
                return child;
            } catch (RuntimeException e) {
                if (System.currentTimeMillis() > deadline) {
                    throw e;
                }
                try {
                    Thread.sleep(50);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    throw e;
                }
            }
        }
    }

    // Plays the part of the Python supervisor: starts the child and records its state signals
    @WorkflowInterface
    public interface TestParentWorkflow {
        @WorkflowMethod
        void run();

        @SignalMethod(name = "update_account_opening_state")
        void updateAccountOpeningState(UpdateAccountOpeningStateInput input);

        @QueryMethod
        List<String> getStates();
    }

    public static class TestParentWorkflowImpl implements TestParentWorkflow {
        private final List<String> states = new ArrayList<>();

        @Override
        public void run() {
            OpenInvestmentAccountWorkflow child = Workflow.newChildWorkflowStub(OpenInvestmentAccountWorkflow.class,
                    ChildWorkflowOptions.newBuilder().setWorkflowId(CHILD_ID).build());
            child.run(new OpenInvestmentAccountInput(CLIENT_ID, ACCOUNT_NAME, 1000.0, null));
            // let the final state signal arrive before completing
            Workflow.await(Duration.ofSeconds(5), () -> states.contains(ACCOUNT_NAME + ": Complete"));
        }

        @Override
        public void updateAccountOpeningState(UpdateAccountOpeningStateInput input) {
            if (!CHILD_ID.equals(input.getWorkflowId())) {
                states.add("unexpected workflow_id " + input.getWorkflowId());
            }
            states.add(input.getAccountName() + ": " + input.getState());
        }

        @Override
        public List<String> getStates() {
            return states;
        }
    }

    // Same activity names and signatures as the Python activities
    @ActivityInterface
    public interface PythonActivities {
        @ActivityMethod(name = "get_client")
        WealthManagementClient getClient(String clientId);

        @ActivityMethod(name = "update_client")
        String updateClient(String clientId, Map<String, Object> fields);

        @ActivityMethod(name = "open_investment")
        Map<String, Object> openInvestment(InvestmentAccount account);

        @ActivityMethod(name = "remove_investment")
        boolean removeInvestment(String clientId, String investmentId);

        @ActivityMethod(name = "debit_linked_account")
        Map<String, Object> debitLinkedAccount(String clientId, double amount, String transferId);

        @ActivityMethod(name = "credit_linked_account")
        Map<String, Object> creditLinkedAccount(String clientId, double amount, String transferId);

        @ActivityMethod(name = "register_account")
        String registerAccount(String clientId, String investmentId, double amount);
    }

    static class FakePythonActivities implements PythonActivities {
        // Activity calls, in order
        final List<String> calls = Collections.synchronizedList(new ArrayList<>());
        // Keyed by activity name: "reject" fails non-retryably; an Integer N fails the first N attempts
        final Map<String, Object> failures = new ConcurrentHashMap<>();
        volatile InvestmentAccount openedAccount;
        volatile Map<String, Object> updatedFields;

        private void maybeFail(String name) {
            Object behavior = failures.get(name);
            if ("reject".equals(behavior)) {
                throw ApplicationFailure.newNonRetryableFailure(name + " rejected", "Rejected");
            }
            if (behavior instanceof Integer n && Activity.getExecutionContext().getInfo().getAttempt() <= n) {
                throw new RuntimeException(name + " unavailable");
            }
        }

        @Override
        public WealthManagementClient getClient(String clientId) {
            calls.add("get_client");
            return new WealthManagementClient(clientId, "Jane", "Doe", "1 Main St", "555-0199",
                    "jane@example.com", "single");
        }

        @Override
        public String updateClient(String clientId, Map<String, Object> fields) {
            updatedFields = fields;
            return "updated";
        }

        @Override
        public Map<String, Object> openInvestment(InvestmentAccount account) {
            calls.add("open_investment#" + Activity.getExecutionContext().getInfo().getAttempt());
            maybeFail("open_investment");
            openedAccount = account;
            Map<String, Object> result = new HashMap<>();
            result.put("investment_id", INVESTMENT_ID);
            result.put("name", account.getName());
            result.put("balance", account.getBalance());
            return result;
        }

        @Override
        public boolean removeInvestment(String clientId, String investmentId) {
            calls.add("remove_investment:" + investmentId);
            return true;
        }

        @Override
        public Map<String, Object> debitLinkedAccount(String clientId, double amount, String transferId) {
            calls.add("debit:" + amount);
            maybeFail("debit_linked_account");
            return Map.of();
        }

        @Override
        public Map<String, Object> creditLinkedAccount(String clientId, double amount, String transferId) {
            calls.add("credit:" + amount);
            if (!transferId.endsWith("-refund")) {
                throw ApplicationFailure.newNonRetryableFailure("refund must use its own transfer ID", "BadTransferId");
            }
            return Map.of();
        }

        @Override
        public String registerAccount(String clientId, String investmentId, double amount) {
            calls.add("register");
            maybeFail("register_account");
            return "CUST-1";
        }
    }
}

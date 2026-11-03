package io.temporal.demo.wealthmanagement.workflow;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import io.temporal.client.WorkflowStub;
import io.temporal.demo.wealthmanagement.common.ServerInfo;
import io.temporal.demo.wealthmanagement.model.InvestmentAccount;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountInput;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountOutput;
import io.temporal.demo.wealthmanagement.model.UpdateAccountOpeningStateInput;
import io.temporal.demo.wealthmanagement.model.WealthManagementClient;
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
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class OpenInvestmentAccountWorkflowTest {
    private static final String OPEN_ACCOUNT_QUEUE = "OpenAccount-test";
    private static final String CLIENT_ID = "123";
    private static final String ACCOUNT_NAME = "College Fund";
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

        assertEquals(List.of(
                ACCOUNT_NAME + ": Waiting KYC",
                ACCOUNT_NAME + ": Waiting Compliance Review",
                ACCOUNT_NAME + ": Compliance review has been approved. Creating Investment Account",
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
            child.run(new OpenInvestmentAccountInput(CLIENT_ID, ACCOUNT_NAME, 1000.0));
            // let the final state signal arrive before completing
            Workflow.await(Duration.ofSeconds(5), () -> states.contains(ACCOUNT_NAME + ": Complete"));
        }

        @Override
        public void updateAccountOpeningState(UpdateAccountOpeningStateInput input) {
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
    }

    static class FakePythonActivities implements PythonActivities {
        volatile InvestmentAccount openedAccount;
        volatile Map<String, Object> updatedFields;

        @Override
        public WealthManagementClient getClient(String clientId) {
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
            openedAccount = account;
            Map<String, Object> result = new HashMap<>();
            result.put("investment_id", "I-1");
            result.put("name", account.getName());
            result.put("balance", account.getBalance());
            return result;
        }
    }
}

package io.temporal.demo.wealthmanagement.workflow;

import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountInput;
import io.temporal.demo.wealthmanagement.model.OpenInvestmentAccountOutput;
import io.temporal.demo.wealthmanagement.model.WealthManagementClient;
import io.temporal.workflow.QueryMethod;
import io.temporal.workflow.SignalMethod;
import io.temporal.workflow.UpdateMethod;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

import java.util.Map;

/**
 * Java version of src/temporal_supervisor/workflows/open_account_workflow.py.
 * The workflow type and the handler names match the Python version, so the Python
 * supervisor and activities can start and drive either implementation.
 */
@WorkflowInterface
public interface OpenInvestmentAccountWorkflow {
    @WorkflowMethod(name = "OpenInvestmentAccountWorkflow")
    OpenInvestmentAccountOutput run(OpenInvestmentAccountInput inputs);

    @QueryMethod(name = "get_current_state")
    String getCurrentState();

    @UpdateMethod(name = "get_client_details")
    WealthManagementClient getClientDetails();

    @UpdateMethod(name = "update_client_details")
    String updateClientDetails(Map<String, Object> client);

    @SignalMethod(name = "verify_kyc")
    void verifyKyc();

    @SignalMethod(name = "compliance_approved")
    void complianceApproved();
}

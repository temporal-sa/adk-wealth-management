import pytest
from temporalio.testing import WorkflowEnvironment

import tests.scripted_model as model
from tests.chat_harness import chat_workers, kyc_approvals


@pytest.fixture(scope="module")
async def client():
    """A client for chats run by the scripted model (see chat_harness.py)."""
    async with await WorkflowEnvironment.start_local() as env:
        async with chat_workers(env.client) as client:
            yield client


@pytest.fixture
def scripted():
    """Fresh model script for each test, and a check that every scripted response was used."""
    model.reset()
    kyc_approvals.clear()
    yield model
    assert not model.unused(), f"scripted responses were not used: {model.unused()}"

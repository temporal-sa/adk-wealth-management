# Open Account Workflow in Java

A Java version of [open_account_workflow.py](../src/temporal_supervisor/workflows/open_account_workflow.py).
It shows a polyglot Temporal application. The supervisor (AI) workflow and every activity run in Python, and
only the Open Account child workflow runs here.

How the two sides fit together:

* The Python supervisor starts `OpenInvestmentAccountWorkflow` as a child workflow on the
  `TEMPORAL_TASK_QUEUE_OPEN_ACCOUNT` task queue, which this worker polls.
* This workflow calls the Python activities (`get_client`, `update_client`, and the saga steps
  `open_investment`, `debit_linked_account`, `register_account` with their compensations `remove_investment` and
  `credit_linked_account`) by name on the Python task queue (`TEMPORAL_TASK_QUEUE`, default `Supervisor`).
  The chaos panel's flags are read inside those activities, so every failure scenario works with this worker too.
* It reports progress by signalling `update_account_opening_state` on the parent workflow, as the Python
  version does. Signals are routed by workflow ID, so the different task queue doesn't matter.
* The workflow type, query, update and signal names match the Python version, so the Python agent tools and
  `localsendcomplianceapproval.sh` drive either implementation unchanged.
* When `USE_CLAIM_CHECK=true`, payloads go through the same Redis claim check as the Python worker, in the
  same format, so either side (and the codec server) can decode them.

## Prerequisites

* Java JDK 25 (Gradle 9.8.2 is provided by the wrapper, so no separate Gradle install)
* Everything the Python demo needs (see [the main README](../src/temporal_supervisor/README.md))

## Running

Pass the same task queue name to the Python worker and to this worker.

```bash
# terminal 1
cd src/temporal_supervisor
./startlocalworker.sh OpenAccount

# terminal 2
cd java
./startlocalworker.sh OpenAccount
```

To try the Java workflow without the chat or a Gemini key, run the open-account scenarios against it
instead of the Python worker (with this worker running):
`src/temporal_supervisor/startlocalopenaccount.sh OpenAccount`. See "Testing the open-account workflow
on its own" in [the main README](../src/temporal_supervisor/README.md).

For Temporal Cloud, use `startcloudworker.sh` in both places. It reads the connection settings from
`setcloudenv.sh` in the project root, the same file the Python worker uses.

Both scripts load `../setclaimcheck.sh` if it exists, then call `runworker.sh`, which builds the jar
(`./gradlew -q bootJar`), adds the JDK 24+ flags that silence gRPC/Netty's `sun.misc.Unsafe` and native-access
warnings, activates the `mtls` Spring profile when `TEMPORAL_TLS_CLIENT_CERT_PATH` is set, and starts the worker
(its PID goes to `.worker.pid`). The worker logs the connection and auth mode it uses at startup. If a
connection setting seems to be ignored, check that line.

### mTLS client key (Temporal Cloud)

Java's TLS needs the client key as an unencrypted PKCS8 key (`-----BEGIN PRIVATE KEY-----`). When
`TEMPORAL_TLS_CLIENT_CERT_PATH` is set, `startcloudworker.sh` checks the key; if
`TEMPORAL_TLS_CLIENT_KEY_PATH` points to a different format (for example PKCS1, `-----BEGIN RSA PRIVATE KEY-----`),
the script stops and prints the command to convert it, for example:

```bash
openssl pkcs8 -topk8 -nocrypt -in /path/to/key.key -out /path/to/key.pkcs8.key
```

Then add the converted key to `setcloudenv.sh` (the Python components keep using
`TEMPORAL_TLS_CLIENT_KEY_PATH`):

```bash
export TEMPORAL_KEY_PKCS8_PATH="/path/to/key.pkcs8.key"
```

The `mtls` profile (`src/main/resources/application-mtls.yaml`) reads `TEMPORAL_KEY_PKCS8_PATH` when it is set,
otherwise `TEMPORAL_TLS_CLIENT_KEY_PATH`. API key auth needs no key file, so the check is skipped.

## Connecting

Connection settings come from the same `TEMPORAL_*` variables the Temporal CLI and the Python worker use.

| Variable | Default | Purpose |
|---|---|---|
| TEMPORAL_ADDRESS | localhost:7233 | Frontend address |
| TEMPORAL_NAMESPACE | default | Namespace |
| TEMPORAL_API_KEY | empty | API key auth (enables TLS) |
| TEMPORAL_TLS_CLIENT_CERT_PATH, TEMPORAL_TLS_CLIENT_KEY_PATH | empty | mTLS auth (the start scripts then activate the `mtls` profile) |
| TEMPORAL_KEY_PKCS8_PATH | empty | PKCS8 copy of the mTLS key; overrides TEMPORAL_TLS_CLIENT_KEY_PATH for Java only |
| TEMPORAL_TASK_QUEUE_OPEN_ACCOUNT | required | Task queue this worker polls |
| TEMPORAL_TASK_QUEUE | Supervisor | Python task queue that hosts the activities |
| USE_CLAIM_CHECK, REDIS_HOST, REDIS_PORT | false, localhost, 6379 | Claim check codec |

An API key turns on TLS automatically. If you run the jar yourself with mTLS, add the profile:
`java -jar build/libs/open-account-worker.jar --spring.profiles.active=mtls`.

Keep API keys and certificates out of the repository.

## Bug in the workflow code (chaos panel)

The chaos panel's **Bug in workflow code** switch gives new account openings the promo code
`WELCOME-BONUS`. In `src/main/java/io/temporal/demo/wealthmanagement/workflow/OpenInvestmentAccountWorkflowImpl.java`,
`BONUS_RATES` is keyed by `"WELCOME_BONUS"` (underscore), so the lookup in `welcomeBonus` returns null and
unboxing it throws a `NullPointerException` right after compliance approval. The exception fails the workflow
task, not the workflow, so the workflow stays **Running**, paused at that line with its state intact. To fix it,
use the commented-out `BONUS_RATES` line above it and restart the Java worker; the workflow resumes on the next
workflow-task retry and opens the account with the bonus. Re-arm the bug afterwards, from the repository root:

```bash
git checkout java/src/main/java/io/temporal/demo/wealthmanagement/workflow/OpenInvestmentAccountWorkflowImpl.java
```

See [Failure Scenarios](../src/temporal_supervisor/README.md#failure-scenarios-chaos-panel) for the other scenarios.

## Tests

```bash
./gradlew test
```

The workflow tests run against the Temporal test server, with fake Python activities and a test parent
workflow that records the state signals. They cover the saga (happy path, rollback in reverse order, undoing
only completed steps, transient retries) and the promo-code bug pausing the workflow.

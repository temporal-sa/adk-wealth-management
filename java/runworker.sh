#!/bin/bash
# Builds and starts the Java Open Account worker. Called by startlocalworker.sh and startcloudworker.sh.
# exec keeps the PID, so the worker can be found and stopped by PID (see .worker.pid).
cd "$(dirname "$0")"
if [ -z "$TEMPORAL_TASK_QUEUE_OPEN_ACCOUNT" ]; then
  echo "**** TEMPORAL_TASK_QUEUE_OPEN_ACCOUNT is not set"
  exit 1
fi
# Java 24+ warns at startup when gRPC's bundled Netty uses sun.misc.Unsafe or loads native code.
# The flags only exist from Java 23, so add them only where the warnings appear.
JAVA_OPTS=()
java_major="$(java -XshowSettings:properties -version 2>&1 | awk -F' = ' '/java.specification.version/ {print $2}')"
if [ "${java_major%%.*}" -ge 24 ] 2>/dev/null; then
  JAVA_OPTS+=(--enable-native-access=ALL-UNNAMED --sun-misc-unsafe-memory-access=allow)
fi
./gradlew -q bootJar || exit 1
echo $$ > .worker.pid
# mTLS certificates are only read when the mtls profile is active
exec java "${JAVA_OPTS[@]}" -jar build/libs/open-account-worker.jar \
  ${TEMPORAL_TLS_CLIENT_CERT_PATH:+--spring.profiles.active=mtls}

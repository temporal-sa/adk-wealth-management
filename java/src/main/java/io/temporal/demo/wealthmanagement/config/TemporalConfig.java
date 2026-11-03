package io.temporal.demo.wealthmanagement.config;

import io.temporal.client.WorkflowClientOptions;
import io.temporal.common.converter.CodecDataConverter;
import io.temporal.common.converter.DefaultDataConverter;
import io.temporal.demo.wealthmanagement.claimcheck.ClaimCheckCodec;
import io.temporal.serviceclient.WorkflowServiceStubsOptions;
import io.temporal.spring.boot.TemporalOptionsCustomizer;
import io.temporal.spring.boot.autoconfigure.properties.TemporalProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.net.InetAddress;
import java.util.Collections;

/**
 * The Temporal Spring Boot starter builds the client and the worker from the spring.temporal
 * properties in application.yaml. This class only adds what those properties cannot express.
 */
@Configuration
public class TemporalConfig {
    private static final Logger log = LoggerFactory.getLogger(TemporalConfig.class);

    @Value("${claim-check.enabled}")
    private boolean claimCheckEnabled;

    @Bean
    public TemporalOptionsCustomizer<WorkflowServiceStubsOptions.Builder> serviceStubsCustomizer(
            TemporalProperties properties) {
        // A misspelled variable is silently ignored, so say what is actually in use.
        log.info("Temporal connection: address={} namespace={} auth={}",
                properties.getConnection().getTarget(),
                properties.getNamespace(),
                authMode(properties));
        return builder -> builder;
    }

    @Bean
    public TemporalOptionsCustomizer<WorkflowClientOptions.Builder> clientCustomizer(ClaimCheckCodec claimCheckCodec) {
        return builder -> {
            builder.setIdentity("open-account-java@" + host() + "-" + ProcessHandle.current().pid());
            if (claimCheckEnabled) {
                log.info("Using claim check codec (USE_CLAIM_CHECK=true)");
                builder.setDataConverter(new CodecDataConverter(
                        DefaultDataConverter.newDefaultInstance(),
                        Collections.singletonList(claimCheckCodec)));
            }
            return builder;
        };
    }

    private static String authMode(TemporalProperties properties) {
        String apiKey = properties.getConnection().getApiKey();
        if (apiKey != null && !apiKey.isEmpty()) {
            return "api-key";
        }
        return properties.getConnection().getMTLS() != null ? "mtls" : "none";
    }

    private static String host() {
        try {
            return InetAddress.getLocalHost().getHostName();
        } catch (Exception e) {
            return "unknown-host";
        }
    }
}

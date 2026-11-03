package io.temporal.demo.wealthmanagement;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Runs the Open Account workflow in Java while the supervisor (AI) workflow and all of the
 * activities stay in Python. The Temporal Spring Boot starter creates the client and the worker
 * from the spring.temporal properties in application.yaml.
 */
@SpringBootApplication
public class StartTheWorker {
    public static void main(String[] args) {
        SpringApplication.run(StartTheWorker.class, args);
    }
}

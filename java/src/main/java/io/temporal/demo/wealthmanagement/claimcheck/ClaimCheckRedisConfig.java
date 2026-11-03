package io.temporal.demo.wealthmanagement.claimcheck;

import io.temporal.api.common.v1.Payload;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.serializer.RedisSerializer;
import org.springframework.data.redis.serializer.SerializationException;
import org.springframework.data.redis.serializer.StringRedisSerializer;

@Configuration
public class ClaimCheckRedisConfig {

    // Keys are plain strings and values are serialized Payload protobufs, the same
    // format the Python ClaimCheckCodec writes, so either language can decode the other's payloads.
    @Bean
    public RedisTemplate<String, Payload> claimCheckRedisTemplate(RedisConnectionFactory connectionFactory) {
        RedisTemplate<String, Payload> template = new RedisTemplate<>();
        template.setConnectionFactory(connectionFactory);
        template.setKeySerializer(new StringRedisSerializer());
        template.setValueSerializer(new PayloadRedisSerializer());
        return template;
    }

    static class PayloadRedisSerializer implements RedisSerializer<Payload> {
        @Override
        public byte[] serialize(Payload payload) {
            return payload == null ? null : payload.toByteArray();
        }

        @Override
        public Payload deserialize(byte[] bytes) {
            if (bytes == null) {
                return null;
            }
            try {
                return Payload.parseFrom(bytes);
            } catch (Exception e) {
                throw new SerializationException("Could not deserialize Payload", e);
            }
        }
    }
}

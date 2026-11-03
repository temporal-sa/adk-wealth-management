package io.temporal.demo.wealthmanagement.claimcheck;

import com.google.protobuf.ByteString;
import io.temporal.api.common.v1.Payload;
import io.temporal.common.converter.DataConverterException;
import io.temporal.common.converter.EncodingKeys;
import io.temporal.payload.codec.PayloadCodec;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.stereotype.Component;

import javax.annotation.Nonnull;
import java.util.List;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * Claim check pattern: each payload is stored in Redis and replaced with its key in the event
 * history. Wire-compatible with src/temporal_supervisor/claim_check/claim_check_codec.py.
 */
@Component
public class ClaimCheckCodec implements PayloadCodec {
    private static final ByteString ENCODING = ByteString.copyFromUtf8("binary/claim-check");
    private static final String CLAIM_CHECK_CODEC_KEY = "temporal.io/claim-check-codec";
    private static final ByteString CLAIM_CHECK_CODEC_VERSION = ByteString.copyFromUtf8("v1");

    private final RedisTemplate<String, Payload> redisTemplate;

    public ClaimCheckCodec(RedisTemplate<String, Payload> claimCheckRedisTemplate) {
        this.redisTemplate = claimCheckRedisTemplate;
    }

    @Nonnull
    @Override
    public List<Payload> encode(@Nonnull List<Payload> payloads) {
        return payloads.stream().map(this::encodePayload).collect(Collectors.toList());
    }

    @Nonnull
    @Override
    public List<Payload> decode(@Nonnull List<Payload> payloads) {
        return payloads.stream().map(this::decodePayload).collect(Collectors.toList());
    }

    private Payload encodePayload(Payload payload) {
        String claimKey = UUID.randomUUID().toString();
        redisTemplate.opsForValue().set(claimKey, payload);
        return Payload.newBuilder()
                .putMetadata(EncodingKeys.METADATA_ENCODING_KEY, ENCODING)
                .putMetadata(CLAIM_CHECK_CODEC_KEY, CLAIM_CHECK_CODEC_VERSION)
                .setData(ByteString.copyFromUtf8(claimKey))
                .build();
    }

    private Payload decodePayload(Payload payload) {
        // Pass through anything that was not claim-checked
        if (!CLAIM_CHECK_CODEC_VERSION.equals(payload.getMetadataOrDefault(CLAIM_CHECK_CODEC_KEY, null))) {
            return payload;
        }
        String claimKey = payload.getData().toStringUtf8();
        Payload stored;
        try {
            stored = redisTemplate.opsForValue().get(claimKey);
        } catch (Exception e) {
            throw new DataConverterException(e);
        }
        if (stored == null) {
            throw new DataConverterException("Claim check key not found in Redis: " + claimKey, null);
        }
        return stored;
    }
}

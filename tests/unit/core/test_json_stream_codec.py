from antcode_core.infrastructure.redis.stream_client import JsonCodec


def test_json_codec_round_trips_boolean_fields_as_json():
    codec = JsonCodec()

    encoded = codec.encode({"success": False, "retry": True})

    assert encoded == {"success": "false", "retry": "true"}
    assert codec.decode(encoded) == {"success": False, "retry": True}

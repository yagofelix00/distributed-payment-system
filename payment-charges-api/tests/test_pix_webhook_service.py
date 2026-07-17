from services.pix_webhook_service import check_charge_ttl


class RedisStub:
    def __init__(self, exists_result=None, exists_error=None):
        self.exists_result = exists_result
        self.exists_error = exists_error
        self.keys_checked = []

    def exists(self, key):
        self.keys_checked.append(key)
        if self.exists_error:
            raise self.exists_error
        return self.exists_result


def test_check_charge_ttl_returns_present_when_ttl_key_exists(monkeypatch):
    redis_stub = RedisStub(exists_result=1)
    monkeypatch.setattr("services.pix_webhook_service.redis_client", redis_stub)

    result = check_charge_ttl("ext-ttl-present")

    assert result == "present"
    assert redis_stub.keys_checked == ["charge:ttl:ext-ttl-present"]


def test_check_charge_ttl_returns_missing_when_ttl_key_does_not_exist(monkeypatch):
    redis_stub = RedisStub(exists_result=0)
    monkeypatch.setattr("services.pix_webhook_service.redis_client", redis_stub)

    result = check_charge_ttl("ext-ttl-missing")

    assert result == "missing"
    assert redis_stub.keys_checked == ["charge:ttl:ext-ttl-missing"]


def test_check_charge_ttl_returns_unavailable_when_redis_exists_raises(monkeypatch):
    redis_stub = RedisStub(exists_error=RuntimeError("Redis unavailable"))
    monkeypatch.setattr("services.pix_webhook_service.redis_client", redis_stub)
    monkeypatch.setattr(
        "services.pix_webhook_service.logger.exception",
        lambda *args, **kwargs: None,
    )

    result = check_charge_ttl("ext-ttl-unavailable")

    assert result == "unavailable"
    assert redis_stub.keys_checked == ["charge:ttl:ext-ttl-unavailable"]

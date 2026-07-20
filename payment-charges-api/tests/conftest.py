import pathlib
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.expirations = {}
        self.ttls = {}
        self.now = 0
        self._lock = threading.RLock()

    def _expire_if_needed(self, key):
        expires_at = self.expirations.get(key)
        if expires_at is not None and expires_at <= self.now:
            self.store.pop(key, None)
            self.expirations.pop(key, None)
            self.ttls.pop(key, None)

    def advance_time(self, seconds):
        with self._lock:
            self.now += seconds
            for key in list(self.expirations):
                self._expire_if_needed(key)

    def get(self, key):
        with self._lock:
            self._expire_if_needed(key)
            return self.store.get(key)

    def set(self, key, value, nx=False, ex=None):
        with self._lock:
            self._expire_if_needed(key)

            if nx and key in self.store:
                return False

            self.store[key] = value

            if ex is None:
                self.expirations.pop(key, None)
                self.ttls.pop(key, None)
            else:
                self.expirations[key] = self.now + ex
                self.ttls[key] = ex

            return True

    def setex(self, key, ttl, value):
        with self._lock:
            self.store[key] = value
            self.expirations[key] = self.now + ttl
            self.ttls[key] = ttl

    def exists(self, key):
        with self._lock:
            self._expire_if_needed(key)
            return 1 if key in self.store else 0

    def delete(self, key):
        with self._lock:
            self.store.pop(key, None)
            self.expirations.pop(key, None)
            self.ttls.pop(key, None)


@pytest.fixture
def fake_redis():
    return FakeRedis()

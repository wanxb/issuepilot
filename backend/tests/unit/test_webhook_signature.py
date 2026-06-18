"""verify_signature 单元测试（纯函数，无 IO）。"""
from __future__ import annotations

import hashlib
import hmac

import pytest

from app.api.webhooks import verify_signature


SECRET = "test-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature() -> None:
    body = b'{"action":"closed"}'
    sig = _sign(body)
    assert verify_signature(secret=SECRET, body=body, signature_header=sig) is True


def test_missing_signature() -> None:
    body = b'{}'
    assert verify_signature(secret=SECRET, body=body, signature_header=None) is False
    assert verify_signature(secret=SECRET, body=body, signature_header="") is False


def test_wrong_prefix() -> None:
    body = b'{}'
    bad = "sha1=" + hmac.new(SECRET.encode(), body, hashlib.sha1).hexdigest()
    assert verify_signature(secret=SECRET, body=body, signature_header=bad) is False


def test_tampered_body() -> None:
    body = b'{"a":1}'
    sig = _sign(body)
    tampered = b'{"a":2}'
    assert verify_signature(secret=SECRET, body=tampered, signature_header=sig) is False


def test_wrong_secret() -> None:
    body = b'{}'
    sig = "sha256=" + hmac.new(b"other-secret", body, hashlib.sha256).hexdigest()
    assert verify_signature(secret=SECRET, body=body, signature_header=sig) is False

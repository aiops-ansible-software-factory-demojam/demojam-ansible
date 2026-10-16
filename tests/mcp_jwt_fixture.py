"""Test-only RSA issuer fixture for the stock MCP server's JWT verifier."""

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://issuer.test"


def token(key, **claims):
    return jwt.encode({
        "iss": ISSUER, "aud": "webapp-mcp", "sub": "test-service-account",
        "azp": "demojam-ao-rca", "jti": "test-token", "iat": int(time.time()),
        "exp": int(time.time()) + 3600, **claims,
    }, key, algorithm="RS256", headers={"kid": "test-key"})


def create(directory):
    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    (directory / "signing.key").write_bytes(pem)
    (directory / "signing.key").chmod(0o600)
    numbers = key.public_key().public_numbers()

    def encode(number):
        return base64.urlsafe_b64encode(number.to_bytes((number.bit_length() + 7) // 8, "big")).decode().rstrip("=")

    (directory / "jwks.json").write_text(json.dumps({"keys": [{
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "test-key",
        "n": encode(numbers.n), "e": encode(numbers.e),
    }]}))
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
        "-keyout", str(directory / "tls.key"), "-out", str(directory / "tls.crt"),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    material = {
        "expires_at": int(time.time()) + 3600, "jwt_issuer": ISSUER,
        "jwt_jwks_uri": "http://localhost:8765/jwks.json",
        "jwt_id": "test-token", "jwt_ca": (directory / "tls.crt").read_text(),
        "tls_cert": (directory / "tls.crt").read_text(),
        "tls_key": (directory / "tls.key").read_text(),
        "ca_cert": (directory / "tls.crt").read_text(),
    }
    (directory / "mcp-material.json").write_text(json.dumps(material))
    (directory / "mcp-material.json").chmod(0o600)
    return pem


if __name__ == "__main__":
    create(Path(sys.argv[1]))

"""
Kalshi API authentication using RSA-PSS signatures.
"""

import base64
import time
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend


class KalshiAuth:
    """
    Handles RSA-PSS signature-based authentication for Kalshi API.

    Required headers:
    - KALSHI-ACCESS-KEY: Your API key ID
    - KALSHI-ACCESS-SIGNATURE: RSA-PSS signature of the request
    - KALSHI-ACCESS-TIMESTAMP: Request timestamp in milliseconds
    """

    def __init__(self, api_key_id: str, private_key_path: str = None, private_key_base64: str = None):
        self.api_key_id = api_key_id
        self.private_key = self._load_private_key(private_key_path, private_key_base64)

    def _load_private_key(self, path: str = None, base64_key: str = None):
        """Load private key from file or base64 string."""

        if path and Path(path).exists():
            with open(path, "rb") as f:
                key_data = f.read()
        elif base64_key:
            key_data = base64.b64decode(base64_key)
        else:
            raise ValueError("Must provide either private_key_path or private_key_base64")

        return serialization.load_pem_private_key(
            key_data,
            password=None,
            backend=default_backend()
        )

    def get_auth_headers(self, method: str, path: str, body: str = "") -> dict:
        """
        Generate authentication headers for a request.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g., /portfolio/balance)
            body: Request body as string (ignored - not part of signature)

        Returns:
            Dictionary with auth headers
        """
        timestamp = str(int(time.time() * 1000))

        # Create message to sign: timestamp + method + path (NO BODY per Kalshi docs)
        # Also strip query parameters from path
        path_without_query = path.split('?')[0]
        message = f"{timestamp}{method.upper()}{path_without_query}"
        message_bytes = message.encode("utf-8")

        # Sign with RSA-PSS using DIGEST_LENGTH per Kalshi docs
        signature = self.private_key.sign(
            message_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )

        # Base64 encode signature
        signature_b64 = base64.b64encode(signature).decode("utf-8")

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature_b64,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }


def generate_key_pair(save_path: str = None) -> tuple[str, str]:
    """
    Generate a new RSA key pair for Kalshi API.

    Returns:
        Tuple of (public_key_pem, private_key_pem)
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode("utf-8")

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")

    if save_path:
        with open(f"{save_path}/private_key.pem", "w") as f:
            f.write(private_pem)
        with open(f"{save_path}/public_key.pem", "w") as f:
            f.write(public_pem)
        print(f"Keys saved to {save_path}/")

    return public_pem, private_pem


if __name__ == "__main__":
    # Generate keys for setup
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "generate":
        save_to = sys.argv[2] if len(sys.argv) > 2 else "."
        pub, priv = generate_key_pair(save_to)
        print("Public key (paste this into Kalshi):")
        print(pub)

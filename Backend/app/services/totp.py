import hmac
import hashlib
import time
import base64
import struct
import secrets

def generate_base32_secret() -> str:
    """Generates a secure 16-character base32 secret key."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return "".join(secrets.choice(chars) for _ in range(16))

def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    """
    Verifies a 6-digit TOTP code against a base32 secret key.
    Allows for clock drift within the given window (number of 30-second steps).
    """
    try:
        # Normalize secret and add padding if missing
        secret = secret.strip().replace(" ", "").upper()
        missing_padding = len(secret) % 8
        if missing_padding:
            secret += "=" * (8 - missing_padding)
            
        key = base64.b32decode(secret, casefold=True)
        
        # Check current time window, as well as window-steps before and after
        for offset in range(-window, window + 1):
            counter = int(time.time() / 30) + offset
            msg = struct.pack(">Q", counter)
            digest = hmac.new(key, msg, hashlib.sha1).digest()
            
            # Dynamic truncation
            o = digest[-1] & 0x0f
            binary = struct.unpack(">I", digest[o:o+4])[0] & 0x7fffffff
            val = binary % 1000000
            
            if f"{val:06d}" == code.strip():
                return True
    except Exception:
        pass
    return False

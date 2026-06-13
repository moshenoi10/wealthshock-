import os
from functools import lru_cache

import config


@lru_cache(maxsize=1)
def get_client():
    from py_clob_client.client import ClobClient

    key = config.PRIVATE_KEY
    if not key or key == "paste_your_polymarket_private_key_here":
        raise ValueError("PRIVATE_KEY not configured in .env")

    # signature_type=2 → POLY_GNOSIS_SAFE (EIP-1271) for deposit wallet users
    client = ClobClient(
        host=config.POLYMARKET_HOST,
        key=key,
        chain_id=config.CHAIN_ID,
        signature_type=2,
    )

    try:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        print("[AUTH] API credentials derived successfully")
    except Exception as exc:
        print(f"[AUTH] Warning — could not derive API creds: {exc}")

    return client

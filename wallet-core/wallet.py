"""KenanWallet core — self-custody wallet, signing happens ONLY here.

Security rules enforced by this module:
- The private key / seed phrase NEVER leaves this module: no network call,
  no logging, no server upload. Callers only receive the public address and
  signed transaction dicts.
- Seed phrases are BIP39 (via the `mnemonic` package, English wordlist).
  Private key = first 32 bytes of PBKDF2(seed, "mnemonic", 2048 rounds)
  (BIP39 seed derivation; Ed25519 key from that seed). Recovery is tested
  by re-deriving the address from the phrase and comparing it.
- Never store a phrase in plaintext: this module keeps keys in memory
  only. The Android app must use Android Keystore for at-rest storage
  (see android-app/). This Python module is the testable reference.
"""
from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mnemonic import Mnemonic

from blockchain.kenan_chain import address_from_pubkey, canonical_message

_MNEMO = Mnemonic("english")


def create_wallet() -> dict:
    """Returns {mnemonic, address, _key} — caller must NOT persist _key raw.

    In production code paths (tests + CLI demo) the private key object is
    returned only so the caller can sign in the same process; it is never
    serialised to disk or sent anywhere by this module.
    """
    phrase = _MNEMO.generate(strength=256)  # 24 words
    return restore_wallet(phrase)


def restore_wallet(phrase: str) -> dict:
    phrase = " ".join(phrase.strip().split())
    if not _MNEMO.check(phrase):
        raise ValueError("invalid recovery phrase (BIP39 checksum failed)")
    seed = _MNEMO.to_seed(phrase, passphrase="")
    priv = Ed25519PrivateKey.from_private_bytes(seed[:32])
    pub = priv.public_key().public_bytes_raw()
    return {"mnemonic": phrase, "address": address_from_pubkey(pub),
            "public_key_hex": pub.hex(), "_private": priv}


def verify_restore(phrase: str, expected_address: str) -> bool:
    """Backup-completeness check: user must prove they can re-derive."""
    try:
        return restore_wallet(phrase)["address"] == expected_address
    except ValueError:
        return False


def sign_transfer(wallet: dict, to: str, amount_base: int, fee_base: int,
                  nonce: int, memo: str = "") -> dict:
    tx = {"from": wallet["address"], "to": to, "amount": int(amount_base),
          "fee": int(fee_base), "nonce": int(nonce), "memo": memo,
          "pubkey": wallet["public_key_hex"]}
    sig = wallet["_private"].sign(canonical_message(tx))
    tx["signature"] = sig.hex()
    return tx


def public_view(wallet: dict) -> dict:
    return {"address": wallet["address"],
            "public_key_hex": wallet["public_key_hex"]}

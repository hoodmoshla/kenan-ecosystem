//! Kenan Rust core — Ed25519 signing + KEN address derivation.
//! Mirrors the Python reference (same address format, same fee rule) so a
//! signature produced here verifies against the Python node and vice versa.
//! 1 KEN = 1_000_000_000 base units; minimum fee = 1_000_000 base units.
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use sha2::{Digest, Sha256};

pub const UNIT: u64 = 1_000_000_000;
pub const BASE_FEE: u64 = 1_000_000;
pub const CHAIN_ID: &str = "kenan-devnet-1";

pub fn address_from_pubkey(pubkey: &[u8]) -> String {
    let hash = Sha256::digest(pubkey);
    format!("kenan1{}", hex::encode(&hash[..20]))
}

pub fn canonical_message(from: &str, to: &str, amount: u64, fee: u64, nonce: u64, memo: &str) -> String {
    // Must match Python: chain id is signed (domain separation).
    format!("{}|{}|{}|{}|{}|{}|{}", CHAIN_ID, from, to, amount, fee, nonce, memo)
}

pub fn signing_key_from_seed(seed32: &[u8; 32]) -> SigningKey {
    SigningKey::from_bytes(seed32)
}

pub fn sign(signing_key: &SigningKey, msg: &[u8]) -> String {
    hex::encode(signing_key.sign(msg).to_bytes())
}

pub fn verify(pubkey_hex: &str, msg: &[u8], signature_hex: &str) -> bool {
    let Ok(pub_bytes) = hex::decode(pubkey_hex) else { return false };
    let Ok(sig_bytes) = hex::decode(signature_hex) else { return false };
    let Ok(vk) = VerifyingKey::from_bytes(&pub_bytes.try_into().unwrap_or([0u8; 32])) else { return false };
    let Ok(arr) = sig_bytes.try_into() else { return false };
    let sig = Signature::from_bytes(&arr);
    vk.verify(msg, &sig).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn sign_verify_roundtrip_and_tamper() {
        let key = signing_key_from_seed(&[7u8; 32]);
        let vk = key.verifying_key();
        let addr = address_from_pubkey(vk.as_bytes());
        assert!(addr.starts_with("kenan1") && addr.len() == 46);
        let msg = canonical_message(&addr, "kenan1", 1000, BASE_FEE, 0, "");
        let sig = sign(&key, msg.as_bytes());
        assert!(verify(&hex::encode(vk.as_bytes()), msg.as_bytes(), &sig));
        assert!(!verify(&hex::encode(vk.as_bytes()), b"tampered", &sig));
    }
    #[test]
    fn fee_rule() { assert_eq!(BASE_FEE, UNIT / 1000); }
}

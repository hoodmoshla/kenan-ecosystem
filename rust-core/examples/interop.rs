// Prints a fixed-seed keypair + signature for cross-checking against the
// Python node (tests/test_kenan.py verifies the same standard Ed25519).
// Run: cargo run --release --example interop
use rust_core::{address_from_pubkey, sign, signing_key_from_seed};

fn main() {
    let key = signing_key_from_seed(&[7u8; 32]);
    let vk = key.verifying_key();
    let addr = address_from_pubkey(vk.as_bytes());
    let msg = b"kenan-interop-test";
    println!("address={}", addr);
    println!("pubkey={}", hex::encode(vk.as_bytes()));
    println!("signature={}", sign(&key, msg));
    println!("message=kenan-interop-test");
}

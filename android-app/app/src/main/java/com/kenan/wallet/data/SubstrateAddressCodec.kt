package com.kenan.wallet.data

import io.novasama.substrate_sdk_android.ss58.SS58Encoder

/** Network-specific SS58 view of the existing Ed25519 public key; the wallet derivation is unchanged. */
object SubstrateAddressCodec {
    const val LOCAL_TEMPLATE_PREFIX: Short = 42

    fun fromPublicKeyHex(publicKeyHex: String): String {
        val publicKey = decodeHex(publicKeyHex)
        require(publicKey.size == 32) { "Ed25519 public key must be 32 bytes" }
        return SS58Encoder.encode(publicKey, LOCAL_TEMPLATE_PREFIX)
    }

    fun accountId(address: String): ByteArray {
        val prefix = SS58Encoder.extractAddressPrefix(address)
        require(prefix == LOCAL_TEMPLATE_PREFIX) { "Address belongs to another SS58 network prefix" }
        val accountId = SS58Encoder.decode(address)
        require(accountId.size == 32) { "Substrate address must decode to AccountId32" }
        return accountId
    }

    fun accountIdFromPublicKeyHex(publicKeyHex: String): ByteArray {
        val accountId = decodeHex(publicKeyHex)
        require(accountId.size == 32) { "Ed25519 public key must be 32 bytes" }
        return accountId
    }

    private fun decodeHex(value: String): ByteArray {
        val hex = value.removePrefix("0x")
        require(hex.length % 2 == 0 && hex.all { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' }) {
            "Invalid hexadecimal public key"
        }
        return ByteArray(hex.length / 2) { index -> hex.substring(index * 2, index * 2 + 2).toInt(16).toByte() }
    }
}

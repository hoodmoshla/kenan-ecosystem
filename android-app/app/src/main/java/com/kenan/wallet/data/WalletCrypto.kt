package com.kenan.wallet.data

import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import java.math.BigDecimal
import java.math.RoundingMode
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

const val DEVNET_CHAIN_ID = "kenan-devnet-1"
const val DEVNET_FEE_BASE = 1_000_000L
const val KEN_DECIMALS = 9

/** BIP39-24 / Ed25519 implementation matching wallet-core/wallet.py exactly. */
object WalletCrypto {
    private val wordList: List<String> by lazy {
        val stream = WalletCrypto::class.java.getResourceAsStream("/bip39-english.txt")
            ?: error("BIP39 English word list is missing")
        stream.bufferedReader(StandardCharsets.UTF_8).useLines { it.toList() }.also {
            require(it.size == 2048 && it.distinct().size == 2048) { "invalid BIP39 word list" }
        }
    }
    private val wordIndexes: Map<String, Int> by lazy { wordList.withIndex().associate { it.value to it.index } }
    private val random = SecureRandom()

    data class Identity(val address: String, val publicKeyHex: String)
    data class SignedTransfer(
        val from: String,
        val to: String,
        val amount: Long,
        val fee: Long,
        val nonce: Long,
        val memo: String,
        val publicKeyHex: String,
        val signatureHex: String
    ) {
        fun toJson(): String = "{\"from\":\"$from\",\"to\":\"$to\",\"amount\":$amount," +
            "\"fee\":$fee,\"nonce\":$nonce,\"memo\":\"\",\"pubkey\":\"$publicKeyHex\"," +
            "\"signature\":\"$signatureHex\"}"

        /** Matches blockchain.kenan_chain.tx_hash() (signature is excluded). */
        fun transactionHash(): String {
            val canonical = "{\"amount\":$amount,\"fee\":$fee,\"from\":\"$from\",\"memo\":\"$memo\",\"nonce\":$nonce,\"pubkey\":\"$publicKeyHex\",\"to\":\"$to\"}"
            return sha256(canonical.toByteArray(StandardCharsets.UTF_8)).toHex()
        }
    }

    fun createMnemonic(): String {
        val entropy = ByteArray(32).also(random::nextBytes)
        val checksum = MessageDigest.getInstance("SHA-256").digest(entropy)
        val phrase = (0 until 24).map { wordIndex ->
            var index = 0
            for (offset in 0 until 11) {
                val bit = wordIndex * 11 + offset
                val value = if (bit < 256) {
                    (entropy[bit / 8].toInt() ushr (7 - bit % 8)) and 1
                } else {
                    (checksum[0].toInt() ushr (7 - (bit - 256))) and 1
                }
                index = (index shl 1) or value
            }
            wordList[index]
        }.joinToString(" ")
        entropy.fill(0)
        checksum.fill(0)
        return phrase
    }

    fun normalizeAndValidate(phrase: String): String {
        val normalized = phrase.trim().split(Regex("\\s+")).joinToString(" ").lowercase()
        val words = normalized.split(' ')
        require(words.size == 24) { "أدخل عبارة استعادة من 24 كلمة" }
        val indexes = words.map { wordIndexes[it] ?: throw IllegalArgumentException("كلمة غير موجودة في قائمة BIP39") }
        val entropy = ByteArray(32)
        var suppliedChecksum = 0
        for ((wordPosition, index) in indexes.withIndex()) {
            for (offset in 0 until 11) {
                val bitPosition = wordPosition * 11 + offset
                val value = (index ushr (10 - offset)) and 1
                if (bitPosition < 256) {
                    if (value == 1) entropy[bitPosition / 8] = (entropy[bitPosition / 8].toInt() or (1 shl (7 - bitPosition % 8))).toByte()
                } else {
                    suppliedChecksum = (suppliedChecksum shl 1) or value
                }
            }
        }
        val expectedChecksum = (MessageDigest.getInstance("SHA-256").digest(entropy)[0].toInt() ushr 0) and 0xff
        entropy.fill(0)
        require(suppliedChecksum == expectedChecksum) { "عبارة الاستعادة غير صحيحة (فشل checksum)" }
        return normalized
    }

    fun identity(mnemonic: String): Identity {
        val normalized = normalizeAndValidate(mnemonic)
        val seed = bip39Seed(normalized)
        val privateKey = Ed25519PrivateKeyParameters(seed, 0)
        val publicKey = privateKey.generatePublicKey().encoded
        val address = "kenan1" + sha256(publicKey).copyOfRange(0, 20).toHex()
        seed.fill(0)
        publicKey.fill(0)
        return Identity(address, privateKey.generatePublicKey().encoded.toHex())
    }

    fun signTransfer(mnemonic: String, to: String, amountBase: Long, feeBase: Long, nonce: Long): SignedTransfer {
        val receiver = to.trim()
        require(isValidAddress(receiver)) { "عنوان المستلم غير صالح" }
        require(amountBase > 0) { "يجب أن يكون المبلغ أكبر من صفر" }
        require(feeBase == DEVNET_FEE_BASE) { "رسوم شبكة KEN التجريبية غير متوقعة" }
        require(nonce >= 0) { "nonce غير صالح" }
        val normalized = normalizeAndValidate(mnemonic)
        val seed = bip39Seed(normalized)
        try {
            val privateKey = Ed25519PrivateKeyParameters(seed, 0)
            val publicKey = privateKey.generatePublicKey().encoded
            val from = "kenan1" + sha256(publicKey).copyOfRange(0, 20).toHex()
            require(from != receiver) { "لا يمكن الإرسال إلى العنوان نفسه" }
            val message = "$DEVNET_CHAIN_ID|$from|$receiver|$amountBase|$feeBase|$nonce|".toByteArray(StandardCharsets.UTF_8)
            val signer = Ed25519Signer()
            signer.init(true, privateKey)
            signer.update(message, 0, message.size)
            val signature = signer.generateSignature()
            return SignedTransfer(from, receiver, amountBase, feeBase, nonce, "", publicKey.toHex(), signature.toHex())
        } finally {
            seed.fill(0)
        }
    }

    /** Signs the SCALE payload supplied by the Substrate SDK without changing the wallet's BIP39/Ed25519 derivation. */
    fun signSubstratePayload(mnemonic: String, payload: ByteArray, expectedAccountId: ByteArray): ByteArray {
        require(expectedAccountId.size == 32) { "Substrate AccountId must contain 32 bytes" }
        val normalized = normalizeAndValidate(mnemonic)
        val seed = bip39Seed(normalized)
        try {
            val privateKey = Ed25519PrivateKeyParameters(seed, 0)
            val publicKey = privateKey.generatePublicKey().encoded
            try {
                require(publicKey.contentEquals(expectedAccountId)) { "Local wallet key does not match Substrate AccountId" }
                val signer = Ed25519Signer()
                signer.init(true, privateKey)
                signer.update(payload, 0, payload.size)
                return signer.generateSignature()
            } finally {
                publicKey.fill(0)
            }
        } finally {
            seed.fill(0)
        }
    }

    fun parseKenAmount(value: String): Long {
        return parseAmount(value, KEN_DECIMALS)
    }

    fun parseAmount(value: String, decimals: Int): Long {
        require(decimals in 0..18) { "Unsupported token decimals" }
        val amount = try { BigDecimal(value.trim()) } catch (_: NumberFormatException) {
            throw IllegalArgumentException("أدخل مبلغًا صالحًا")
        }
        require(amount.signum() > 0) { "يجب أن يكون المبلغ أكبر من صفر" }
        return try { amount.setScale(decimals, RoundingMode.UNNECESSARY).movePointRight(decimals).longValueExact() }
        catch (_: ArithmeticException) { throw IllegalArgumentException("المبلغ يتجاوز الدقة أو الحد المدعوم") }
    }

    fun formatKen(baseUnits: Long): String = formatAmount(baseUnits, KEN_DECIMALS)

    fun formatAmount(baseUnits: Long, decimals: Int): String {
        require(decimals in 0..18) { "Unsupported token decimals" }
        return BigDecimal.valueOf(baseUnits, decimals).stripTrailingZeros().toPlainString()
    }

    fun isValidAddress(address: String): Boolean = Regex("kenan1[0-9a-f]{40}").matches(address)

    private fun bip39Seed(normalizedPhrase: String): ByteArray {
        val password = normalizedPhrase.toByteArray(StandardCharsets.UTF_8)
        val salt = "mnemonic".toByteArray(StandardCharsets.UTF_8) + byteArrayOf(0, 0, 0, 1)
        val mac = Mac.getInstance("HmacSHA512")
        mac.init(SecretKeySpec(password, "HmacSHA512"))
        var u = mac.doFinal(salt)
        val result = u.clone()
        repeat(2047) {
            u = mac.doFinal(u)
            for (i in result.indices) result[i] = (result[i].toInt() xor u[i].toInt()).toByte()
        }
        password.fill(0); salt.fill(0); u.fill(0)
        return result
    }

    private fun sha256(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)
    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it.toInt() and 0xff) }
}

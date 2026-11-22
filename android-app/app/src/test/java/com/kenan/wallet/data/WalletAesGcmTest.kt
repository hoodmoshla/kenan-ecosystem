package com.kenan.wallet.data

import javax.crypto.AEADBadTagException
import javax.crypto.spec.SecretKeySpec
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class WalletAesGcmTest {
    private val key = SecretKeySpec(ByteArray(16) { it.toByte() }, "AES")
    private val iv = ByteArray(12) { (it + 10).toByte() }
    private val aad = "test-wallet-aad".toByteArray(Charsets.UTF_8)
    private val plaintext = "unit test mnemonic data".toByteArray(Charsets.UTF_8)

    @Test fun fixedIvRoundTripDecryptsToOriginalPlaintext() {
        val ciphertext = WalletAesGcm.encrypt(key, iv, plaintext, aad)
        val decrypted = WalletAesGcm.decrypt(key, iv, ciphertext, aad)
        assertArrayEquals(plaintext, decrypted)
    }

    @Test fun modifiedIvAndAuthenticationTagAreRejected() {
        val ciphertext = WalletAesGcm.encrypt(key, iv, plaintext, aad)
        val changedIv = iv.clone().also { it[0] = (it[0].toInt() xor 1).toByte() }
        val changedTag = ciphertext.clone().also {
            val last = it.lastIndex
            it[last] = (it[last].toInt() xor 1).toByte()
        }
        assertThrows(AEADBadTagException::class.java) {
            WalletAesGcm.decrypt(key, changedIv, ciphertext, aad)
        }
        assertThrows(AEADBadTagException::class.java) {
            WalletAesGcm.decrypt(key, iv, changedTag, aad)
        }
    }
}

package com.kenan.wallet.data

import javax.crypto.Cipher
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Small AES-GCM helper shared by the wallet store and JVM tests. */
object WalletAesGcm {
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val TAG_LENGTH_BITS = 128

    data class Sealed(val iv: ByteArray, val ciphertext: ByteArray)

    /** Encrypts with a provider-generated nonce and returns the nonce with the authenticated ciphertext. */
    fun encrypt(key: SecretKey, plaintext: ByteArray, aad: ByteArray): Sealed {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key)
        cipher.updateAAD(aad)
        return Sealed(cipher.iv, cipher.doFinal(plaintext))
    }

    /** Encrypts with a supplied nonce; useful when the caller manages a unique nonce explicitly. */
    fun encrypt(key: SecretKey, iv: ByteArray, plaintext: ByteArray, aad: ByteArray): ByteArray {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(TAG_LENGTH_BITS, iv))
        cipher.updateAAD(aad)
        return cipher.doFinal(plaintext)
    }

    /** Decrypts with the exact nonce stored alongside the ciphertext. */
    fun decrypt(key: SecretKey, iv: ByteArray, ciphertext: ByteArray, aad: ByteArray): ByteArray {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(TAG_LENGTH_BITS, iv))
        cipher.updateAAD(aad)
        return cipher.doFinal(ciphertext)
    }
}

package com.kenan.wallet.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.system.Os
import android.util.Base64
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

/** Stores only an AES-GCM ciphertext in app-private storage; the AES key never leaves Android Keystore. */
class EncryptedWalletStore(context: Context) {
    private val appContext = context.applicationContext
    private val walletFile = File(appContext.filesDir, "kenan-wallet-v1.json")

    data class PublicWallet(val address: String, val publicKeyHex: String)

    fun loadWallet(): PublicWallet? {
        if (!walletFile.exists()) return null
        val envelope = JSONObject(walletFile.readText(StandardCharsets.UTF_8))
        require(envelope.getInt("version") == 1) { "إصدار ملف المحفظة غير مدعوم" }
        val iv = Base64.decode(envelope.getString("iv"), Base64.NO_WRAP)
        val ciphertext = Base64.decode(envelope.getString("ciphertext"), Base64.NO_WRAP)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, keyStoreKey())
        cipher.updateAAD(AAD)
        val plaintext = cipher.doFinal(ciphertext)
        try {
            val phrase = String(plaintext, StandardCharsets.UTF_8)
            val identity = WalletCrypto.identity(phrase)
            require(identity.address == envelope.getString("address")) { "بيانات المحفظة لا تطابق العنوان المحفوظ" }
            return PublicWallet(identity.address, identity.publicKeyHex)
        } finally {
            plaintext.fill(0)
            iv.fill(0)
            ciphertext.fill(0)
        }
    }

    fun saveMnemonic(input: String): PublicWallet {
        val phrase = WalletCrypto.normalizeAndValidate(input)
        val identity = WalletCrypto.identity(phrase)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, keyStoreKey())
        cipher.updateAAD(AAD)
        val plaintext = phrase.toByteArray(StandardCharsets.UTF_8)
        val ciphertext = try { cipher.doFinal(plaintext) } finally { plaintext.fill(0) }
        val envelope = JSONObject()
            .put("version", 1)
            .put("address", identity.address)
            .put("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .put("ciphertext", Base64.encodeToString(ciphertext, Base64.NO_WRAP))
        ciphertext.fill(0)
        val temp = File(appContext.filesDir, "kenan-wallet-v1.json.tmp")
        FileOutputStream(temp).use { stream ->
            stream.write(envelope.toString().toByteArray(StandardCharsets.UTF_8))
            stream.fd.sync()
        }
        try {
            Os.rename(temp.absolutePath, walletFile.absolutePath)
        } catch (e: Exception) {
            temp.delete()
            throw IllegalStateException("تعذر حفظ ملف المحفظة دون المساس بالنسخة السابقة", e)
        }
        return PublicWallet(identity.address, identity.publicKeyHex)
    }

    fun signTransfer(to: String, amountBase: Long, feeBase: Long, nonce: Long): WalletCrypto.SignedTransfer {
        val envelope = JSONObject(walletFile.readText(StandardCharsets.UTF_8))
        val iv = Base64.decode(envelope.getString("iv"), Base64.NO_WRAP)
        val ciphertext = Base64.decode(envelope.getString("ciphertext"), Base64.NO_WRAP)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, keyStoreKey())
        cipher.updateAAD(AAD)
        val plaintext = cipher.doFinal(ciphertext)
        try {
            val phrase = String(plaintext, StandardCharsets.UTF_8)
            val tx = WalletCrypto.signTransfer(phrase, to, amountBase, feeBase, nonce)
            require(tx.from == envelope.getString("address")) { "عنوان المحفظة لا يطابق المفتاح المحلي" }
            return tx
        } finally {
            plaintext.fill(0)
            iv.fill(0)
            ciphertext.fill(0)
        }
    }

    private fun keyStoreKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }

    companion object {
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val KEY_ALIAS = "com.kenan.wallet.devnet.aesgcm.v1"
        private val AAD = "com.kenan.wallet.wallet-file.v1".toByteArray(StandardCharsets.UTF_8)
    }
}

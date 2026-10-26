package com.kenan.wallet.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WalletCryptoTest {
    private val knownMnemonic = ("abandon ".repeat(23) + "art")

    @Test fun knownBip39IdentityMatchesPythonWallet() {
        val identity = WalletCrypto.identity(knownMnemonic)
        assertEquals("kenan1e28c3608979d45d2c7dc74b1c19519e54ce8961c", identity.address)
        assertEquals("1de352e44cd333672593f2334a730e180aaf290de89aa16d480de594e34e2961", identity.publicKeyHex)
    }

    @Test fun signingMatchesPythonDevnetCanonicalMessage() {
        val tx = WalletCrypto.signTransfer(knownMnemonic, "kenan1" + "a".repeat(40), 123456789, DEVNET_FEE_BASE, 0)
        assertEquals("kenan1e28c3608979d45d2c7dc74b1c19519e54ce8961c", tx.from)
        assertEquals("83996074af8b16499df9e5c75fce1de46e9495ed50585d5ed26548c949a50ed26f278e5b938df56ed63dd1bb109e00fa5f2504304b8bb18e5de48408a980e40b", tx.signatureHex)
        assertEquals("4c3c96158e11a3514a2c9a5c86ab56a52e45e1788ce90be80308f70f765fb61d", tx.transactionHash())
        assertTrue(tx.toJson().contains("\"signature\""))
    }

    @Test fun generatedMnemonicIsValidAndRestorable() {
        val phrase = WalletCrypto.createMnemonic()
        assertEquals(24, phrase.split(" ").size)
        assertTrue(WalletCrypto.isValidAddress(WalletCrypto.identity(phrase).address))
    }

    @Test fun invalidChecksumAndUnsupportedAmountsAreRejected() {
        try {
            WalletCrypto.normalizeAndValidate("abandon ".repeat(23) + "about")
            throw AssertionError("bad checksum should fail")
        } catch (_: IllegalArgumentException) { }
        assertEquals(1_250_000_000L, WalletCrypto.parseKenAmount("1.25"))
        assertEquals("0.001", WalletCrypto.formatKen(1_000_000))
        assertFalse(WalletCrypto.isValidAddress("kenan1short"))
        try {
            WalletCrypto.parseKenAmount("0.0000000001")
            throw AssertionError("more than nine decimal places should fail")
        } catch (_: IllegalArgumentException) { }
    }
}

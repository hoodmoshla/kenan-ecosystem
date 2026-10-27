package com.kenan.wallet.data

import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SubstrateAddressCodecTest {
    private val knownMnemonic = "abandon ".repeat(23) + "art"

    @Test fun currentWalletPublicKeyIsTheSubstrateAccountIdAndSs58View() {
        val identity = WalletCrypto.identity(knownMnemonic)
        val accountId = SubstrateAddressCodec.accountIdFromPublicKeyHex(identity.publicKeyHex)
        val address = SubstrateAddressCodec.fromPublicKeyHex(identity.publicKeyHex)

        assertEquals(SubstrateAddressCodec.LOCAL_TEMPLATE_PREFIX, io.novasama.substrate_sdk_android.ss58.SS58Encoder.extractAddressPrefix(address))
        assertArrayEquals(accountId, SubstrateAddressCodec.accountId(address))
        assertFalse(WalletCrypto.isValidAddress(address))
    }

    @Test fun substrateSignatureVerifiesAgainstTheUnchangedWalletPublicKey() {
        val identity = WalletCrypto.identity(knownMnemonic)
        val accountId = SubstrateAddressCodec.accountIdFromPublicKeyHex(identity.publicKeyHex)
        val payload = "SCALE transfer payload fixture".toByteArray(Charsets.UTF_8)
        val signature = WalletCrypto.signSubstratePayload(knownMnemonic, payload, accountId)
        val verifier = Ed25519Signer()
        verifier.init(false, Ed25519PublicKeyParameters(accountId, 0))
        verifier.update(payload, 0, payload.size)
        assertTrue(verifier.verifySignature(signature))
        assertEquals(64, signature.size)
    }

    @Test fun wrongAccountIdIsNeverSigned() {
        val identity = WalletCrypto.identity(knownMnemonic)
        val accountId = SubstrateAddressCodec.accountIdFromPublicKeyHex(identity.publicKeyHex)
        accountId[0] = (accountId[0].toInt() xor 1).toByte()
        try {
            WalletCrypto.signSubstratePayload(knownMnemonic, byteArrayOf(1), accountId)
            throw AssertionError("Mismatched AccountId32 must be rejected")
        } catch (_: IllegalArgumentException) { }
    }
}

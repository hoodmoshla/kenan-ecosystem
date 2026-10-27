package com.kenan.wallet.data

import io.novasama.substrate_sdk_android.encrypt.SignatureWrapper
import io.novasama.substrate_sdk_android.runtime.extrinsic.v5.transactionExtension.InheritedImplication
import io.novasama.substrate_sdk_android.runtime.extrinsic.v5.transactionExtension.extensions.verifySignature.GeneralTransactionSigner
import io.novasama.substrate_sdk_android.runtime.extrinsic.v5.transactionExtension.signingPayload
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.math.BigInteger

/** Runs against a real local Solochain node only when POLKADOT_RPC_URL is provided by CI. */
class SubstrateNodeApiIntegrationTest {
    @Test
    fun ed25519TransferFinalizesAndAConnectionFailureDoesNotDuplicateIt() = runBlocking {
        val endpoint = System.getenv("POLKADOT_RPC_URL")
        assumeTrue("POLKADOT_RPC_URL is not configured", !endpoint.isNullOrBlank())

        // Public test-only BIP39 vector already used by WalletCryptoTest; never a user wallet.
        val testMnemonic = ("abandon ".repeat(23) + "art")
        val senderPublicKeyHex = "1de352e44cd333672593f2334a730e180aaf290de89aa16d480de594e34e2961"
        val senderId = SubstrateAddressCodec.accountIdFromPublicKeyHex(senderPublicKeyHex)
        val recipientMnemonic = WalletCrypto.createMnemonic()
        val recipientIdentity = WalletCrypto.identity(recipientMnemonic)
        val recipientId = SubstrateAddressCodec.accountIdFromPublicKeyHex(recipientIdentity.publicKeyHex)
        val recipientAddress = SubstrateAddressCodec.fromPublicKeyHex(recipientIdentity.publicKeyHex)
        val api = SubstrateNodeApi(endpoint!!)
        val identity = api.inspectIdentity()
        assertTrue(identity.genesisHash.matches(Regex("0x[0-9a-f]{64}")))
        assertEquals("UNIT", identity.tokenSymbol)
        assertEquals(12, identity.tokenDecimals)

        val before = api.account(senderId)
        val recipientBefore = api.account(recipientId)
        assertTrue("test wallet must be funded", before.freeBalanceBase > 0)
        assertEquals("random receiver must start empty", 0L, recipientBefore.freeBalanceBase)
        val amount = BigInteger.valueOf(10L).pow(identity.tokenDecimals).longValueExactCompat()
        val quote = api.quoteTransfer(senderId, recipientId, recipientAddress, amount)
        assertTrue("quote must include enough to pay the transfer and fee", quote.balanceBase >= amount + quote.feeBase)
        assertEquals(before.nonce, quote.nonce)

        var preparedAfterFailedConnect = false
        try {
            SubstrateNodeApi("ws://127.0.0.1:1").sendTransfer(
                senderId,
                recipientId,
                quote,
                signer(testMnemonic)
            ) { preparedAfterFailedConnect = true }
            throw AssertionError("closed endpoint unexpectedly accepted a transfer")
        } catch (_: Exception) {
            // A connection failure before submission must not create a pending record or consume the nonce.
        }
        assertFalse("the signed transaction must not be prepared before RPC is connected", preparedAfterFailedConnect)
        assertEquals(before.nonce, api.account(senderId).nonce)

        var pending: PolkadotPendingTransfer? = null
        val finalized = api.sendTransfer(senderId, recipientId, quote, signer(testMnemonic)) {
            pending = it
        }
        assertNotNull("the exact signed extrinsic must be persisted before submission", pending)
        assertEquals(finalized.hash, pending!!.hash)
        assertEquals(quote.nonce, finalized.nonce)
        assertTrue(finalized.blockHeight > 0)
        assertNotNull(finalized.actualFeeBase)

        val after = api.account(senderId)
        assertEquals(before.nonce + 1, after.nonce)
        assertEquals(amount + finalized.actualFeeBase!!, before.freeBalanceBase - after.freeBalanceBase)
        assertEquals(amount, api.account(recipientId).freeBalanceBase)
        val history = api.recentTransactions(senderId, 20)
        assertEquals("one successful retry must produce exactly one on-chain extrinsic", 1, history.count { it.hash == finalized.hash })
        val resolution = api.resolvePending(pending!!)
        assertTrue("saved transaction must resolve to the same finalized transfer", resolution is PolkadotPendingResolution.Finalized)
        assertEquals(before.nonce + 1, api.account(senderId).nonce)
    }

    private fun signer(mnemonic: String) = object : GeneralTransactionSigner {
        override suspend fun signInheritedImplication(
            inheritedImplication: InheritedImplication,
            accountId: ByteArray
        ): SignatureWrapper {
            val payload = inheritedImplication.signingPayload()
            return try {
                val signature = WalletCrypto.signSubstratePayload(mnemonic, payload, accountId)
                SignatureWrapper.Ed25519(signature)
            } finally {
                payload.fill(0)
            }
        }
    }
}

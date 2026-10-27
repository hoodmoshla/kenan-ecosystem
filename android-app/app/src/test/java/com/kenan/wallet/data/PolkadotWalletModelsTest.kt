package com.kenan.wallet.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PolkadotWalletModelsTest {
    private val identity = PolkadotNetworkIdentity(
        chainName = "Development",
        systemName = "solochain-template-node",
        systemVersion = "1.0.0",
        genesisHash = "0x" + "ab".repeat(32),
        specName = "solochain-template-runtime",
        specVersion = 1,
        transactionVersion = 1,
        tokenSymbol = "UNIT",
        tokenDecimals = 12,
        finalizedHash = "0x" + "cd".repeat(32),
        finalizedHeight = 42
    )

    @Test fun chainPinMatchesOnlyTheExpectedGenesisAndAssetIdentity() {
        val pin = PolkadotChainPin.from(identity)
        assertTrue(pin.matches(identity.copy(finalizedHeight = 44)))
        assertFalse(pin.matches(identity.copy(genesisHash = "0x" + "ef".repeat(32))))
        assertFalse(pin.matches(identity.copy(tokenDecimals = 9)))
        assertEquals(pin, PolkadotChainPin.fromJson(pin.toJson()))
    }

    @Test fun pendingExtrinsicIsRestoredWithoutChangingItsHashOrBytes() {
        val pending = PolkadotPendingTransfer(
            hash = "0x" + "11".repeat(32),
            extrinsicHex = "0xdeadbeef",
            nonce = 7,
            genesisHash = identity.genesisHash,
            submittedAtHeight = 42,
            expiresAtHeight = 106
        )
        val restored = PolkadotPendingTransfer.fromJson(pending.toJson())
        assertNotNull(restored)
        assertEquals(pending, restored)
        assertTrue(PolkadotPendingTransfer.fromJson("not json") == null)
    }
}

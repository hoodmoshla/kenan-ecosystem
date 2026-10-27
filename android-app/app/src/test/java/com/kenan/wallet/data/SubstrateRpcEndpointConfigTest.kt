package com.kenan.wallet.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SubstrateRpcEndpointConfigTest {
    @Test fun emulatorReceivesOnlyTheLoopbackBridgeDefault() {
        assertEquals("ws://10.0.2.2:9944", SubstrateRpcEndpointConfig.resolve("", null, true))
        assertEquals("ws://10.0.2.2:9944", SubstrateRpcEndpointConfig.validate("WS://10.0.2.2:9944/", true))
    }

    @Test fun privatePhoneEndpointRequiresTlsUnlessLoopback() {
        assertEquals("wss://192.168.1.20:9944", SubstrateRpcEndpointConfig.validate("wss://192.168.1.20:9944", false))
        assertEquals("wss://node.local:9944", SubstrateRpcEndpointConfig.validate("wss://node.local:9944", false))
        assertEquals("ws://localhost:9944", SubstrateRpcEndpointConfig.validate("ws://localhost:9944", false))
        assertRejected("ws://node.local:9944", false)
    }

    @Test fun publicEndpointsAndPlaintextLanAreRejected() {
        assertRejected("wss://rpc.example.org:9944", false)
        assertRejected("ws://192.168.1.20:9944", false)
        assertRejected("ws://10.0.2.2:9944", false)
        assertNull(SubstrateRpcEndpointConfig.resolve("wss://rpc.example.org:9944", null, false))
    }

    @Test fun credentialsQueryFragmentsAndPathsAreRejected() {
        assertRejected("ws://user:pass@localhost:9944", true)
        assertRejected("ws://localhost:9944/rpc", true)
        assertRejected("ws://localhost:9944?token=x", true)
        assertRejected("ws://localhost:9944#fragment", true)
    }

    private fun assertRejected(value: String, isEmulator: Boolean) {
        try {
            SubstrateRpcEndpointConfig.validate(value, isEmulator)
            throw AssertionError("Expected rejection: $value")
        } catch (_: IllegalArgumentException) { }
    }
}

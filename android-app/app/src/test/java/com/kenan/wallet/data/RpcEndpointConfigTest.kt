package com.kenan.wallet.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class RpcEndpointConfigTest {
    @Test fun onlyEmulatorGetsAutomaticDefaultWhenUnset() {
        assertEquals("http://10.0.2.2:8545", RpcEndpointConfig.resolve("", null, isEmulator = true))
        assertNull(RpcEndpointConfig.resolve("", null, isEmulator = false))
    }

    @Test fun emulatorAliasInBuildDefaultIsRejectedOnPhysicalDevice() {
        assertEquals(
            "http://10.0.2.2:8545",
            RpcEndpointConfig.resolve("http://10.0.2.2:8545", null, isEmulator = true)
        )
        assertNull(RpcEndpointConfig.resolve("http://10.0.2.2:8545", null, isEmulator = false))
    }

    @Test fun validSavedOverrideTakesPrecedenceOverBuildDefault() {
        assertEquals(
            "https://192.168.1.40:9443",
            RpcEndpointConfig.resolve("http://10.0.2.2:8545", "https://192.168.1.40:9443", isEmulator = false)
        )
    }

    @Test fun invalidSavedOverrideFailsClosedInsteadOfUsingBuildDefault() {
        assertNull(
            RpcEndpointConfig.resolve("http://192.168.1.40:8545", "https://example.com", isEmulator = false)
        )
        assertNull(
            RpcEndpointConfig.resolve("http://192.168.1.40:8545", "http://10.0.2.2:8545", isEmulator = false)
        )
    }

    @Test fun validationAllowsExplicitLocalHttpAndHttpsOnly() {
        assertEquals("http://192.168.1.2:8545", RpcEndpointConfig.validate("http://192.168.1.2:8545/", false))
        assertEquals("https://node.local:9443", RpcEndpointConfig.validate("https://node.local:9443", false))
        assertEquals("http://localhost:8545", RpcEndpointConfig.validate("http://localhost:8545", false))
    }

    @Test fun validationRejectsCredentialsQueriesFragmentsRemoteAndBadSchemes() {
        listOf(
            "http://user:pass@192.168.1.2:8545",
            "http://192.168.1.2:8545?token=x",
            "http://192.168.1.2:8545#fragment",
            "https://example.com:8545",
            "ftp://192.168.1.2:8545",
            "http://192.168.1.2:99999"
        ).forEach { endpoint ->
            assertThrows(IllegalArgumentException::class.java) {
                RpcEndpointConfig.validate(endpoint, isEmulator = false)
            }
        }
    }

    @Test fun backendDerivationReplacesPortWithEightThousandOnlyForValidLocalUrl() {
        assertEquals("http://192.168.1.2:8000", RpcEndpointConfig.deriveBackend("http://192.168.1.2:8545"))
        assertEquals("http://10.0.2.2:8000", RpcEndpointConfig.deriveBackend("http://10.0.2.2:8545"))
        assertNull(RpcEndpointConfig.deriveBackend("https://example.com:8545"))
    }
}

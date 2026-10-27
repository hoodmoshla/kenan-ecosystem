package com.kenan.wallet.data

import java.net.Inet6Address
import java.net.InetAddress
import java.net.URI
import java.util.Locale

/** Local-only RPC endpoint policy for the generic Polkadot SDK development template. */
object SubstrateRpcEndpointConfig {
    private const val EMULATOR_ALIAS = "10.0.2.2"
    const val EMULATOR_DEFAULT = "ws://10.0.2.2:9944"

    fun validate(raw: String, isEmulator: Boolean): String {
        val value = raw.trim()
        require(value.isNotEmpty()) { "Substrate RPC endpoint is empty" }
        val uri = try { URI(value) } catch (_: Exception) {
            throw IllegalArgumentException("Invalid Substrate WebSocket RPC endpoint")
        }
        val scheme = uri.scheme?.lowercase(Locale.ROOT)
        require(scheme == "ws" || scheme == "wss") { "Substrate RPC must use ws or wss" }
        require(!uri.isOpaque && uri.rawAuthority != null && uri.host != null) { "Substrate RPC endpoint must include a host" }
        require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null) {
            "Substrate RPC endpoint cannot contain credentials, query, or fragment"
        }
        require(uri.rawPath.isNullOrEmpty() || uri.rawPath == "/") { "Substrate RPC endpoint cannot contain a path" }
        require(uri.port == -1 || uri.port in 1..65535) { "Substrate RPC endpoint port is invalid" }

        val host = uri.host.removeSurrounding("[", "]").lowercase(Locale.ROOT)
        require(isLocalHost(host)) { "Substrate RPC must use a local or private host; public RPC endpoints are blocked" }
        require(isEmulator || host != EMULATOR_ALIAS) { "10.0.2.2 is only available on the Android emulator" }
        if (scheme == "ws") {
            require(isCleartextLoopbackHost(host)) {
                "Unencrypted ws is allowed only through loopback or the Android emulator bridge; use wss for private LAN hosts"
            }
        }
        return uri.toASCIIString().replaceFirst(Regex("^[A-Za-z][A-Za-z0-9+.-]*:"), "$scheme:").trimEnd('/')
    }

    fun resolve(buildDefault: String, savedOverride: String?, isEmulator: Boolean): String? {
        val override = savedOverride?.trim().orEmpty()
        if (override.isNotEmpty()) return try { validate(override, isEmulator) } catch (_: IllegalArgumentException) { null }
        val configuredDefault = buildDefault.trim()
        if (configuredDefault.isNotEmpty()) return try { validate(configuredDefault, isEmulator) } catch (_: IllegalArgumentException) { null }
        return if (isEmulator) EMULATOR_DEFAULT else null
    }

    private fun isCleartextLoopbackHost(host: String): Boolean =
        host == "localhost" || host.endsWith(".localhost") ||
            host == "127.0.0.1" || host == "::1" || host == EMULATOR_ALIAS

    private fun isLocalHost(host: String): Boolean {
        if (host == "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host == EMULATOR_ALIAS) return true
        val octets = host.split('.')
        if (octets.size == 4 && octets.all { it.isNotEmpty() && it.all(Char::isDigit) }) {
            val ip = octets.map { it.toIntOrNull()?.takeIf { n -> n in 0..255 } ?: return false }
            return ip[0] == 10 || ip[0] == 127 ||
                (ip[0] == 172 && ip[1] in 16..31) ||
                (ip[0] == 192 && ip[1] == 168) ||
                (ip[0] == 169 && ip[1] == 254)
        }
        if (!host.contains(':')) return false
        return try {
            val address = InetAddress.getByName(host)
            address is Inet6Address && (
                address.isLoopbackAddress || address.isLinkLocalAddress ||
                    (address.address[0].toInt() and 0xfe) == 0xfc
                )
        } catch (_: Exception) { false }
    }
}

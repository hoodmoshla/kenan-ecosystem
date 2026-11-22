package com.kenan.wallet.data

import java.net.Inet6Address
import java.net.InetAddress
import java.net.URI
import java.util.Locale

/** Validation and resolution for explicitly local KEN devnet endpoints. */
object RpcEndpointConfig {
    private const val EMULATOR_ALIAS = "10.0.2.2"
    private const val EMULATOR_DEFAULT = "http://10.0.2.2:8545"

    /** Returns a normalized local HTTP(S) endpoint or throws for unsafe/invalid input. */
    fun validate(raw: String, isEmulator: Boolean): String {
        val value = raw.trim()
        require(value.isNotEmpty()) { "RPC endpoint is empty" }

        val uri = try {
            URI(value)
        } catch (_: Exception) {
            throw IllegalArgumentException("Invalid RPC endpoint")
        }
        val scheme = uri.scheme?.lowercase(Locale.ROOT)
        require(scheme == "http" || scheme == "https") { "RPC endpoint must use http or https" }
        require(!uri.isOpaque && uri.rawAuthority != null && uri.host != null) { "RPC endpoint must include a host" }
        require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null) {
            "RPC endpoint cannot contain credentials, query, or fragment"
        }
        require(uri.port == -1 || uri.port in 1..65535) { "RPC endpoint port is invalid" }

        val host = uri.host.removeSurrounding("[", "]").lowercase(Locale.ROOT)
        require(isLocalHost(host)) { "RPC endpoint must use a local host" }
        require(isEmulator || host != EMULATOR_ALIAS) { "10.0.2.2 is only available on the Android emulator" }

        val normalized = uri.toASCIIString()
        return normalized.replaceFirst(Regex("^[A-Za-z][A-Za-z0-9+.-]*:"), "$scheme:").trimEnd('/')
    }

    /** Resolves saved override first, then build default; only emulators receive an automatic default. */
    fun resolve(buildDefault: String, savedOverride: String?, isEmulator: Boolean): String? {
        val override = savedOverride?.trim().orEmpty()
        if (override.isNotEmpty()) return try {
            validate(override, isEmulator)
        } catch (_: IllegalArgumentException) {
            null
        }

        val configuredDefault = buildDefault.trim()
        if (configuredDefault.isNotEmpty()) return try {
            validate(configuredDefault, isEmulator)
        } catch (_: IllegalArgumentException) {
            null
        }

        return if (isEmulator) EMULATOR_DEFAULT else null
    }

    /** Derives the local backend URL by setting its port to 8000. */
    fun deriveBackend(nodeUrl: String): String? {
        val validated = try {
            validate(nodeUrl, isEmulator = true)
        } catch (_: IllegalArgumentException) {
            return null
        }
        return try {
            val uri = URI(validated)
            val host = uri.host.removeSurrounding("[", "]")
            val authorityHost = if (host.contains(':')) "[$host]" else host
            val path = uri.rawPath.orEmpty()
            URI("${uri.scheme}://$authorityHost:8000$path").toASCIIString()
        } catch (_: Exception) {
            null
        }
    }

    private fun isLocalHost(host: String): Boolean {
        if (host == "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) return true
        if (host == EMULATOR_ALIAS) return true

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
        } catch (_: Exception) {
            false
        }
    }
}

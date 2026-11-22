package com.kenan.wallet.data

import com.kenan.wallet.BuildConfig
import com.kenan.wallet.isAndroidEmulator
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

/** RPC adapter for the existing Python KEN devnet; signatures are made locally, never here. */
data class AccountState(val address: String, val balanceBase: Long, val nonce: Long, val network: String)
data class ChainInfo(val chainId: String, val height: Long, val supplyBase: Long, val feeBase: Long, val network: String)
data class ChainTransaction(
    val hash: String,
    val status: String,
    val blockHeight: Long,
    val from: String,
    val to: String,
    val amountBase: Long
)

class NodeHttpException(val statusCode: Int, message: String) : IllegalStateException(message)

class NodeApi(
    buildDefault: String = BuildConfig.KENAN_NODE_URL,
    savedOverride: String? = null,
    isEmulator: Boolean = isAndroidEmulator()
) {
    private val baseUrl = RpcEndpointConfig.resolve(buildDefault, savedOverride, isEmulator)

    private fun request(method: String, path: String, body: String? = null): JSONObject {
        val configuredBaseUrl = baseUrl ?: throw IllegalStateException("RPC endpoint is not configured")
        val connection = URL(configuredBaseUrl + path).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 8_000
            connection.readTimeout = 8_000
            connection.setRequestProperty("Accept", "application/json")
            if (body != null) {
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.doOutput = true
                connection.outputStream.use { it.write(body.toByteArray(StandardCharsets.UTF_8)) }
            }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader(StandardCharsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (code !in 200..299) throw NodeHttpException(code, "node error ($code): $text")
            return JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    private fun requestArray(path: String): JSONArray {
        val configuredBaseUrl = baseUrl ?: throw IllegalStateException("RPC endpoint is not configured")
        val connection = URL(configuredBaseUrl + path).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 8_000
            connection.readTimeout = 8_000
            connection.setRequestProperty("Accept", "application/json")
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader(StandardCharsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (code !in 200..299) throw NodeHttpException(code, "node error ($code): $text")
            return JSONArray(text)
        } finally {
            connection.disconnect()
        }
    }

    suspend fun chainInfo(): ChainInfo = withContext(Dispatchers.IO) {
        val json = request("GET", "/chain/info")
        ChainInfo(
            chainId = json.getString("chain_id"),
            height = json.getLong("height"),
            supplyBase = json.getLong("total_supply_base"),
            feeBase = json.getLong("fee_base"),
            network = json.optString("network", "unknown")
        )
    }

    suspend fun account(address: String): AccountState = withContext(Dispatchers.IO) {
        require(WalletCrypto.isValidAddress(address)) { "invalid Kenan devnet address" }
        val json = request("GET", "/accounts/$address")
        AccountState(
            address = json.getString("address"),
            balanceBase = json.getLong("balance_base"),
            nonce = json.getLong("nonce"),
            network = json.optString("network", "unknown")
        )
    }

    /** Returns recent confirmed transfers involving [address]; transport and parse errors are surfaced to the UI. */
    suspend fun recentTransactions(address: String, limit: Int = 20): List<ChainTransaction> = withContext(Dispatchers.IO) {
        if (!WalletCrypto.isValidAddress(address) || limit !in 1..100) return@withContext emptyList()
        val blocks = requestArray("/blocks?limit=$limit")
        val found = mutableListOf<ChainTransaction>()
        for (blockIndex in blocks.length() - 1 downTo 0) {
            val block = blocks.optJSONObject(blockIndex) ?: continue
            val blockHeight = block.optLong("height", -1L)
            if (blockHeight < 0) continue
            val transactions = block.optJSONArray("txs") ?: continue
            for (txIndex in transactions.length() - 1 downTo 0) {
                val tx = transactions.optJSONObject(txIndex) ?: continue
                val from = tx.optString("from", "")
                val to = tx.optString("to", "")
                val hash = tx.optString("hash", "")
                val status = tx.optString("status", "")
                val amountBase = tx.optLong("amount", -1L)
                val txHeight = if (tx.has("block_height")) tx.optLong("block_height", -1L) else blockHeight
                if (!WalletCrypto.isValidAddress(from) || !WalletCrypto.isValidAddress(to) ||
                    !Regex("[0-9a-f]{64}").matches(hash) || status != "confirmed" || txHeight < 0 || amountBase <= 0 ||
                    (from != address && to != address)
                ) continue
                found += ChainTransaction(hash, status, txHeight, from, to, amountBase)
                if (found.size >= limit) return@withContext found
            }
        }
        found
    }

    suspend fun submitTx(txJson: String): JSONObject = withContext(Dispatchers.IO) {
        request("POST", "/tx/submit", txJson)
    }

    suspend fun transaction(hash: String): JSONObject = withContext(Dispatchers.IO) {
        require(Regex("[0-9a-f]{64}").matches(hash)) { "invalid transaction hash" }
        request("GET", "/tx/$hash")
    }
}

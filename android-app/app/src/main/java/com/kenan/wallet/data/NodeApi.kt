package com.kenan.wallet.data

import com.kenan.wallet.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

/** RPC adapter for the existing Python KEN devnet; signatures are made locally, never here. */
data class AccountState(val address: String, val balanceBase: Long, val nonce: Long, val network: String)
data class ChainInfo(val chainId: String, val height: Long, val supplyBase: Long, val feeBase: Long, val network: String)
class NodeHttpException(val statusCode: Int, message: String) : IllegalStateException(message)

class NodeApi(private val baseUrl: String = BuildConfig.KENAN_NODE_URL.trimEnd('/')) {
    private fun request(method: String, path: String, body: String? = null): JSONObject {
        val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 8000
            connection.readTimeout = 8000
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

    suspend fun submitTx(txJson: String): JSONObject = withContext(Dispatchers.IO) {
        request("POST", "/tx/submit", txJson)
    }

    suspend fun transaction(hash: String): JSONObject = withContext(Dispatchers.IO) {
        require(Regex("[0-9a-f]{64}").matches(hash)) { "invalid transaction hash" }
        request("GET", "/tx/$hash")
    }
}

package com.kenan.wallet.data

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Minimal devnet RPC client (HttpURLConnection, no third-party HTTP lib).
 * Reads balances/transactions ONLY from the node — never from a local
 * database copy. Signing is deliberately NOT here: the release wallet
 * must sign with a platform key (see WalletStore) and only then POST.
 * Base URL is devnet-only by default and must be changed explicitly for
 * any other network; the UI always shows the network label from /chain/info.
 */
data class AccountState(val address: String, val balanceKen: Double, val nonce: Long, val network: String)
data class ChainInfo(val height: Long, val supplyKen: Double, val network: String)

class NodeApi(private val baseUrl: String = "http://10.0.2.2:8545") {
    private fun get(path: String): JSONObject {
        val c = URL(baseUrl + path).openConnection() as HttpURLConnection
        c.connectTimeout = 8000; c.readTimeout = 8000
        // Check the status BEFORE reading: on 4xx/5xx inputStream throws,
        // which previously masked the node error as a generic failure.
        val code = c.responseCode
        val stream = if (code in 200..299) c.inputStream else c.errorStream
        val text = stream.bufferedReader().readText()
        if (code !in 200..299) throw IllegalStateException("node error ($code): $text")
        return JSONObject(text)
    }
    fun chainInfo(): ChainInfo { val j = get("/chain/info"); return ChainInfo(j.getLong("height"), j.getDouble("total_supply_ken"), j.optString("network", "devnet-test-only")) }
    fun account(address: String): AccountState { val j = get("/accounts/$address"); return AccountState(j.getString("address"), j.getDouble("balance_ken"), j.getLong("nonce"), j.optString("network", "devnet-test-only")) }
    fun submitTx(txJson: String): JSONObject {
        val c = URL(baseUrl + "/tx/submit").openConnection() as HttpURLConnection
        c.requestMethod = "POST"; c.setRequestProperty("Content-Type", "application/json"); c.doOutput = true
        c.outputStream.bufferedWriter().use { it.write(txJson) }
        val text = (if (c.responseCode in 200..299) c.inputStream else c.errorStream).bufferedReader().readText()
        if (c.responseCode !in 200..299) throw IllegalStateException("submit failed: $text")
        return JSONObject(text)
    }
}

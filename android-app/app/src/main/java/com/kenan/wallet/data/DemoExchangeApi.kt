package com.kenan.wallet.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.net.URLEncoder
import java.nio.charset.StandardCharsets

/** A visible order-book level returned by the demo exchange. */
data class DemoBookEntry(val id: Long, val price: Double?, val quantity: Double)

data class DemoTrade(
    val pair: String,
    val price: Double,
    val quantity: Double,
    val demoOnly: Boolean
)

data class DemoOrder(
    val id: Long,
    val user: String,
    val side: String,
    val pair: String,
    val kind: String,
    val price: Double?,
    val quantity: Double,
    val filled: Double,
    val status: String,
    val demoOnly: Boolean
)

data class DemoBalance(val asset: String, val available: Double, val reserved: Double)

data class DemoMarketSnapshot(
    val pair: String,
    val bids: List<DemoBookEntry>,
    val asks: List<DemoBookEntry>,
    val trades: List<DemoTrade>,
    val orders: List<DemoOrder>,
    val balances: List<DemoBalance>,
    val demoOnly: Boolean
)

data class DemoOrderSubmission(val id: Long, val status: String, val filled: Double, val demoOnly: Boolean)
data class DemoOrderCancellation(val id: Long, val status: String, val demoOnly: Boolean)

class DemoExchangeException(val statusCode: Int?) :
    IllegalStateException("تعذر إكمال طلب التداول التجريبي")

/** Explicit-address HTTP client for the in-memory DEMO exchange; it never handles keys or signing. */
object DemoExchangeApi {
    private const val CONNECT_TIMEOUT_MS = 5_000
    private const val READ_TIMEOUT_MS = 8_000

    suspend fun marketSnapshot(baseUrl: String, user: String, pair: String): DemoMarketSnapshot =
        withContext(Dispatchers.IO) {
            require(user.isNotBlank())
            requireValidPair(pair)
            val book = request(baseUrl, "GET", "/exchange/book?pair=${encode(pair)}")
            val tradeResponse = request(baseUrl, "GET", "/exchange/trades")
            val orderResponse = request(baseUrl, "GET", "/exchange/orders?user=${encode(user)}")
            val balanceResponse = request(baseUrl, "GET", "/exchange/balances/${encode(user)}")
            checkDemo(book)
            checkDemo(tradeResponse)
            checkDemo(orderResponse)
            checkDemo(balanceResponse)

            DemoMarketSnapshot(
                pair = book.getString("pair"),
                bids = parseBook(book.getJSONArray("bids")),
                asks = parseBook(book.getJSONArray("asks")),
                trades = parseTrades(tradeResponse.getJSONArray("trades")),
                orders = parseOrders(orderResponse.getJSONArray("orders")),
                balances = parseBalances(balanceResponse.getJSONObject("balances")),
                demoOnly = true
            )
        }

    suspend fun depositDemo(baseUrl: String, user: String, asset: String, amount: Double): List<DemoBalance> =
        withContext(Dispatchers.IO) {
            require(user.isNotBlank() && asset.isNotBlank())
            require(amount.isFinite() && amount > 0.0)
            val response = request(
                baseUrl,
                "POST",
                "/exchange/deposit-demo",
                JSONObject().put("user", user).put("asset", asset).put("amount", amount)
            )
            checkDemo(response)
            parseBalances(response.getJSONObject("balances"))
        }

    suspend fun placeOrder(
        baseUrl: String,
        user: String,
        side: String,
        pair: String,
        kind: String,
        qty: Double,
        price: Double? = null
    ): DemoOrderSubmission = withContext(Dispatchers.IO) {
        require(user.isNotBlank())
        require(side == "buy" || side == "sell")
        require(kind == "limit" || kind == "market")
        requireValidPair(pair)
        require(qty.isFinite() && qty > 0.0)
        require(price == null || (price.isFinite() && price > 0.0))
        val body = JSONObject()
            .put("user", user)
            .put("side", side)
            .put("pair", pair)
            .put("kind", kind)
            .put("qty", qty)
        if (price != null) body.put("price", price)
        val response = request(baseUrl, "POST", "/exchange/orders", body)
        checkDemo(response)
        DemoOrderSubmission(
            id = response.getLong("id"),
            status = response.getString("status"),
            filled = response.optDouble("filled", 0.0),
            demoOnly = true
        )
    }

    suspend fun cancelOrder(baseUrl: String, user: String, id: Long): DemoOrderCancellation =
        withContext(Dispatchers.IO) {
            require(user.isNotBlank())
            require(id > 0)
            val response = request(
                baseUrl,
                "POST",
                "/exchange/orders/$id/cancel",
                JSONObject().put("user", user)
            )
            checkDemo(response)
            DemoOrderCancellation(
                id = response.getLong("id"),
                status = response.getString("status"),
                demoOnly = true
            )
        }

    private fun request(baseUrl: String, method: String, path: String, body: JSONObject? = null): JSONObject {
        val connection = try {
            URL(normalizeBaseUrl(baseUrl) + path).openConnection() as HttpURLConnection
        } catch (_: Exception) {
            throw DemoExchangeException(null)
        }
        try {
            connection.requestMethod = method
            connection.connectTimeout = CONNECT_TIMEOUT_MS
            connection.readTimeout = READ_TIMEOUT_MS
            connection.setRequestProperty("Accept", "application/json")
            if (body != null) {
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.doOutput = true
                connection.outputStream.use { it.write(body.toString().toByteArray(StandardCharsets.UTF_8)) }
            }
            val code = connection.responseCode
            if (code !in 200..299) throw DemoExchangeException(code)
            val text = connection.inputStream.bufferedReader(StandardCharsets.UTF_8).use { it.readText() }
            return JSONObject(text)
        } catch (error: DemoExchangeException) {
            throw error
        } catch (_: Exception) {
            throw DemoExchangeException(null)
        } finally {
            connection.disconnect()
        }
    }

    private fun normalizeBaseUrl(baseUrl: String): String {
        val address = try {
            URI(baseUrl.trim().trimEnd('/'))
        } catch (_: Exception) {
            throw IllegalArgumentException("عنوان API غير صالح")
        }
        require((address.scheme == "http" || address.scheme == "https") &&
            !address.host.isNullOrBlank() && address.userInfo == null &&
            address.query == null && address.fragment == null) { "عنوان API غير صالح" }
        return address.toASCIIString().trimEnd('/')
    }

    private fun requireValidPair(pair: String) {
        val assets = pair.split('/')
        require(assets.size == 2 && assets.all { it.isNotBlank() } && assets[0] != assets[1])
    }

    private fun encode(value: String): String =
        URLEncoder.encode(value, StandardCharsets.UTF_8.name()).replace("+", "%20")

    private fun checkDemo(json: JSONObject) {
        if (!json.optBoolean("demo_only", false)) throw DemoExchangeException(null)
    }

    private fun parseBook(array: JSONArray): List<DemoBookEntry> = buildList(array.length()) {
        for (index in 0 until array.length()) {
            val item = array.getJSONObject(index)
            add(DemoBookEntry(item.getLong("id"), nullableDouble(item, "price"), item.getDouble("qty")))
        }
    }

    private fun parseTrades(array: JSONArray): List<DemoTrade> = buildList(array.length()) {
        for (index in 0 until array.length()) {
            val item = array.getJSONObject(index)
            add(DemoTrade(item.getString("pair"), item.getDouble("price"), item.getDouble("qty"), true))
        }
    }

    private fun parseOrders(array: JSONArray): List<DemoOrder> = buildList(array.length()) {
        for (index in 0 until array.length()) {
            val item = array.getJSONObject(index)
            add(
                DemoOrder(
                    id = item.getLong("id"),
                    user = item.getString("user"),
                    side = item.getString("side"),
                    pair = item.getString("pair"),
                    kind = item.getString("kind"),
                    price = nullableDouble(item, "price"),
                    quantity = item.getDouble("qty"),
                    filled = item.getDouble("filled"),
                    status = item.getString("status"),
                    demoOnly = true
                )
            )
        }
    }

    private fun parseBalances(objectValue: JSONObject): List<DemoBalance> = buildList(objectValue.length()) {
        val assets = objectValue.keys()
        while (assets.hasNext()) {
            val asset = assets.next()
            val balance = objectValue.getJSONObject(asset)
            add(DemoBalance(asset, balance.getDouble("free"), balance.getDouble("reserved")))
        }
    }

    private fun nullableDouble(json: JSONObject, key: String): Double? =
        if (json.isNull(key)) null else json.getDouble(key)
}

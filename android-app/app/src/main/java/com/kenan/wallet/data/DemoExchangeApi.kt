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
import java.util.UUID

/** A submitted DEMO limit order from the locally configured backend. */
data class DemoBookEntry(
    val id: Long,
    val priceNum: Long,
    val priceDen: Long,
    val qtyUnits: Long,
    val demoOnly: Boolean
)

data class DemoTrade(
    val pair: String,
    val qtyUnits: Long,
    val quoteUnits: Long,
    val priceNum: Long,
    val priceDen: Long,
    val feeUnits: Long,
    val demoOnly: Boolean
) {
    /** Display-only projection for the sparkline; no order math uses a floating point value. */
    val chartPrice: Double get() = priceNum.toDouble() / priceDen.toDouble()
}

data class DemoOrder(
    val id: Long,
    val user: String,
    val side: String,
    val pair: String,
    val kind: String,
    val priceNum: Long?,
    val priceDen: Long?,
    val qtyUnits: Long,
    val filledUnits: Long,
    val status: String,
    val demoOnly: Boolean
)

data class DemoBalance(
    val asset: String,
    val availableUnits: Long,
    val reservedUnits: Long,
    val demoOnly: Boolean
)

data class DemoMarketSnapshot(
    val pair: String,
    val bids: List<DemoBookEntry>,
    val asks: List<DemoBookEntry>,
    val trades: List<DemoTrade>,
    val orders: List<DemoOrder>,
    val balances: List<DemoBalance>,
    val demoOnly: Boolean
)

data class DemoOrderSubmission(val id: Long, val status: String, val filledUnits: Long, val demoOnly: Boolean)
data class DemoOrderCancellation(val id: Long, val status: String, val demoOnly: Boolean)

class DemoExchangeException(val statusCode: Int?) :
    IllegalStateException("تعذر إكمال طلب التداول التجريبي")

/**
 * HTTP client for the local DEMO exchange only. It has no funding/deposit
 * method, handles no wallet keys, and sends integer smallest ledger units.
 */
object DemoExchangeApi {
    private const val CONNECT_TIMEOUT_MS = 5_000
    private const val READ_TIMEOUT_MS = 8_000
    private const val DEMO_PAIR = "DEMO_BASE/DEMO_QUOTE"

    suspend fun marketSnapshot(baseUrl: String, user: String, pair: String): DemoMarketSnapshot =
        withContext(Dispatchers.IO) {
            require(user.isNotBlank())
            requireValidPair(pair)
            val book = request(baseUrl, "GET", "/exchange/book?pair=${encode(pair)}")
            val tradeResponse = request(baseUrl, "GET", "/exchange/trades?user=${encode(user)}")
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

    suspend fun placeOrder(
        baseUrl: String,
        user: String,
        side: String,
        pair: String,
        kind: String,
        qtyUnits: Long,
        priceNum: Long? = null,
        priceDen: Long? = null,
        quoteBudgetUnits: Long? = null,
        idempotencyKey: String = UUID.randomUUID().toString()
    ): DemoOrderSubmission = withContext(Dispatchers.IO) {
        require(user.isNotBlank())
        require(side == "buy" || side == "sell")
        require(kind == "limit" || kind == "market")
        requireValidPair(pair)
        require(qtyUnits > 0)
        require(idempotencyKey.length in 8..128)
        if (kind == "limit") {
            require(priceNum != null && priceNum > 0 && priceDen != null && priceDen > 0)
            require(quoteBudgetUnits == null)
        } else {
            require(priceNum == null && priceDen == null)
            if (side == "buy") require(quoteBudgetUnits != null && quoteBudgetUnits > 0)
            else require(quoteBudgetUnits == null)
        }

        val body = JSONObject()
            .put("user", user)
            .put("side", side)
            .put("pair", pair)
            .put("kind", kind)
            .put("qty_units", qtyUnits)
        if (priceNum != null) body.put("price_num", priceNum)
        if (priceDen != null) body.put("price_den", priceDen)
        if (quoteBudgetUnits != null) body.put("quote_budget_units", quoteBudgetUnits)
        val response = request(
            baseUrl,
            "POST",
            "/exchange/orders",
            body,
            mapOf("Idempotency-Key" to idempotencyKey)
        )
        checkDemo(response)
        val order = response.getJSONObject("order")
        checkDemo(order)
        DemoOrderSubmission(
            id = order.getLong("id"),
            status = order.getString("status"),
            filledUnits = order.getLong("filled_units"),
            demoOnly = true
        )
    }

    suspend fun cancelOrder(
        baseUrl: String,
        user: String,
        id: Long,
        idempotencyKey: String = UUID.randomUUID().toString()
    ): DemoOrderCancellation = withContext(Dispatchers.IO) {
        require(user.isNotBlank())
        require(id > 0)
        val response = request(
            baseUrl,
            "POST",
            "/exchange/orders/$id/cancel",
            JSONObject().put("user", user),
            mapOf("Idempotency-Key" to idempotencyKey)
        )
        checkDemo(response)
        val order = response.getJSONObject("order")
        checkDemo(order)
        DemoOrderCancellation(
            id = order.getLong("id"),
            status = order.getString("status"),
            demoOnly = true
        )
    }

    private fun request(
        baseUrl: String,
        method: String,
        path: String,
        body: JSONObject? = null,
        headers: Map<String, String> = emptyMap()
    ): JSONObject {
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
            headers.forEach { (name, value) -> connection.setRequestProperty(name, value) }
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
        require(pair == DEMO_PAIR) { "زوج DEMO غير متاح" }
    }

    private fun encode(value: String): String =
        URLEncoder.encode(value, StandardCharsets.UTF_8.name()).replace("+", "%20")

    private fun checkDemo(json: JSONObject) {
        if (json.optString("environment") != "DEMO" ||
            !json.optBoolean("demo_only", false) || !json.optBoolean("test_only", false)) {
            throw DemoExchangeException(null)
        }
    }

    private fun parseBook(array: JSONArray): List<DemoBookEntry> = buildList(array.length()) {
        for (index in 0 until array.length()) {
            val item = array.getJSONObject(index)
            checkDemo(item)
            val price = item.getJSONObject("price")
            add(DemoBookEntry(
                item.getLong("id"), price.getLong("num"), price.getLong("den"),
                item.getLong("qty_units"), true
            ))
        }
    }

    private fun parseTrades(array: JSONArray): List<DemoTrade> = buildList(array.length()) {
        for (index in 0 until array.length()) {
            val item = array.getJSONObject(index)
            checkDemo(item)
            val price = item.getJSONObject("price")
            add(DemoTrade(
                pair = item.getString("pair"),
                qtyUnits = item.getLong("qty_units"),
                quoteUnits = item.getLong("quote_units"),
                priceNum = price.getLong("num"),
                priceDen = price.getLong("den"),
                feeUnits = item.getLong("fee_units"),
                demoOnly = true
            ))
        }
    }

    private fun parseOrders(array: JSONArray): List<DemoOrder> = buildList(array.length()) {
        for (index in 0 until array.length()) {
            val item = array.getJSONObject(index)
            checkDemo(item)
            val price = if (item.isNull("limit_price")) null else item.getJSONObject("limit_price")
            add(DemoOrder(
                id = item.getLong("id"),
                user = item.getString("user"),
                side = item.getString("side"),
                pair = item.getString("pair"),
                kind = item.getString("kind"),
                priceNum = price?.getLong("num"),
                priceDen = price?.getLong("den"),
                qtyUnits = item.getLong("qty_units"),
                filledUnits = item.getLong("filled_units"),
                status = item.getString("status"),
                demoOnly = true
            ))
        }
    }

    private fun parseBalances(objectValue: JSONObject): List<DemoBalance> = buildList(objectValue.length()) {
        val assets = objectValue.keys()
        while (assets.hasNext()) {
            val asset = assets.next()
            val balance = objectValue.getJSONObject(asset)
            checkDemo(balance)
            add(DemoBalance(
                asset,
                balance.getLong("available_units"),
                balance.getLong("reserved_units"),
                true
            ))
        }
    }
}

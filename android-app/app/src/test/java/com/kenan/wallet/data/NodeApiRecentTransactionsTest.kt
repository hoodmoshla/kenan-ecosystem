package com.kenan.wallet.data

import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.nio.charset.StandardCharsets
import kotlin.concurrent.thread

class NodeApiRecentTransactionsTest {
    private val wallet = "kenan1e28c3608979d45d2c7dc74b1c19519e54ce8961c"
    private val recipient = "kenan1" + "a".repeat(40)
    private val unrelated = "kenan1" + "b".repeat(40)

    @Test
    fun recentTransactionsReturnsOnlyConfirmedWalletTransfersNewestFirst() {
        val body = JSONArray()
            .put(
                JSONObject().put("height", 1).put(
                    "txs", JSONArray()
                        .put(tx("1".repeat(64), wallet, recipient, 50, "confirmed"))
                        .put(tx("2".repeat(64), unrelated, recipient, 99, "pending"))
                        .put(tx("3".repeat(64), unrelated, recipient, 99, "confirmed"))
                )
            )
            .put(
                JSONObject().put("height", 2).put(
                    "txs", JSONArray().put(tx("4".repeat(64), unrelated, wallet, 75, "confirmed"))
                )
            )

        withServer(200, body.toString()) { endpoint ->
            val result = runBlocking {
                NodeApi(buildDefault = endpoint, isEmulator = false).recentTransactions(wallet, limit = 20)
            }
            assertEquals(2, result.size)
            assertEquals("4".repeat(64), result[0].hash)
            assertEquals(2L, result[0].blockHeight)
            assertEquals(75L, result[0].amountBase)
            assertEquals("1".repeat(64), result[1].hash)
            assertEquals(1L, result[1].blockHeight)
            assertEquals(50L, result[1].amountBase)
        }
    }

    @Test
    fun recentTransactionsSurfacesHttpFailuresForLocalizedUiHandling() {
        withServer(503, "maintenance") { endpoint ->
            val error = try {
                runBlocking {
                    NodeApi(buildDefault = endpoint, isEmulator = false).recentTransactions(wallet)
                }
                null
            } catch (expected: NodeHttpException) {
                expected
            }
            assertNotNull(error)
            assertEquals(503, error?.statusCode)
        }
    }

    private fun tx(hash: String, from: String, to: String, amount: Long, status: String) =
        JSONObject()
            .put("hash", hash)
            .put("from", from)
            .put("to", to)
            .put("amount", amount)
            .put("status", status)

    private fun withServer(status: Int, response: String, block: (String) -> Unit) {
        val server = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))
        val worker = thread(isDaemon = true, name = "node-api-test-server") {
            try {
                server.accept().use { socket ->
                    val reader = BufferedReader(InputStreamReader(socket.getInputStream(), StandardCharsets.US_ASCII))
                    reader.readLine()
                    while (reader.readLine()?.isNotEmpty() == true) { }
                    val payload = response.toByteArray(StandardCharsets.UTF_8)
                    val phrase = if (status == 200) "OK" else "Service Unavailable"
                    val headers = "HTTP/1.1 $status $phrase\r\n" +
                        "Content-Type: application/json; charset=utf-8\r\n" +
                        "Content-Length: ${payload.size}\r\n" +
                        "Connection: close\r\n\r\n"
                    socket.getOutputStream().use { output ->
                        output.write(headers.toByteArray(StandardCharsets.US_ASCII))
                        output.write(payload)
                        output.flush()
                    }
                }
            } catch (_: Exception) {
                // The test closes the listening socket if the request fails early.
            }
        }
        try {
            block("http://127.0.0.1:${server.localPort}")
        } finally {
            server.close()
            worker.join(1_000)
        }
    }
}

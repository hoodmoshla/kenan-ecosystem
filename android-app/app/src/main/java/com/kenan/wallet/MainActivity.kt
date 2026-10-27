package com.kenan.wallet

import android.app.Application
import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter
import com.kenan.wallet.data.*
import io.novasama.substrate_sdk_android.encrypt.SignatureWrapper
import io.novasama.substrate_sdk_android.runtime.extrinsic.v5.transactionExtension.InheritedImplication
import io.novasama.substrate_sdk_android.runtime.extrinsic.v5.transactionExtension.extensions.verifySignature.GeneralTransactionSigner
import io.novasama.substrate_sdk_android.runtime.extrinsic.v5.transactionExtension.signingPayload
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.Locale

private val PageBg = Color(0xFFF3F6FB)
private val Ink = Color(0xFF14243A)
private val Muted = Color(0xFF718096)
private val Emerald = Color(0xFF11856B)
private val Mint = Color(0xFF3BD5A5)
private val Navy = Color(0xFF13243B)
private val Gold = Color(0xFFC19A4A)
private val Border = Color(0xFFE4EAF2)
private val Positive = Color(0xFF19856B)
private val Negative = Color(0xFFCC5A63)
private val SoftGreen = Color(0xFFE8F6F1)
private val SoftRed = Color(0xFFFFEFF0)

sealed class UiState<out T> {
    data object Loading : UiState<Nothing>()
    data class Ok<T>(val value: T) : UiState<T>()
    data class Err(val message: String) : UiState<Nothing>()
}

data class TransferQuote(
    val recipient: String,
    val amountBase: Long,
    val feeBase: Long,
    val nonce: Long,
    val balanceBase: Long
)

private enum class AppTab { Home, Markets, Trade, Assets, Settings }

class WalletViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application
    private val store = EncryptedWalletStore(application)
    private val settings = application.getSharedPreferences("kenan-wallet-settings", Context.MODE_PRIVATE)
    private val emulator = isAndroidEmulator()

    var wallet by mutableStateOf<EncryptedWalletStore.PublicWallet?>(null)
        private set
    var ready by mutableStateOf(false)
        private set
    var busy by mutableStateOf(false)
        private set
    var setupError by mutableStateOf<String?>(null)
        private set
    var account: UiState<AccountState> by mutableStateOf(UiState.Loading)
        private set
    var chain: UiState<ChainInfo> by mutableStateOf(UiState.Loading)
        private set
    var recentTransactions by mutableStateOf<List<ChainTransaction>>(emptyList())
        private set
    var historyUnavailable by mutableStateOf(false)
        private set
    var quote by mutableStateOf<TransferQuote?>(null)
        private set
    var polkadotQuote by mutableStateOf<PolkadotTransferQuote?>(null)
        private set
    var selectedNetwork by mutableStateOf(
        settings.getString("selected_network", NETWORK_PYTHON)
            ?.takeIf { it == NETWORK_PYTHON || it == NETWORK_POLKADOT } ?: NETWORK_PYTHON
    )
        private set
    var substrateRpcUrl by mutableStateOf(
        SubstrateRpcEndpointConfig.resolve("", settings.getString("substrate_rpc_url", null), emulator).orEmpty()
    )
        private set
    var polkadotIdentity by mutableStateOf<PolkadotNetworkIdentity?>(null)
        private set
    var polkadotChainPin by mutableStateOf(PolkadotChainPin.fromJson(settings.getString("polkadot_chain_pin", null)))
        private set
    var polkadotPending by mutableStateOf(PolkadotPendingTransfer.fromJson(settings.getString("polkadot_pending_transfer", null)))
        private set
    var sendError by mutableStateOf<String?>(null)
        private set
    var sendResult by mutableStateOf<String?>(null)
        private set
    var rpcUrl by mutableStateOf(
        RpcEndpointConfig.resolve(
            BuildConfig.KENAN_NODE_URL,
            settings.getString("rpc_url", null),
            emulator
        ).orEmpty()
    )
        private set
    var backendUrl by mutableStateOf(
        settings.getString("backend_url", null)
            ?: RpcEndpointConfig.deriveBackend(rpcUrl).orEmpty()
    )
        private set
    var settingsError by mutableStateOf<String?>(null)
        private set
    var settingsMessage by mutableStateOf<String?>(null)
        private set
    var balanceVisible by mutableStateOf(settings.getBoolean("balance_visible", true))
        private set
    val isPolkadotSelected: Boolean get() = selectedNetwork == NETWORK_POLKADOT
    val isPolkadotTrusted: Boolean get() = polkadotIdentity?.let { polkadotChainPin?.matches(it) } == true
    val currentAddress: String get() = wallet?.let { if (isPolkadotSelected) it.substrateAddress else it.address }.orEmpty()
    val currentRpcEndpoint: String get() = if (isPolkadotSelected) substrateRpcUrl else rpcUrl
    val currentTokenSymbol: String get() = if (isPolkadotSelected) polkadotIdentity?.tokenSymbol ?: "UNIT" else "KEN"
    val currentTokenDecimals: Int get() = if (isPolkadotSelected) polkadotIdentity?.tokenDecimals ?: 12 else KEN_DECIMALS
    var demoSnapshot by mutableStateOf<DemoMarketSnapshot?>(null)
        private set
    var demoLoading by mutableStateOf(false)
        private set
    var demoError by mutableStateOf<String?>(null)
        private set
    var demoMessage by mutableStateOf<String?>(null)
        private set

    init {
        viewModelScope.launch {
            try {
                wallet = withContext(Dispatchers.IO) { store.loadWallet() }
            } catch (_: Exception) {
                setupError = app.getString(R.string.setup_error)
            } finally {
                ready = true
            }
            if (wallet != null) refresh()
        }
    }

    fun toggleBalanceVisibility() {
        balanceVisible = !balanceVisible
        settings.edit().putBoolean("balance_visible", balanceVisible).apply()
    }

    fun formatCurrentAmount(baseUnits: Long): String = WalletCrypto.formatAmount(baseUnits, currentTokenDecimals)

    fun saveMnemonic(phrase: String, allowExistingAddressMatch: Boolean = false) {
        if (busy) return
        busy = true
        setupError = null
        viewModelScope.launch {
            try {
                wallet = withContext(Dispatchers.IO) { store.saveMnemonic(phrase, allowExistingAddressMatch) }
                setupError = null
            } catch (_: Exception) {
                setupError = app.getString(R.string.setup_error)
            } finally {
                ready = true
                busy = false
            }
            if (wallet != null) refresh()
        }
    }

    fun saveEndpoints(rpcInput: String, backendInput: String) {
        settingsError = null
        settingsMessage = null
        try {
            val normalizedRpc = if (rpcInput.isBlank()) "" else RpcEndpointConfig.validate(rpcInput, emulator)
            val normalizedBackend = if (backendInput.isBlank()) {
                RpcEndpointConfig.deriveBackend(normalizedRpc).orEmpty()
            } else {
                RpcEndpointConfig.validate(backendInput, emulator)
            }
            rpcUrl = normalizedRpc
            backendUrl = normalizedBackend
            settings.edit()
                .putString("rpc_url", normalizedRpc)
                .putString("backend_url", normalizedBackend)
                .apply()
            settingsMessage = app.getString(R.string.settings_saved)
            refresh()
        } catch (_: IllegalArgumentException) {
            val isPhysicalEmulatorAlias = !emulator &&
                (rpcInput.contains("10.0.2.2") || backendInput.contains("10.0.2.2"))
            settingsError = app.getString(
                if (isPhysicalEmulatorAlias) R.string.physical_emulator_alias else R.string.url_invalid
            )
        }
    }

    fun selectNetwork(network: String) {
        if (network != NETWORK_PYTHON && network != NETWORK_POLKADOT) return
        if (selectedNetwork == network) return
        selectedNetwork = network
        settings.edit().putString("selected_network", network).apply()
        quote = null
        polkadotQuote = null
        sendError = null
        sendResult = null
        refresh()
    }

    fun saveSubstrateRpc(input: String) {
        settingsError = null
        settingsMessage = null
        try {
            val normalized = if (input.isBlank()) "" else SubstrateRpcEndpointConfig.validate(input, emulator)
            if (normalized != substrateRpcUrl) polkadotIdentity = null
            substrateRpcUrl = normalized
            settings.edit().putString("substrate_rpc_url", normalized).apply()
            settingsMessage = app.getString(R.string.settings_saved)
            refresh()
        } catch (_: IllegalArgumentException) {
            settingsError = app.getString(R.string.substrate_rpc_invalid)
        }
    }

    fun inspectCurrentPolkadotNetwork() {
        if (substrateRpcUrl.isBlank()) {
            settingsError = app.getString(R.string.rpc_unconfigured)
            return
        }
        if (busy) return
        busy = true
        settingsError = null
        settingsMessage = null
        viewModelScope.launch {
            try {
                val identity = SubstrateNodeApi(substrateRpcUrl).inspectIdentity()
                polkadotIdentity = identity
                settingsMessage = app.getString(R.string.polkadot_identity_reviewed)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                settingsError = friendlyRpcError(error)
            } finally {
                busy = false
            }
        }
    }

    fun trustCurrentPolkadotNetwork() {
        val reviewedIdentity = polkadotIdentity
        if (substrateRpcUrl.isBlank()) {
            settingsError = app.getString(R.string.rpc_unconfigured)
            return
        }
        if (reviewedIdentity == null) {
            settingsError = app.getString(R.string.polkadot_review_first)
            return
        }
        if (busy) return
        busy = true
        settingsError = null
        settingsMessage = null
        viewModelScope.launch {
            try {
                val freshIdentity = SubstrateNodeApi(substrateRpcUrl).inspectIdentity()
                if (!reviewedIdentity.samePinnedIdentity(freshIdentity)) {
                    polkadotIdentity = freshIdentity
                    settingsError = app.getString(R.string.polkadot_identity_changed)
                    return@launch
                }
                val pin = PolkadotChainPin.from(freshIdentity)
                check(settings.edit().putString("polkadot_chain_pin", pin.toJson()).commit()) {
                    "Could not save the trusted chain identity"
                }
                polkadotIdentity = freshIdentity
                polkadotChainPin = pin
                settingsMessage = app.getString(R.string.polkadot_network_trusted)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                settingsError = friendlyRpcError(error)
            } finally {
                busy = false
            }
            refresh()
        }
    }

    fun refresh() {
        val current = wallet ?: return
        if (isPolkadotSelected) {
            refreshPolkadot(current)
            return
        }
        val endpoint = rpcUrl
        if (endpoint.isBlank()) {
            account = UiState.Err(app.getString(R.string.rpc_unconfigured))
            chain = UiState.Err(app.getString(R.string.rpc_unconfigured))
            recentTransactions = emptyList()
            historyUnavailable = false
            return
        }
        account = UiState.Loading
        chain = UiState.Loading
        recentTransactions = emptyList()
        historyUnavailable = false
        viewModelScope.launch {
            try {
                val api = NodeApi(endpoint)
                val info = api.chainInfo()
                verifyDevnet(info)
                val state = api.account(current.address)
                require(state.network == "devnet-test-only") { app.getString(R.string.error_wrong_network) }
                account = UiState.Ok(state)
                chain = UiState.Ok(info)
                try {
                    recentTransactions = api.recentTransactions(current.address, 20)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: Exception) {
                    recentTransactions = emptyList()
                    historyUnavailable = true
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (e: Exception) {
                val message = friendlyRpcError(e)
                account = UiState.Err(message)
                chain = UiState.Err(message)
                recentTransactions = emptyList()
                historyUnavailable = false
            }
        }
    }

    private fun refreshPolkadot(current: EncryptedWalletStore.PublicWallet) {
        val endpoint = substrateRpcUrl
        if (endpoint.isBlank()) {
            account = UiState.Err(app.getString(R.string.rpc_unconfigured))
            chain = UiState.Err(app.getString(R.string.rpc_unconfigured))
            recentTransactions = emptyList()
            historyUnavailable = false
            return
        }
        account = UiState.Loading
        chain = UiState.Loading
        recentTransactions = emptyList()
        historyUnavailable = false
        viewModelScope.launch {
            try {
                val api = SubstrateNodeApi(endpoint)
                val identity = api.inspectIdentity()
                polkadotIdentity = identity
                val pendingJson = settings.getString("polkadot_pending_transfer", null)
                val pending = PolkadotPendingTransfer.fromJson(pendingJson)
                polkadotPending = pending
                if (pendingJson != null && pending == null) {
                    sendError = app.getString(R.string.polkadot_pending_corrupt)
                }
                if (pending != null) {
                    when (val resolution = api.resolvePending(pending)) {
                        is PolkadotPendingResolution.Finalized -> {
                            persistPolkadotPending(null)
                            sendResult = app.getString(
                                R.string.polkadot_transfer_confirmed,
                                resolution.transaction.hash,
                                resolution.transaction.blockHeight
                            )
                        }
                        PolkadotPendingResolution.Rejected -> {
                            persistPolkadotPending(null)
                            sendError = app.getString(R.string.polkadot_transfer_rejected)
                        }
                        PolkadotPendingResolution.ExpiredNotIncluded -> {
                            persistPolkadotPending(null)
                            sendError = app.getString(R.string.polkadot_transfer_expired)
                        }
                        PolkadotPendingResolution.StillPending -> {
                            sendError = app.getString(R.string.polkadot_transfer_still_pending, pending.hash)
                        }
                        PolkadotPendingResolution.Unknown -> {
                            sendError = app.getString(R.string.polkadot_transfer_unknown, pending.hash)
                        }
                    }
                }
                val accountId = SubstrateAddressCodec.accountIdFromPublicKeyHex(current.publicKeyHex)
                val state = api.account(accountId)
                account = UiState.Ok(
                    AccountState(current.substrateAddress, state.freeBalanceBase, state.nonce, identity.chainName)
                )
                chain = UiState.Ok(
                    ChainInfo(identity.genesisHash.take(14), identity.finalizedHeight, 0L, 0L, identity.chainName)
                )
                try {
                    recentTransactions = api.recentTransactions(accountId, 20).map { tx ->
                        ChainTransaction(tx.hash, "finalized", tx.blockHeight, tx.from, tx.to, tx.amountBase)
                    }
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: Exception) {
                    recentTransactions = emptyList()
                    historyUnavailable = true
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                val message = friendlyRpcError(error)
                account = UiState.Err(message)
                chain = UiState.Err(message)
                recentTransactions = emptyList()
                historyUnavailable = false
            }
        }
    }

    private fun persistPolkadotPending(pending: PolkadotPendingTransfer?) {
        if (pending == null) {
            settings.edit().remove("polkadot_pending_transfer").apply()
        } else {
            settings.edit().putString("polkadot_pending_transfer", pending.toJson()).apply()
        }
        polkadotPending = pending
    }

    fun refreshDemoExchange() {
        val current = wallet ?: return
        if (backendUrl.isBlank()) {
            demoSnapshot = null
            demoError = app.getString(R.string.demo_backend_unavailable)
            return
        }
        demoLoading = true
        demoError = null
        demoMessage = null
        viewModelScope.launch {
            try {
                demoSnapshot = DemoExchangeApi.marketSnapshot(backendUrl, current.address, DEMO_PAIR)
            } catch (_: Exception) {
                demoSnapshot = null
                demoError = app.getString(R.string.demo_backend_unavailable)
            } finally {
                demoLoading = false
            }
        }
    }

    fun addDemoCredits(asset: String) {
        val current = wallet ?: return
        if (backendUrl.isBlank() || demoLoading) {
            demoError = app.getString(R.string.demo_backend_unavailable)
            return
        }
        demoLoading = true
        demoError = null
        demoMessage = null
        val amount = if (asset == "KEN") 100.0 else 1000.0
        viewModelScope.launch {
            try {
                DemoExchangeApi.depositDemo(backendUrl, current.address, asset, amount)
                demoMessage = app.getString(R.string.demo_action_complete)
                demoSnapshot = DemoExchangeApi.marketSnapshot(backendUrl, current.address, DEMO_PAIR)
            } catch (_: Exception) {
                demoError = app.getString(R.string.demo_backend_unavailable)
            } finally {
                demoLoading = false
            }
        }
    }

    fun placeDemoOrder(side: String, kind: String, quantity: Double, price: Double?) {
        val current = wallet ?: return
        if (demoLoading) return
        if (backendUrl.isBlank()) {
            demoError = app.getString(R.string.demo_backend_unavailable)
            return
        }
        demoLoading = true
        demoError = null
        demoMessage = null
        viewModelScope.launch {
            try {
                DemoExchangeApi.placeOrder(backendUrl, current.address, side, DEMO_PAIR, kind, quantity, price)
                demoMessage = app.getString(R.string.demo_action_complete)
                demoSnapshot = DemoExchangeApi.marketSnapshot(backendUrl, current.address, DEMO_PAIR)
            } catch (_: Exception) {
                demoError = app.getString(R.string.demo_order_failed)
            } finally {
                demoLoading = false
            }
        }
    }

    fun cancelDemoOrder(orderId: Long) {
        val current = wallet ?: return
        if (demoLoading) return
        demoLoading = true
        demoError = null
        demoMessage = null
        viewModelScope.launch {
            try {
                DemoExchangeApi.cancelOrder(backendUrl, current.address, orderId)
                demoMessage = app.getString(R.string.demo_action_complete)
                demoSnapshot = DemoExchangeApi.marketSnapshot(backendUrl, current.address, DEMO_PAIR)
            } catch (_: Exception) {
                demoError = app.getString(R.string.demo_order_failed)
            } finally {
                demoLoading = false
            }
        }
    }

    fun prepareTransfer(recipientInput: String, amountInput: String) {
        if (isPolkadotSelected) {
            preparePolkadotTransfer(recipientInput, amountInput)
            return
        }
        val current = wallet ?: run { sendError = app.getString(R.string.setup_title); return }
        val recipient = recipientInput.trim()
        if (!WalletCrypto.isValidAddress(recipient)) {
            sendError = app.getString(R.string.error_invalid_address)
            return
        }
        val amount = try {
            WalletCrypto.parseKenAmount(amountInput)
        } catch (_: Exception) {
            sendError = app.getString(R.string.error_invalid_amount)
            return
        }
        if (recipient == current.address) {
            sendError = app.getString(R.string.error_same_address)
            return
        }
        if (rpcUrl.isBlank()) {
            sendError = app.getString(R.string.rpc_unconfigured)
            return
        }
        sendError = null
        sendResult = null
        quote = null
        busy = true
        viewModelScope.launch {
            try {
                val api = NodeApi(rpcUrl)
                val info = api.chainInfo()
                verifyDevnet(info)
                require(info.feeBase == DEVNET_FEE_BASE) { app.getString(R.string.error_fee_changed) }
                val state = api.account(current.address)
                require(state.network == "devnet-test-only") { app.getString(R.string.error_wrong_network) }
                require(amount <= Long.MAX_VALUE - info.feeBase && state.balanceBase >= amount + info.feeBase) {
                    app.getString(R.string.error_insufficient_balance)
                }
                quote = TransferQuote(recipient, amount, info.feeBase, state.nonce, state.balanceBase)
                chain = UiState.Ok(info)
                account = UiState.Ok(state)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (e: Exception) {
                sendError = when (e) {
                    is NodeHttpException -> app.getString(R.string.error_request_rejected, e.statusCode)
                    is IllegalArgumentException -> e.message ?: app.getString(R.string.error_transfer_changed)
                    else -> friendlyRpcError(e)
                }
            } finally {
                busy = false
            }
        }
    }

    private fun preparePolkadotTransfer(recipientInput: String, amountInput: String) {
        val current = wallet ?: run { sendError = app.getString(R.string.setup_title); return }
        val pendingJson = settings.getString("polkadot_pending_transfer", null)
        val pending = PolkadotPendingTransfer.fromJson(pendingJson)
        if (pendingJson != null && pending == null) {
            sendError = app.getString(R.string.polkadot_pending_corrupt)
            return
        }
        if (pending != null) {
            polkadotPending = pending
            sendError = app.getString(R.string.polkadot_transfer_unknown, pending.hash)
            return
        }
        val identity = polkadotIdentity
        if (identity == null || polkadotChainPin?.matches(identity) != true) {
            sendError = app.getString(R.string.polkadot_trust_required)
            return
        }
        val recipient = recipientInput.trim()
        val recipientId = try {
            SubstrateAddressCodec.accountId(recipient)
        } catch (_: Exception) {
            sendError = app.getString(R.string.polkadot_invalid_address)
            return
        }
        val senderId = SubstrateAddressCodec.accountIdFromPublicKeyHex(current.publicKeyHex)
        if (recipientId.contentEquals(senderId)) {
            sendError = app.getString(R.string.error_same_address)
            return
        }
        val amount = try {
            WalletCrypto.parseAmount(amountInput, identity.tokenDecimals)
        } catch (_: Exception) {
            sendError = app.getString(R.string.polkadot_invalid_amount, identity.tokenDecimals)
            return
        }
        if (substrateRpcUrl.isBlank()) {
            sendError = app.getString(R.string.rpc_unconfigured)
            return
        }
        sendError = null
        sendResult = null
        quote = null
        polkadotQuote = null
        busy = true
        viewModelScope.launch {
            try {
                val reviewed = SubstrateNodeApi(substrateRpcUrl).quoteTransfer(
                    senderId,
                    recipientId,
                    recipient,
                    amount
                )
                require(polkadotChainPin?.matches(reviewed.identity) == true) {
                    app.getString(R.string.polkadot_identity_changed)
                }
                require(reviewed.amountBase <= Long.MAX_VALUE - reviewed.feeBase &&
                    reviewed.balanceBase >= reviewed.amountBase + reviewed.feeBase) {
                    app.getString(R.string.error_insufficient_balance)
                }
                polkadotIdentity = reviewed.identity
                polkadotQuote = reviewed
                account = UiState.Ok(
                    AccountState(current.substrateAddress, reviewed.balanceBase, reviewed.nonce, reviewed.identity.chainName)
                )
                chain = UiState.Ok(
                    ChainInfo(reviewed.identity.genesisHash.take(14), reviewed.identity.finalizedHeight, 0L, 0L, reviewed.identity.chainName)
                )
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                sendError = if (error is IllegalArgumentException) friendlyPolkadotTransferError(error)
                else friendlyRpcError(error)
            } finally {
                busy = false
            }
        }
    }

    fun cancelQuote() {
        quote = null
        polkadotQuote = null
    }

    fun confirmTransfer() {
        if (isPolkadotSelected) {
            confirmPolkadotTransfer()
            return
        }
        val current = wallet ?: return
        val reviewed = quote ?: return
        if (busy || rpcUrl.isBlank()) return
        busy = true
        sendError = null
        sendResult = null
        viewModelScope.launch {
            try {
                val api = NodeApi(rpcUrl)
                val info = api.chainInfo()
                verifyDevnet(info)
                require(info.feeBase == reviewed.feeBase) { app.getString(R.string.error_fee_changed) }
                val accountNow = api.account(current.address)
                require(accountNow.nonce == reviewed.nonce) { app.getString(R.string.error_nonce_changed) }
                require(accountNow.balanceBase == reviewed.balanceBase &&
                    accountNow.balanceBase >= reviewed.amountBase + reviewed.feeBase) {
                    app.getString(R.string.error_balance_changed)
                }
                val tx = withContext(Dispatchers.IO) {
                    store.signTransfer(reviewed.recipient, reviewed.amountBase, reviewed.feeBase, reviewed.nonce)
                }
                require(tx.from == current.address) { app.getString(R.string.error_signing) }
                val expectedHash = tx.transactionHash()
                try {
                    val submitted = api.submitTx(tx.toJson())
                    require(submitted.getString("hash") == expectedHash) { app.getString(R.string.error_tx_hash_mismatch) }
                } catch (submitError: Exception) {
                    if (submitError is CancellationException) throw submitError
                    if (submitError is NodeHttpException && submitError.statusCode in 400..499) throw submitError
                    val found = try {
                        api.transaction(expectedHash)
                    } catch (cancelled: CancellationException) {
                        throw cancelled
                    } catch (_: Exception) {
                        null
                    }
                    if (found == null || !matchesReviewedTransaction(found, tx)) {
                        throw AmbiguousTransferException(expectedHash, submitError)
                    }
                }
                val confirmed = api.transaction(expectedHash)
                require(matchesReviewedTransaction(confirmed, tx)) { app.getString(R.string.error_tx_mismatch) }
                sendResult = app.getString(
                    R.string.transfer_confirmed,
                    confirmed.getString("hash"),
                    confirmed.getLong("block_height")
                )
                quote = null
                refresh()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (e: Exception) {
                sendError = when (e) {
                    is AmbiguousTransferException -> app.getString(R.string.error_ambiguous_send, e.txHash)
                    is NodeHttpException -> app.getString(R.string.error_request_rejected, e.statusCode)
                    is IllegalArgumentException -> e.message ?: app.getString(R.string.error_transfer_changed)
                    else -> friendlyRpcError(e)
                }
            } finally {
                busy = false
            }
        }
    }

    private fun confirmPolkadotTransfer() {
        val current = wallet ?: return
        val reviewed = polkadotQuote ?: return
        if (busy || substrateRpcUrl.isBlank()) return
        if (polkadotChainPin?.matches(reviewed.identity) != true) {
            sendError = app.getString(R.string.polkadot_identity_changed)
            polkadotQuote = null
            return
        }
        val existingPendingJson = settings.getString("polkadot_pending_transfer", null)
        val existingPending = PolkadotPendingTransfer.fromJson(existingPendingJson)
        if (existingPendingJson != null && existingPending == null) {
            sendError = app.getString(R.string.polkadot_pending_corrupt)
            polkadotQuote = null
            return
        }
        if (existingPending != null) {
            polkadotPending = existingPending
            sendError = app.getString(R.string.polkadot_transfer_unknown, existingPending.hash)
            polkadotQuote = null
            refresh()
            return
        }
        busy = true
        sendError = null
        sendResult = null
        viewModelScope.launch {
            try {
                val senderId = SubstrateAddressCodec.accountIdFromPublicKeyHex(current.publicKeyHex)
                val recipientId = SubstrateAddressCodec.accountId(reviewed.recipient)
                val signer = object : GeneralTransactionSigner {
                    override suspend fun signInheritedImplication(
                        inheritedImplication: InheritedImplication,
                        accountId: ByteArray
                    ): SignatureWrapper {
                        val payload = inheritedImplication.signingPayload()
                        return try {
                            SignatureWrapper.Ed25519(store.signSubstratePayload(payload, accountId))
                        } finally {
                            payload.fill(0)
                        }
                    }
                }
                val transaction = SubstrateNodeApi(substrateRpcUrl).sendTransfer(
                    senderId,
                    recipientId,
                    reviewed,
                    signer
                ) { pending ->
                    check(settings.edit().putString("polkadot_pending_transfer", pending.toJson()).commit()) {
                        "Could not persist the signed transaction before submission"
                    }
                }
                persistPolkadotPending(null)
                polkadotQuote = null
                sendResult = app.getString(
                    R.string.polkadot_transfer_confirmed,
                    transaction.hash,
                    transaction.blockHeight
                )
                refresh()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                polkadotQuote = null
                if (error is SubstrateNodeApi.SubstrateNotSubmittedException ||
                    error is SubstrateNodeApi.SubstrateRejectedException
                ) {
                    settings.edit().remove("polkadot_pending_transfer").commit()
                    polkadotPending = null
                }
                val pending = PolkadotPendingTransfer.fromJson(settings.getString("polkadot_pending_transfer", null))
                polkadotPending = pending
                sendError = when {
                    error is SubstrateNodeApi.SubstrateAmbiguousSubmissionException ->
                        app.getString(R.string.polkadot_transfer_unknown, error.txHash)
                    error is SubstrateNodeApi.SubstrateRejectedException ->
                        app.getString(R.string.polkadot_transfer_rejected)
                    error is SubstrateNodeApi.SubstrateNotSubmittedException -> friendlyRpcError(error)
                    error is IllegalArgumentException -> friendlyPolkadotTransferError(error)
                    pending != null -> app.getString(R.string.polkadot_transfer_unknown, pending.hash)
                    else -> friendlyRpcError(error)
                }
                if (pending != null) refresh()
            } finally {
                busy = false
            }
        }
    }

    private fun verifyDevnet(info: ChainInfo) {
        require(info.chainId == DEVNET_CHAIN_ID && info.network == "devnet-test-only") {
            app.getString(R.string.error_wrong_network)
        }
    }

    private fun friendlyRpcError(error: Exception): String = when (error) {
        is NodeHttpException -> app.getString(R.string.error_request_rejected, error.statusCode)
        is IOException, is UnknownHostException, is ConnectException, is SocketTimeoutException ->
            app.getString(if (isPolkadotSelected) R.string.polkadot_rpc_unavailable else R.string.error_rpc_unavailable)
        else -> app.getString(if (isPolkadotSelected) R.string.polkadot_rpc_unavailable else R.string.error_rpc_unavailable)
    }

    private fun friendlyPolkadotTransferError(error: Exception): String {
        val message = error.message.orEmpty()
        return when {
            message.contains("nonce changed", ignoreCase = true) -> app.getString(R.string.error_nonce_changed)
            message.contains("balance changed", ignoreCase = true) -> app.getString(R.string.error_balance_changed)
            message.contains("fee changed", ignoreCase = true) -> app.getString(R.string.error_fee_changed)
            message.contains("insufficient", ignoreCase = true) -> app.getString(R.string.error_insufficient_balance)
            message.contains("identity", ignoreCase = true) -> app.getString(R.string.polkadot_identity_changed)
            else -> message.ifBlank { app.getString(R.string.error_transfer_changed) }
        }
    }

    private fun matchesReviewedTransaction(json: JSONObject, tx: WalletCrypto.SignedTransfer): Boolean =
        json.optString("hash") == tx.transactionHash() &&
            json.optString("status") == "confirmed" &&
            json.optString("from") == tx.from && json.optString("to") == tx.to &&
            json.optLong("amount", -1) == tx.amount && json.optLong("fee", -1) == tx.fee &&
            json.optLong("nonce", -1) == tx.nonce && json.optString("signature") == tx.signatureHex

    private class AmbiguousTransferException(val txHash: String, cause: Exception) :
        IllegalStateException("ambiguous", cause)

    companion object {
        const val DEMO_PAIR = "KEN/USD-DEMO"
        const val NETWORK_PYTHON = "python-devnet"
        const val NETWORK_POLKADOT = "polkadot-sdk-local"
    }
}

@Composable
fun KenanApp(vm: WalletViewModel = viewModel()) {
    val language = LocalConfiguration.current.locales[0]?.language.orEmpty()
    val direction = if (language == "ar") LayoutDirection.Rtl else LayoutDirection.Ltr
    CompositionLocalProvider(LocalLayoutDirection provides direction) {
        MaterialTheme(
            colorScheme = lightColorScheme(
                primary = Emerald,
                onPrimary = Color.White,
                secondary = Navy,
                tertiary = Gold,
                background = PageBg,
                onBackground = Ink,
                surface = Color.White,
                onSurface = Ink,
                error = Negative
            )
        ) {
            Surface(Modifier.fillMaxSize(), color = PageBg) {
                when {
                    !vm.ready -> LoadingWalletScreen()
                    vm.wallet == null -> SetupScreen(vm)
                    else -> WalletShell(vm)
                }
            }
        }
    }
}

@Composable
private fun LoadingWalletScreen() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(14.dp)) {
            CircularProgressIndicator(color = Emerald)
            Text(stringResource(R.string.loading_wallet), color = Muted)
        }
    }
}

@Composable
private fun SetupScreen(vm: WalletViewModel) {
    var recoveryMode by rememberSaveable { mutableStateOf(false) }
    var phraseInput by rememberSaveable { mutableStateOf("") }
    var generatedPhrase by remember { mutableStateOf<String?>(null) }
    var backupConfirmed by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.Center
    ) {
        AppCard {
            Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Text(stringResource(R.string.setup_title), style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                Text(stringResource(R.string.devnet_banner), color = Gold, style = MaterialTheme.typography.labelLarge)
                Text(stringResource(R.string.setup_body), color = Muted)
                if (recoveryMode) {
                    OutlinedTextField(
                        value = phraseInput,
                        onValueChange = { phraseInput = it },
                        label = { Text(stringResource(R.string.recovery_phrase)) },
                        modifier = Modifier.fillMaxWidth(),
                        minLines = 4,
                        maxLines = 7
                    )
                    Button(
                        onClick = { vm.saveMnemonic(phraseInput, allowExistingAddressMatch = true) },
                        enabled = !vm.busy && phraseInput.isNotBlank(),
                        modifier = Modifier.fillMaxWidth()
                    ) { Text(if (vm.busy) stringResource(R.string.checking) else stringResource(R.string.restore_and_save)) }
                    TextButton(onClick = { recoveryMode = false; phraseInput = "" }) { Text(stringResource(R.string.cancel)) }
                } else {
                    Button(
                        onClick = { generatedPhrase = WalletCrypto.createMnemonic(); backupConfirmed = false },
                        enabled = !vm.busy,
                        modifier = Modifier.fillMaxWidth()
                    ) { Text(stringResource(R.string.create_wallet)) }
                    OutlinedButton(
                        onClick = { recoveryMode = true },
                        enabled = !vm.busy,
                        modifier = Modifier.fillMaxWidth()
                    ) { Text(stringResource(R.string.restore_wallet)) }
                }
                vm.setupError?.let { ErrorText(it) }
            }
        }
    }
    generatedPhrase?.let { phrase ->
        AlertDialog(
            onDismissRequest = { generatedPhrase = null; backupConfirmed = false },
            title = { Text(stringResource(R.string.recovery_phrase)) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(stringResource(R.string.setup_body), color = Muted)
                    SelectionContainer { Text(phrase, style = MaterialTheme.typography.bodyLarge) }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = backupConfirmed, onCheckedChange = { backupConfirmed = it })
                        Text(stringResource(R.string.backup_confirm))
                    }
                    vm.setupError?.let { ErrorText(it) }
                }
            },
            confirmButton = {
                Button(
                    onClick = { vm.saveMnemonic(phrase) },
                    enabled = backupConfirmed && !vm.busy
                ) { Text(stringResource(R.string.save_encrypted_wallet)) }
            },
            dismissButton = {
                TextButton(onClick = { generatedPhrase = null; backupConfirmed = false }) { Text(stringResource(R.string.cancel)) }
            }
        )
    }
}

@Composable
private fun WalletShell(vm: WalletViewModel) {
    var selectedTab by rememberSaveable { mutableStateOf(AppTab.Home) }
    var showSend by remember { mutableStateOf(false) }
    Scaffold(
        containerColor = PageBg,
        bottomBar = {
            NavigationBar(containerColor = Color.White, tonalElevation = 8.dp) {
                val tabs = listOf(
                    Triple(AppTab.Home, R.string.tab_home, "⌂"),
                    Triple(AppTab.Markets, R.string.tab_markets, "◒"),
                    Triple(AppTab.Trade, R.string.tab_trade, "⇄"),
                    Triple(AppTab.Assets, R.string.tab_assets, "▣"),
                    Triple(AppTab.Settings, R.string.tab_settings, "⚙")
                )
                tabs.forEach { (tab, label, glyph) ->
                    NavigationBarItem(
                        selected = selectedTab == tab,
                        onClick = {
                            selectedTab = tab
                            if (tab == AppTab.Markets || tab == AppTab.Trade) vm.refreshDemoExchange()
                        },
                        icon = { Text(glyph, color = if (selectedTab == tab) Emerald else Muted, style = MaterialTheme.typography.titleMedium) },
                        label = { Text(stringResource(label), maxLines = 1) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = Emerald,
                            selectedTextColor = Emerald,
                            indicatorColor = SoftGreen,
                            unselectedIconColor = Muted,
                            unselectedTextColor = Muted
                        )
                    )
                }
            }
        }
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when (selectedTab) {
                AppTab.Home -> DashboardScreen(vm, onSend = { showSend = true }, onReceive = { selectedTab = AppTab.Assets })
                AppTab.Markets -> MarketsScreen(vm)
                AppTab.Trade -> TradeScreen(vm)
                AppTab.Assets -> AssetsScreen(vm, onSend = { showSend = true })
                AppTab.Settings -> SettingsScreen(vm)
            }
        }
    }
    if (showSend) SendEntryDialog(vm, onDismiss = { showSend = false })
    vm.quote?.let { quote -> TransferReviewDialog(vm, quote) }
    vm.polkadotQuote?.let { quote -> PolkadotTransferReviewDialog(vm, quote) }
}

@Composable
private fun DashboardScreen(vm: WalletViewModel, onSend: () -> Unit, onReceive: () -> Unit) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text(stringResource(R.string.greeting), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, color = Ink)
                Text(
                    stringResource(if (vm.isPolkadotSelected) R.string.polkadot_template_banner else R.string.devnet_banner),
                    style = MaterialTheme.typography.labelMedium,
                    color = Gold
                )
            }
            StatusPill(isConnected = vm.chain is UiState.Ok)
        }
        BalanceHero(vm)
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            PrimaryAction(
                modifier = Modifier.weight(1f),
                title = stringResource(R.string.send),
                icon = Icons.AutoMirrored.Filled.Send,
                onClick = onSend
            )
            SecondaryAction(
                modifier = Modifier.weight(1f),
                title = stringResource(R.string.receive),
                glyph = "↙",
                onClick = onReceive
            )
        }
        ConnectionCard(vm)
        PriceHistoryCard()
        RecentActivityCard(vm)
        Text(stringResource(R.string.security_note), style = MaterialTheme.typography.bodySmall, color = Muted, modifier = Modifier.padding(horizontal = 4.dp))
    }
}

@Composable
private fun BalanceHero(vm: WalletViewModel) {
    val balance = (vm.account as? UiState.Ok)?.value?.balanceBase
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(28.dp),
        colors = CardDefaults.cardColors(containerColor = Navy)
    ) {
        Box(Modifier.background(Brush.linearGradient(listOf(Navy, Color(0xFF1B4653)))).padding(22.dp)) {
            Column(verticalArrangement = Arrangement.spacedBy(15.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(stringResource(R.string.total_assets), color = Color.White.copy(alpha = .78f), modifier = Modifier.weight(1f))
                    val visibilityLabel = stringResource(if (vm.balanceVisible) R.string.hide_balance else R.string.show_balance)
                    IconButton(onClick = vm::toggleBalanceVisibility) {
                        Text(
                            if (vm.balanceVisible) "◉" else "○",
                            modifier = Modifier.semantics { contentDescription = visibilityLabel },
                            color = Color.White,
                            style = MaterialTheme.typography.titleLarge
                        )
                    }
                }
                if (balance == null) {
                    Text("—", style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.Bold, color = Color.White)
                    Text(stringResource(R.string.balance_unavailable), color = Color.White.copy(alpha = .72f), style = MaterialTheme.typography.bodySmall)
                } else {
                    val text = if (vm.balanceVisible) vm.formatCurrentAmount(balance) else "••••••"
                    Text(text, style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.Bold, color = Color.White)
                    Text(vm.currentTokenSymbol, color = Mint, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                }
                val address = vm.currentAddress
                if (address.isNotBlank()) {
                    Text(stringResource(R.string.account_address), color = Color.White.copy(alpha = .62f), style = MaterialTheme.typography.labelSmall)
                    Text(shortAddress(address), color = Color.White.copy(alpha = .82f), style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

@Composable
private fun ConnectionCard(vm: WalletViewModel) {
    val isConnected = vm.chain is UiState.Ok && vm.account is UiState.Ok
    AppCard {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(stringResource(R.string.network_status), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text(
                        stringResource(
                            when {
                                isConnected -> R.string.connected
                                vm.chain is UiState.Loading -> R.string.checking
                                vm.currentRpcEndpoint.isBlank() -> R.string.rpc_unconfigured
                                else -> R.string.disconnected
                            }
                        ),
                        color = if (isConnected) Positive else Negative,
                        style = MaterialTheme.typography.bodySmall
                    )
                }
                IconButton(onClick = vm::refresh, enabled = !vm.busy) {
                    Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.retry), tint = Emerald)
                }
            }
            val info = (vm.chain as? UiState.Ok)?.value
            if (info != null) {
                Text(stringResource(R.string.chain_info, info.network, info.chainId), style = MaterialTheme.typography.bodySmall, color = Muted)
                Text(stringResource(R.string.height, info.height), style = MaterialTheme.typography.bodySmall, color = Muted)
            } else {
                Text(
                    stringResource(
                        when {
                            vm.currentRpcEndpoint.isBlank() -> R.string.rpc_unconfigured
                            vm.isPolkadotSelected -> R.string.polkadot_rpc_help
                            else -> R.string.rpc_help
                        }
                    ),
                    style = MaterialTheme.typography.bodySmall,
                    color = Muted
                )
            }
        }
    }
}

@Composable
private fun PriceHistoryCard() {
    AppCard {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(stringResource(R.string.chart_title), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Text(stringResource(R.string.chart_unavailable), style = MaterialTheme.typography.bodySmall, color = Muted)
        }
    }
}

@Composable
private fun RecentActivityCard(vm: WalletViewModel) {
    AppCard {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(stringResource(R.string.recent_activity), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            if (vm.recentTransactions.isEmpty()) {
                val emptyMessage = when {
                    vm.currentRpcEndpoint.isBlank() -> R.string.rpc_unconfigured
                    vm.chain !is UiState.Ok -> R.string.error_rpc_unavailable
                    vm.historyUnavailable -> R.string.history_unavailable
                    else -> R.string.no_recent_activity
                }
                Text(stringResource(emptyMessage), color = Muted, style = MaterialTheme.typography.bodySmall)
            } else {
                vm.recentTransactions.take(5).forEachIndexed { index, tx ->
                    if (index > 0) HorizontalDivider(color = Border)
                    val outgoing = tx.from == vm.currentAddress
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
                        Surface(
                            shape = CircleShape,
                            color = if (outgoing) SoftRed else SoftGreen,
                            modifier = Modifier.size(38.dp)
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                Text(if (outgoing) "↗" else "↙", color = if (outgoing) Negative else Positive, style = MaterialTheme.typography.titleMedium)
                            }
                        }
                        Column(Modifier.weight(1f).padding(horizontal = 10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(stringResource(if (outgoing) R.string.outgoing else R.string.incoming), fontWeight = FontWeight.Medium)
                            Text(stringResource(R.string.confirmed_at, tx.blockHeight), style = MaterialTheme.typography.bodySmall, color = Muted)
                        }
                        Text(
                            (if (outgoing) "−" else "+") + vm.formatCurrentAmount(tx.amountBase) + " " + vm.currentTokenSymbol,
                            color = if (outgoing) Ink else Positive,
                            style = MaterialTheme.typography.bodyMedium,
                            fontWeight = FontWeight.SemiBold
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun MarketsScreen(vm: WalletViewModel) {
    LaunchedEffect(vm.backendUrl, vm.wallet?.address) { vm.refreshDemoExchange() }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        ScreenHeader(stringResource(R.string.markets_title), stringResource(R.string.demo_market_note)) {
            IconButton(onClick = vm::refreshDemoExchange, enabled = !vm.demoLoading) {
                Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.retry), tint = Emerald)
            }
        }
        AppCard {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(stringResource(R.string.pair), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                    DemoBadge()
                }
                val last = vm.demoSnapshot?.trades?.lastOrNull()
                if (last != null) {
                    Text(stringResource(R.string.last_trade), color = Muted, style = MaterialTheme.typography.bodySmall)
                    Text(stringResource(R.string.last_trade_price, formatDecimal(last.price)), style = MaterialTheme.typography.headlineSmall, color = Emerald, fontWeight = FontWeight.Bold)
                } else {
                    Text(stringResource(R.string.no_trades), color = Muted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        vm.demoError?.let { ErrorPanel(it) }
        vm.demoMessage?.let { SuccessPanel(it) }
        if (vm.demoLoading && vm.demoSnapshot == null) LoadingPanel()
        vm.demoSnapshot?.let { snapshot ->
            if (!snapshot.demoOnly) {
                ErrorPanel(stringResource(R.string.demo_market_note))
            } else {
                if (snapshot.trades.size >= 2) {
                    AppCard {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(stringResource(R.string.chart_title), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                            PriceSparkline(snapshot.trades.map { it.price }, Modifier.fillMaxWidth().height(110.dp))
                            Text(stringResource(R.string.demo_market_note), color = Muted, style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
                OrderBookCard(snapshot)
                RecentDemoTradesCard(snapshot)
            }
        }
        if (vm.demoSnapshot == null && vm.demoError == null && !vm.demoLoading) {
            EmptyPanel(stringResource(R.string.demo_backend_unavailable))
        }
    }
}

@Composable
private fun OrderBookCard(snapshot: DemoMarketSnapshot) {
    AppCard {
        Column(verticalArrangement = Arrangement.spacedBy(9.dp)) {
            Text(stringResource(R.string.order_book), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Row(Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.asks), color = Negative, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                Text(stringResource(R.string.bids), color = Positive, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            }
            if (snapshot.asks.isEmpty() && snapshot.bids.isEmpty()) {
                Text(stringResource(R.string.no_book_orders), color = Muted, style = MaterialTheme.typography.bodySmall)
            } else {
                val count = maxOf(snapshot.asks.size, snapshot.bids.size).coerceAtMost(5)
                repeat(count) { index ->
                    Row(Modifier.fillMaxWidth()) {
                        BookCell(snapshot.asks.getOrNull(index), Negative, Modifier.weight(1f))
                        BookCell(snapshot.bids.getOrNull(index), Positive, Modifier.weight(1f))
                    }
                }
            }
        }
    }
}

@Composable
private fun BookCell(entry: DemoBookEntry?, tint: Color, modifier: Modifier = Modifier) {
    if (entry == null) {
        Text("—", modifier = modifier.padding(vertical = 3.dp), color = Muted)
    } else {
        Text(
            "${formatDecimal(entry.price ?: 0.0)}   ${formatDecimal(entry.quantity)}",
            modifier = modifier.padding(vertical = 3.dp),
            color = tint,
            style = MaterialTheme.typography.bodySmall
        )
    }
}

@Composable
private fun RecentDemoTradesCard(snapshot: DemoMarketSnapshot) {
    AppCard {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(stringResource(R.string.recent_trades), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            if (snapshot.trades.isEmpty()) {
                Text(stringResource(R.string.no_trades), color = Muted, style = MaterialTheme.typography.bodySmall)
            } else {
                snapshot.trades.takeLast(8).asReversed().forEachIndexed { index, trade ->
                    if (index > 0) HorizontalDivider(color = Border)
                    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(formatDecimal(trade.price), modifier = Modifier.weight(1f), fontWeight = FontWeight.Medium)
                        Text(formatDecimal(trade.quantity) + " KEN", color = Muted)
                        if (trade.demoOnly) DemoBadge()
                    }
                }
            }
        }
    }
}

@Composable
private fun TradeScreen(vm: WalletViewModel) {
    LaunchedEffect(vm.backendUrl, vm.wallet?.address) { vm.refreshDemoExchange() }
    var side by rememberSaveable { mutableStateOf("buy") }
    var kind by rememberSaveable { mutableStateOf("limit") }
    var quantity by rememberSaveable { mutableStateOf("") }
    var price by rememberSaveable { mutableStateOf("") }
    val snapshot = vm.demoSnapshot
    val userOrders = snapshot?.orders?.filter { it.user == vm.wallet?.address }.orEmpty()
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        ScreenHeader(stringResource(R.string.trade_title), stringResource(R.string.demo_market_note)) {
            DemoBadge()
        }
        AppCard {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(stringResource(R.string.pair), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                    Text(stringResource(R.string.demo_only), color = Gold, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = side == "buy", onClick = { side = "buy" }, label = { Text(stringResource(R.string.buy)) })
                    FilterChip(selected = side == "sell", onClick = { side = "sell" }, label = { Text(stringResource(R.string.sell)) })
                    Spacer(Modifier.weight(1f))
                    FilterChip(selected = kind == "limit", onClick = { kind = "limit" }, label = { Text(stringResource(R.string.limit_order)) })
                    FilterChip(selected = kind == "market", onClick = { kind = "market" }, label = { Text(stringResource(R.string.market_order)) })
                }
                OutlinedTextField(
                    value = quantity,
                    onValueChange = { quantity = it },
                    label = { Text(stringResource(R.string.order_quantity)) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal)
                )
                if (kind == "limit") {
                    OutlinedTextField(
                        value = price,
                        onValueChange = { price = it },
                        label = { Text(stringResource(R.string.limit_price)) },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal)
                    )
                }
                Text(stringResource(R.string.demo_reset_notice), color = Muted, style = MaterialTheme.typography.bodySmall)
                Button(
                    onClick = {
                        val qty = quantity.toDoubleOrNull()
                        val limitPrice = if (kind == "limit") price.toDoubleOrNull() else null
                        if (qty != null && qty > 0 && (kind == "market" || (limitPrice != null && limitPrice > 0))) {
                            vm.placeDemoOrder(side, kind, qty, limitPrice)
                        }
                    },
                    enabled = !vm.demoLoading && quantity.toDoubleOrNull()?.let { it > 0 } == true &&
                        (kind == "market" || price.toDoubleOrNull()?.let { it > 0 } == true),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text(if (vm.demoLoading) stringResource(R.string.checking) else stringResource(R.string.place_order))
                }
                vm.demoError?.let { ErrorText(it) }
                vm.demoMessage?.let { SuccessText(it) }
            }
        }
        DemoBalancesCard(vm, snapshot)
        AppCard {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.active_orders), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                if (userOrders.isEmpty()) {
                    Text(stringResource(R.string.no_user_orders), color = Muted, style = MaterialTheme.typography.bodySmall)
                } else {
                    userOrders.takeLast(10).asReversed().forEachIndexed { index, order ->
                        if (index > 0) HorizontalDivider(color = Border)
                        Row(Modifier.fillMaxWidth().padding(vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("${order.side.uppercase(Locale.ROOT)} · ${formatDecimal(order.quantity)} KEN", fontWeight = FontWeight.Medium)
                                Text(stringResource(R.string.order_status, localizedOrderStatus(order.status), formatDecimal(order.filled)), color = Muted, style = MaterialTheme.typography.bodySmall)
                            }
                            if (order.status == "open") {
                                TextButton(onClick = { vm.cancelDemoOrder(order.id) }, enabled = !vm.demoLoading) {
                                    Text(stringResource(R.string.cancel_order), color = Negative)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun DemoBalancesCard(vm: WalletViewModel, snapshot: DemoMarketSnapshot?) {
    val balances = snapshot?.balances.orEmpty().associateBy { it.asset }
    AppCard {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.demo_balances), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                DemoBadge()
            }
            listOf("KEN", "USD-DEMO").forEach { asset ->
                val balance = balances[asset]
                val available = if (snapshot == null) "—" else formatDecimal(balance?.available ?: 0.0)
                val reserved = if (snapshot == null) "—" else formatDecimal(balance?.reserved ?: 0.0)
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text(asset, fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
                    Column(horizontalAlignment = Alignment.End) {
                        Text(stringResource(R.string.available) + ": " + available)
                        Text(stringResource(R.string.reserved) + ": " + reserved, color = Muted, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
            HorizontalDivider(color = Border)
            Text(stringResource(R.string.demo_reset_notice), color = Muted, style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { vm.addDemoCredits("KEN") }, enabled = !vm.demoLoading, modifier = Modifier.weight(1f)) {
                    Text(stringResource(R.string.deposit_ken_demo))
                }
                OutlinedButton(onClick = { vm.addDemoCredits("USD-DEMO") }, enabled = !vm.demoLoading, modifier = Modifier.weight(1f)) {
                    Text(stringResource(R.string.deposit_usd_demo))
                }
            }
            vm.demoError?.let { ErrorText(it) }
            vm.demoMessage?.let { SuccessText(it) }
        }
    }
}

@Composable
private fun AssetsScreen(vm: WalletViewModel, onSend: () -> Unit) {
    val clipboard = LocalClipboardManager.current
    var copied by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        ScreenHeader(
            stringResource(R.string.assets_title),
            stringResource(if (vm.isPolkadotSelected) R.string.polkadot_template_banner else R.string.devnet_banner)
        )
        AppCard {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    if (vm.isPolkadotSelected) vm.currentTokenSymbol + " · " + stringResource(R.string.polkadot_local_asset)
                    else stringResource(R.string.on_chain_asset),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold
                )
                val account = (vm.account as? UiState.Ok)?.value
                Text(
                    if (vm.balanceVisible && account != null) vm.formatCurrentAmount(account.balanceBase) + " " + vm.currentTokenSymbol else if (account == null) "—" else "••••••",
                    style = MaterialTheme.typography.headlineMedium,
                    color = Emerald,
                    fontWeight = FontWeight.Bold
                )
                if (account == null) Text(stringResource(R.string.balance_unavailable), color = Muted, style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = onSend, modifier = Modifier.weight(1f)) { Icon(Icons.AutoMirrored.Filled.Send, null); Spacer(Modifier.width(7.dp)); Text(stringResource(R.string.send)) }
                    OutlinedButton(onClick = vm::refresh, enabled = !vm.busy, modifier = Modifier.weight(1f)) { Icon(Icons.Filled.Refresh, null); Spacer(Modifier.width(7.dp)); Text(stringResource(R.string.retry)) }
                }
            }
        }
        AppCard {
            Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.receive), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, modifier = Modifier.align(Alignment.Start))
                Text(
                    stringResource(if (vm.isPolkadotSelected) R.string.polkadot_receive_hint else R.string.receive_hint),
                    color = Muted,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.align(Alignment.Start)
                )
                vm.currentAddress.takeIf { it.isNotBlank() }?.let { address ->
                    AddressQr(address)
                    Text(address, color = Ink, style = MaterialTheme.typography.bodySmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        OutlinedButton(
                            onClick = { clipboard.setText(AnnotatedString(address)); copied = true },
                            modifier = Modifier.weight(1f)
                        ) { Text(stringResource(if (copied) R.string.copied else R.string.copy_address)) }
                        IconButton(onClick = vm::toggleBalanceVisibility) {
                            Text(if (vm.balanceVisible) "◉" else "○", color = Emerald, style = MaterialTheme.typography.titleLarge)
                        }
                    }
                }
            }
        }
        RecentActivityCard(vm)
    }
}

@Composable
private fun SettingsScreen(vm: WalletViewModel) {
    var rpcText by remember(vm.rpcUrl) { mutableStateOf(vm.rpcUrl) }
    var backendText by remember(vm.backendUrl) { mutableStateOf(vm.backendUrl) }
    var substrateRpcText by remember(vm.substrateRpcUrl) { mutableStateOf(vm.substrateRpcUrl) }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        ScreenHeader(stringResource(R.string.settings_title), stringResource(R.string.security_note))
        AppCard {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(stringResource(R.string.network_choice_title), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    FilterChip(
                        selected = !vm.isPolkadotSelected,
                        onClick = { vm.selectNetwork(WalletViewModel.NETWORK_PYTHON) },
                        label = { Text(stringResource(R.string.network_python_option)) },
                        modifier = Modifier.fillMaxWidth()
                    )
                    FilterChip(
                        selected = vm.isPolkadotSelected,
                        onClick = { vm.selectNetwork(WalletViewModel.NETWORK_POLKADOT) },
                        label = { Text(stringResource(R.string.network_polkadot_option)) },
                        modifier = Modifier.fillMaxWidth()
                    )
                }
                Text(
                    stringResource(if (vm.isPolkadotSelected) R.string.polkadot_demo_exchange_note else R.string.devnet_banner),
                    color = Muted,
                    style = MaterialTheme.typography.bodySmall
                )
            }
        }
        AppCard {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = rpcText,
                    onValueChange = { rpcText = it },
                    label = { Text(stringResource(R.string.rpc_address)) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
                )
                Text(
                    stringResource(if (isAndroidEmulator()) R.string.emulator_default_note else R.string.rpc_help),
                    style = MaterialTheme.typography.bodySmall,
                    color = Muted
                )
                OutlinedTextField(
                    value = backendText,
                    onValueChange = { backendText = it },
                    label = { Text(stringResource(R.string.backend_address)) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
                )
                Button(onClick = { vm.saveEndpoints(rpcText, backendText) }, modifier = Modifier.fillMaxWidth()) {
                    Text(stringResource(R.string.save_settings))
                }
                vm.settingsError?.let { ErrorText(it) }
                vm.settingsMessage?.let { SuccessText(it) }
                HorizontalDivider(color = Border)
                Text(stringResource(R.string.lan_setup_title), style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                Text(stringResource(R.string.lan_setup_body), color = Muted, style = MaterialTheme.typography.bodySmall)
            }
        }
        PolkadotNetworkSettings(vm, substrateRpcText) { substrateRpcText = it }
        ConnectionCard(vm)
    }
}

@Composable
private fun PolkadotNetworkSettings(vm: WalletViewModel, rpcText: String, onRpcTextChange: (String) -> Unit) {
    AppCard {
        Column(verticalArrangement = Arrangement.spacedBy(11.dp)) {
            Text(stringResource(R.string.polkadot_settings_title), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            OutlinedTextField(
                value = rpcText,
                onValueChange = onRpcTextChange,
                label = { Text(stringResource(R.string.polkadot_rpc_address)) },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
            )
            Text(stringResource(R.string.polkadot_rpc_help), style = MaterialTheme.typography.bodySmall, color = Muted)
            Button(onClick = { vm.saveSubstrateRpc(rpcText) }, enabled = !vm.busy, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.save_settings))
            }
            Button(
                onClick = vm::inspectCurrentPolkadotNetwork,
                enabled = !vm.busy && vm.substrateRpcUrl.isNotBlank(),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(stringResource(R.string.polkadot_inspect_network))
            }
            if (vm.polkadotIdentity != null && !vm.isPolkadotTrusted) {
                Button(
                    onClick = vm::trustCurrentPolkadotNetwork,
                    enabled = !vm.busy,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text(stringResource(if (vm.polkadotChainPin == null) R.string.polkadot_trust_network else R.string.polkadot_repin_network))
                }
            }
            vm.polkadotIdentity?.let { identity ->
                Text(
                    stringResource(R.string.polkadot_identity_summary, identity.chainName, identity.systemName, identity.specName, identity.specVersion),
                    style = MaterialTheme.typography.bodySmall,
                    color = Ink
                )
                Text(stringResource(R.string.polkadot_identity_token, identity.tokenSymbol, identity.tokenDecimals, identity.finalizedHeight), style = MaterialTheme.typography.bodySmall, color = Muted)
                Text(stringResource(R.string.polkadot_review_genesis), style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                SelectionContainer { Text(identity.genesisHash, style = MaterialTheme.typography.bodySmall) }
                Text(
                    stringResource(if (vm.isPolkadotTrusted) R.string.polkadot_pin_matches else R.string.polkadot_pin_mismatch),
                    color = if (vm.isPolkadotTrusted) Positive else Negative,
                    style = MaterialTheme.typography.bodySmall
                )
            }
            vm.polkadotPending?.let { pending ->
                Text(stringResource(R.string.polkadot_transfer_still_pending, pending.hash), color = Negative, style = MaterialTheme.typography.bodySmall)
            }
            Text(stringResource(R.string.polkadot_template_warning), color = Gold, style = MaterialTheme.typography.bodySmall)
            vm.settingsError?.let { ErrorText(it) }
            vm.settingsMessage?.let { SuccessText(it) }
        }
    }
}

@Composable
private fun SendEntryDialog(vm: WalletViewModel, onDismiss: () -> Unit) {
    var recipient by rememberSaveable { mutableStateOf("") }
    var amount by rememberSaveable { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = { if (!vm.busy) onDismiss() },
        title = {
            Text(
                stringResource(if (vm.isPolkadotSelected) R.string.polkadot_send_title else R.string.send_ken_title),
                fontWeight = FontWeight.Bold
            )
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value = recipient,
                    onValueChange = { recipient = it },
                    label = { Text(stringResource(R.string.recipient)) },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )
                OutlinedTextField(
                    value = amount,
                    onValueChange = { amount = it },
                    label = {
                        Text(
                            if (vm.isPolkadotSelected) stringResource(R.string.polkadot_amount, vm.currentTokenSymbol)
                            else stringResource(R.string.amount)
                        )
                    },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal)
                )
                Text(stringResource(R.string.transfer_security), style = MaterialTheme.typography.bodySmall, color = Muted)
                vm.sendError?.let { ErrorText(it) }
                vm.sendResult?.let { SuccessText(it) }
            }
        },
        confirmButton = {
            Button(onClick = { vm.prepareTransfer(recipient, amount) }, enabled = !vm.busy) {
                Text(if (vm.busy) stringResource(R.string.checking) else stringResource(R.string.preview_transfer))
            }
        },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !vm.busy) { Text(stringResource(R.string.cancel)) } }
    )
}

@Composable
private fun PolkadotTransferReviewDialog(vm: WalletViewModel, quote: PolkadotTransferQuote) {
    val totalBase = if (quote.amountBase <= Long.MAX_VALUE - quote.feeBase) quote.amountBase + quote.feeBase else Long.MAX_VALUE
    AlertDialog(
        onDismissRequest = { if (!vm.busy) vm.cancelQuote() },
        title = { Text(stringResource(R.string.review_transfer), fontWeight = FontWeight.Bold) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                ReviewLine(stringResource(R.string.polkadot_review_chain), quote.identity.chainName + " · " + quote.identity.systemName)
                ReviewLine(stringResource(R.string.polkadot_review_genesis), quote.identity.genesisHash)
                ReviewLine(stringResource(R.string.recipient), quote.recipient)
                ReviewLine(stringResource(R.string.polkadot_amount, quote.identity.tokenSymbol), WalletCrypto.formatAmount(quote.amountBase, quote.identity.tokenDecimals) + " " + quote.identity.tokenSymbol)
                ReviewLine(stringResource(R.string.fee), WalletCrypto.formatAmount(quote.feeBase, quote.identity.tokenDecimals) + " " + quote.identity.tokenSymbol)
                ReviewLine(stringResource(R.string.total), WalletCrypto.formatAmount(totalBase, quote.identity.tokenDecimals) + " " + quote.identity.tokenSymbol)
                ReviewLine(stringResource(R.string.nonce), quote.nonce.toString())
                Text(stringResource(R.string.polkadot_transfer_security), color = Muted, style = MaterialTheme.typography.bodySmall)
                vm.sendError?.let { ErrorText(it) }
            }
        },
        confirmButton = {
            Button(onClick = vm::confirmTransfer, enabled = !vm.busy && vm.isPolkadotTrusted && vm.polkadotPending == null) {
                Text(if (vm.busy) stringResource(R.string.sending) else stringResource(R.string.confirm_sign_send))
            }
        },
        dismissButton = {
            TextButton(onClick = vm::cancelQuote, enabled = !vm.busy) { Text(stringResource(R.string.cancel)) }
        }
    )
}

@Composable
private fun TransferReviewDialog(vm: WalletViewModel, quote: TransferQuote) {
    AlertDialog(
        onDismissRequest = { if (!vm.busy) vm.cancelQuote() },
        title = { Text(stringResource(R.string.review_transfer), fontWeight = FontWeight.Bold) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                ReviewLine(stringResource(R.string.recipient), quote.recipient)
                ReviewLine(stringResource(R.string.amount), WalletCrypto.formatKen(quote.amountBase) + " KEN")
                ReviewLine(stringResource(R.string.fee), WalletCrypto.formatKen(quote.feeBase) + " KEN")
                ReviewLine(stringResource(R.string.total), WalletCrypto.formatKen(quote.amountBase + quote.feeBase) + " KEN")
                ReviewLine(stringResource(R.string.nonce), quote.nonce.toString())
                Text(stringResource(R.string.transfer_security), color = Muted, style = MaterialTheme.typography.bodySmall)
                vm.sendError?.let { ErrorText(it) }
            }
        },
        confirmButton = {
            Button(onClick = vm::confirmTransfer, enabled = !vm.busy) {
                Text(if (vm.busy) stringResource(R.string.sending) else stringResource(R.string.confirm_sign_send))
            }
        },
        dismissButton = {
            TextButton(onClick = vm::cancelQuote, enabled = !vm.busy) { Text(stringResource(R.string.cancel)) }
        }
    )
}

@Composable
private fun ScreenHeader(title: String, subtitle: String, action: (@Composable RowScope.() -> Unit)? = null) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, color = Ink)
            Text(subtitle, color = Muted, style = MaterialTheme.typography.bodySmall)
        }
        action?.invoke(this)
    }
}

@Composable
private fun AppCard(content: @Composable ColumnScope.() -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(22.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, Border)
    ) {
        Column(Modifier.fillMaxWidth().padding(16.dp), content = content)
    }
}

@Composable
private fun StatusPill(isConnected: Boolean) {
    Surface(shape = CircleShape, color = if (isConnected) SoftGreen else Color(0xFFFFF4E0)) {
        Row(Modifier.padding(horizontal = 11.dp, vertical = 7.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Box(Modifier.size(7.dp).background(if (isConnected) Positive else Gold, CircleShape))
            Text(stringResource(if (isConnected) R.string.connected else R.string.disconnected), color = if (isConnected) Positive else Gold, style = MaterialTheme.typography.labelSmall, maxLines = 1)
        }
    }
}

@Composable
private fun DemoBadge() {
    Surface(shape = CircleShape, color = Color(0xFFFFF3D7)) {
        Text(stringResource(R.string.demo_only), color = Color(0xFF88631B), style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold, modifier = Modifier.padding(horizontal = 9.dp, vertical = 5.dp))
    }
}

@Composable
private fun PrimaryAction(modifier: Modifier, title: String, icon: androidx.compose.ui.graphics.vector.ImageVector, onClick: () -> Unit) {
    Button(onClick = onClick, modifier = modifier.height(54.dp), shape = RoundedCornerShape(16.dp)) {
        Icon(icon, null, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(8.dp))
        Text(title)
    }
}

@Composable
private fun SecondaryAction(modifier: Modifier, title: String, glyph: String, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, modifier = modifier.height(54.dp), shape = RoundedCornerShape(16.dp)) {
        Text(glyph, color = Emerald, style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.width(8.dp))
        Text(title)
    }
}

@Composable
private fun ReviewLine(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.Top) {
        Text(label, color = Muted, modifier = Modifier.weight(1f))
        Text(value, color = Ink, fontWeight = FontWeight.Medium, modifier = Modifier.weight(1.4f))
    }
}

@Composable
private fun ErrorPanel(message: String) {
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp), color = SoftRed) {
        Text(message, color = Negative, modifier = Modifier.padding(14.dp), style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun ErrorText(message: String) {
    Text(message, color = Negative, style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun SuccessPanel(message: String) {
    Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp), color = SoftGreen) {
        Text(message, color = Positive, modifier = Modifier.padding(14.dp), style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun SuccessText(message: String) {
    Text(message, color = Positive, style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun LoadingPanel() {
    AppCard {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp, color = Emerald)
            Text(stringResource(R.string.checking), color = Muted)
        }
    }
}

@Composable
private fun EmptyPanel(message: String) {
    AppCard { Text(message, color = Muted, style = MaterialTheme.typography.bodySmall) }
}

@Composable
private fun PriceSparkline(values: List<Double>, modifier: Modifier = Modifier) {
    val prices = values.filter { it.isFinite() && it > 0.0 }
    Canvas(modifier) {
        if (prices.size < 2) return@Canvas
        val min = prices.minOrNull() ?: return@Canvas
        val max = prices.maxOrNull() ?: return@Canvas
        val range = (max - min).takeIf { it > 0.0 } ?: 1.0
        val inset = 8.dp.toPx()
        val width = size.width - 2 * inset
        val height = size.height - 2 * inset
        val path = Path()
        prices.forEachIndexed { index, value ->
            val x = inset + width * index / (prices.size - 1).toFloat()
            val y = inset + height * (1f - ((value - min) / range).toFloat())
            if (index == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        drawPath(path, color = Emerald, style = Stroke(width = 3.dp.toPx(), cap = StrokeCap.Round))
        val lastX = inset + width
        val lastY = inset + height * (1f - ((prices.last() - min) / range).toFloat())
        drawCircle(color = Mint, radius = 5.dp.toPx(), center = Offset(lastX, lastY))
    }
}

@Composable
private fun AddressQr(address: String) {
    val matrix = remember(address) {
        QRCodeWriter().encode(address, BarcodeFormat.QR_CODE, 320, 320, mapOf(EncodeHintType.MARGIN to 1))
    }
    Canvas(Modifier.size(230.dp).background(Color.White).border(1.dp, Border, RoundedCornerShape(16.dp)).padding(12.dp)) {
        drawRect(Color.White)
        val cellWidth = size.width / matrix.width
        val cellHeight = size.height / matrix.height
        for (y in 0 until matrix.height) for (x in 0 until matrix.width) {
            if (matrix[x, y]) drawRect(Color(0xFF10213A), Offset(x * cellWidth, y * cellHeight), androidx.compose.ui.geometry.Size(cellWidth, cellHeight))
        }
    }
}

private fun shortAddress(address: String): String =
    if (address.length > 20) address.take(12) + "…" + address.takeLast(6) else address

private fun formatDecimal(value: Double): String =
    String.format(Locale.US, "%.6f", value).trimEnd('0').trimEnd('.').ifEmpty { "0" }

@Composable
private fun localizedOrderStatus(status: String): String = when (status.lowercase(Locale.ROOT)) {
    "open" -> stringResource(R.string.status_open)
    "partially-filled" -> stringResource(R.string.status_partial)
    "filled" -> stringResource(R.string.status_filled)
    "cancelled", "cancelled-unfilled" -> stringResource(R.string.status_cancelled)
    "rejected" -> stringResource(R.string.status_rejected)
    else -> stringResource(R.string.status_unknown)
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { KenanApp() }
    }
}

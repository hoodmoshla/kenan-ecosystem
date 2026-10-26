package com.kenan.wallet

import android.app.Application
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter
import com.kenan.wallet.data.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

private val Emerald = Color(0xFF0E7C5B)
private val DarkBlue = Color(0xFF0B2A3A)
private val Ivory = Color(0xFFF8F3E7)
private val Gold = Color(0xFFC9A227)

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

class WalletViewModel(application: Application) : AndroidViewModel(application) {
    private val api = NodeApi()
    private val store = EncryptedWalletStore(application)
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
    var quote by mutableStateOf<TransferQuote?>(null)
        private set
    var sendError by mutableStateOf<String?>(null)
        private set
    var sendResult by mutableStateOf<String?>(null)
        private set

    init {
        viewModelScope.launch {
            try {
                wallet = withContext(Dispatchers.IO) { store.loadWallet() }
                ready = true
                if (wallet != null) refresh()
            } catch (e: Exception) {
                setupError = e.message ?: "تعذر فتح المحفظة المحلية"
                ready = true
            }
        }
    }

    fun saveMnemonic(phrase: String) {
        if (busy) return
        busy = true
        setupError = null
        viewModelScope.launch {
            try {
                wallet = withContext(Dispatchers.IO) { store.saveMnemonic(phrase) }
                ready = true
                busy = false
                setupError = null
                refresh()
            } catch (e: Exception) {
                setupError = e.message ?: "تعذر حفظ المحفظة المشفرة"
                busy = false
            }
        }
    }

    fun refresh() {
        val current = wallet ?: return
        viewModelScope.launch {
            account = UiState.Loading
            chain = UiState.Loading
            try {
                val info = api.chainInfo()
                verifyDevnet(info)
                val state = api.account(current.address)
                require(state.network == "devnet-test-only") { "العقدة لا تعرّف نفسها كشبكة اختبار KEN" }
                account = UiState.Ok(state)
                chain = UiState.Ok(info)
            } catch (e: Exception) {
                account = UiState.Err(e.message ?: "تعذر جلب الرصيد")
                chain = UiState.Err(e.message ?: "تعذر الاتصال بالعقدة")
            }
        }
    }

    fun prepareTransfer(recipientInput: String, amountInput: String) {
        val current = wallet ?: run { sendError = "أنشئ أو استعد محفظة أولاً"; return }
        val recipient = recipientInput.trim()
        if (!WalletCrypto.isValidAddress(recipient)) { sendError = "عنوان المستلم غير صالح"; return }
        val amount = try { WalletCrypto.parseKenAmount(amountInput) } catch (e: Exception) {
            sendError = e.message ?: "المبلغ غير صالح"; return
        }
        if (recipient == current.address) { sendError = "لا يمكن الإرسال إلى عنوان المحفظة نفسها"; return }
        sendError = null
        sendResult = null
        quote = null
        busy = true
        viewModelScope.launch {
            try {
                val info = api.chainInfo()
                verifyDevnet(info)
                require(info.feeBase == DEVNET_FEE_BASE) { "رسوم العقدة لا تطابق إعداد محفظة KEN الحالية" }
                val state = api.account(current.address)
                require(state.network == "devnet-test-only") { "العقدة ليست شبكة KEN التجريبية" }
                require(amount <= Long.MAX_VALUE - info.feeBase && state.balanceBase >= amount + info.feeBase) {
                    "الرصيد غير كافٍ للمبلغ والرسوم"
                }
                quote = TransferQuote(recipient, amount, info.feeBase, state.nonce, state.balanceBase)
                chain = UiState.Ok(info)
                account = UiState.Ok(state)
            } catch (e: Exception) {
                sendError = e.message ?: "تعذر إعداد المعاملة من العقدة"
            } finally {
                busy = false
            }
        }
    }

    fun cancelQuote() { quote = null }

    fun confirmTransfer() {
        val current = wallet ?: return
        val reviewed = quote ?: return
        if (busy) return
        busy = true
        sendError = null
        sendResult = null
        viewModelScope.launch {
            try {
                // Re-read account state immediately before signing. If the quote changed, require a new review.
                val info = api.chainInfo()
                verifyDevnet(info)
                require(info.feeBase == reviewed.feeBase) { "تغيّرت الرسوم؛ أعد مراجعة المعاملة قبل التوقيع" }
                val accountNow = api.account(current.address)
                require(accountNow.nonce == reviewed.nonce) { "تغيّر nonce؛ أعد مراجعة المعاملة قبل التوقيع" }
                require(accountNow.balanceBase == reviewed.balanceBase && accountNow.balanceBase >= reviewed.amountBase + reviewed.feeBase) {
                    "تغيّر الرصيد؛ أعد مراجعة المعاملة قبل التوقيع"
                }
                val tx = withContext(Dispatchers.IO) {
                    store.signTransfer(reviewed.recipient, reviewed.amountBase, reviewed.feeBase, reviewed.nonce)
                }
                require(tx.from == current.address) { "المفتاح المحلي لا يطابق عنوان المحفظة" }
                val expectedHash = tx.transactionHash()
                try {
                    val submitted = api.submitTx(tx.toJson())
                    require(submitted.getString("hash") == expectedHash) { "أعاد العقد تجزئة مختلفة للمعاملة" }
                } catch (submitError: Exception) {
                    if (submitError is NodeHttpException && submitError.statusCode in 400..499) throw submitError
                    // A lost response is ambiguous. Query the deterministic tx hash; never auto-resubmit.
                    val found = try { api.transaction(expectedHash) } catch (_: Exception) { null }
                    if (found == null || !matchesReviewedTransaction(found, tx)) {
                        throw IllegalStateException("تعذّر تأكيد نتيجة الإرسال. لم تتم إعادة الإرسال تلقائيًا؛ افحص الهاش $expectedHash في المستكشف قبل المحاولة مجددًا.", submitError)
                    }
                }
                val confirmed = api.transaction(expectedHash)
                require(matchesReviewedTransaction(confirmed, tx)) { "لم يطابق سجل RPC المعاملة التي راجعتها ووقّعتها" }
                sendResult = "تم التحقق عبر RPC: ${confirmed.getString("hash")} • الكتلة ${confirmed.getLong("block_height")}"
                quote = null
                refresh()
            } catch (e: Exception) {
                sendError = e.message ?: "فشل إرسال المعاملة أو التحقق منها"
            } finally {
                busy = false
            }
        }
    }

    private fun verifyDevnet(info: ChainInfo) {
        require(info.chainId == DEVNET_CHAIN_ID && info.network == "devnet-test-only") {
            "رفضت المحفظة التوقيع: العقدة ليست شبكة KEN التجريبية المتوقعة"
        }
    }

    private fun matchesReviewedTransaction(json: JSONObject, tx: WalletCrypto.SignedTransfer): Boolean =
        json.optString("hash") == tx.transactionHash() &&
            json.optString("status") == "confirmed" &&
            json.optString("from") == tx.from && json.optString("to") == tx.to &&
            json.optLong("amount", -1) == tx.amount && json.optLong("fee", -1) == tx.fee &&
            json.optLong("nonce", -1) == tx.nonce && json.optString("signature") == tx.signatureHex
}

@Composable
fun KenanApp(vm: WalletViewModel = viewModel()) {
    CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
        MaterialTheme(colorScheme = lightColorScheme(primary = Emerald, secondary = DarkBlue, tertiary = Gold, background = Ivory)) {
            Surface(Modifier.fillMaxSize(), color = Ivory) {
                when {
                    !vm.ready -> Box(Modifier.fillMaxSize(), contentAlignment = androidx.compose.ui.Alignment.Center) { CircularProgressIndicator() }
                    vm.wallet == null -> SetupScreen(vm)
                    else -> WalletNavigation(vm)
                }
            }
        }
    }
}

@Composable
private fun SetupScreen(vm: WalletViewModel) {
    var recoveryMode by remember { mutableStateOf(false) }
    var phraseInput by remember { mutableStateOf("") }
    var generatedPhrase by remember { mutableStateOf<String?>(null) }
    var backupConfirmed by remember { mutableStateOf(false) }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(22.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        Text("KenanWallet", style = MaterialTheme.typography.headlineMedium)
        Text("شبكة KEN التجريبية — أرصدة اختبارية فقط، وليست أموالاً حقيقية.", color = Gold)
        Text("تنشأ مفاتيح المحفظة على هذا الجهاز. تُحفظ عبارة الاستعادة مشفرة بمفتاح Android Keystore ولا تُرسل إلى العقدة.")
        if (recoveryMode) {
            OutlinedTextField(
                value = phraseInput,
                onValueChange = { phraseInput = it },
                label = { Text("عبارة الاستعادة (24 كلمة)") },
                modifier = Modifier.fillMaxWidth(),
                minLines = 4,
                maxLines = 7
            )
            Button(onClick = { vm.saveMnemonic(phraseInput); phraseInput = "" }, enabled = !vm.busy && phraseInput.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
                Text(if (vm.busy) "جارٍ التحقق والحفظ…" else "استعادة وتخزين مشفر")
            }
            TextButton(onClick = { recoveryMode = false; phraseInput = "" }) { Text("رجوع") }
        } else {
            Button(onClick = { generatedPhrase = WalletCrypto.createMnemonic(); backupConfirmed = false }, enabled = !vm.busy, modifier = Modifier.fillMaxWidth()) {
                Text("إنشاء محفظة جديدة")
            }
            OutlinedButton(onClick = { recoveryMode = true }, enabled = !vm.busy, modifier = Modifier.fillMaxWidth()) {
                Text("استعادة محفظة موجودة")
            }
        }
        vm.setupError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (generatedPhrase != null) {
            AlertDialog(
                onDismissRequest = { generatedPhrase = null; backupConfirmed = false },
                title = { Text("احفظ عبارة الاستعادة الآن") },
                text = {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("هذه العبارة هي النسخة الاحتياطية الوحيدة. لا تشاركها ولا تلتقط لها صورة؛ لن تُرسل إلى الشبكة.")
                        SelectionContainer { Text(generatedPhrase!!, style = MaterialTheme.typography.bodyLarge) }
                        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                            Checkbox(checked = backupConfirmed, onCheckedChange = { backupConfirmed = it })
                            Text("حفظت الكلمات الـ24 في مكان آمن")
                        }
                    }
                },
                confirmButton = {
                    Button(onClick = {
                        val phrase = generatedPhrase ?: return@Button
                        vm.saveMnemonic(phrase)
                        generatedPhrase = null
                        backupConfirmed = false
                    }, enabled = backupConfirmed && !vm.busy) { Text("حفظ المحفظة المشفرة") }
                },
                dismissButton = { TextButton(onClick = { generatedPhrase = null; backupConfirmed = false }) { Text("إلغاء") } }
            )
        }
    }
}

@Composable
private fun WalletNavigation(vm: WalletViewModel) {
    val nav = rememberNavController()
    NavHost(navController = nav, startDestination = "home") {
        composable("home") {
            HomeScreen(vm, onSend = { vm.cancelQuote(); nav.navigate("send") }, onReceive = { nav.navigate("receive") })
        }
        composable("send") { SendScreen(vm, onBack = { nav.popBackStack() }) }
        composable("receive") { ReceiveScreen(vm, onBack = { nav.popBackStack() }) }
    }
}

@Composable
private fun HomeScreen(vm: WalletViewModel, onSend: () -> Unit, onReceive: () -> Unit) {
    if (vm.wallet == null) return
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(13.dp)
    ) {
        Text("KenanWallet", style = MaterialTheme.typography.headlineMedium)
        Text("شبكة تجريبية فقط — لا توجد أموال حقيقية", color = Gold)
        when (val state = vm.account) {
            UiState.Loading -> CircularProgressIndicator()
            is UiState.Err -> Text("تعذر قراءة الرصيد من العقدة: ${state.message}", color = MaterialTheme.colorScheme.error)
            is UiState.Ok -> {
                Text("العنوان: ${state.value.address}", style = MaterialTheme.typography.bodySmall)
                Text("الرصيد: ${WalletCrypto.formatKen(state.value.balanceBase)} KEN", style = MaterialTheme.typography.headlineSmall)
                Text("Nonce: ${state.value.nonce}")
            }
        }
        when (val state = vm.chain) {
            is UiState.Ok -> Text("${state.value.network} • ${state.value.chainId} • ارتفاع ${state.value.height}", style = MaterialTheme.typography.bodySmall)
            is UiState.Err -> Text(state.message, style = MaterialTheme.typography.bodySmall)
            UiState.Loading -> Unit
        }
        Button(onClick = { vm.refresh() }, enabled = !vm.busy, modifier = Modifier.fillMaxWidth()) { Text("تحديث من RPC") }
        Button(onClick = onSend, enabled = !vm.busy, modifier = Modifier.fillMaxWidth()) { Text("إرسال KEN") }
        OutlinedButton(onClick = onReceive, modifier = Modifier.fillMaxWidth()) { Text("استقبال وعرض QR") }
        Text("المفتاح لا يغادر الجهاز. راجع المستلم والمبلغ والرسوم قبل الموافقة على التوقيع.", style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun SendScreen(vm: WalletViewModel, onBack: () -> Unit) {
    var recipient by remember { mutableStateOf("") }
    var amount by remember { mutableStateOf("") }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("إرسال KEN", style = MaterialTheme.typography.headlineMedium)
        OutlinedTextField(value = recipient, onValueChange = { recipient = it }, label = { Text("عنوان المستلم kenan1…") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        OutlinedTextField(value = amount, onValueChange = { amount = it }, label = { Text("المبلغ KEN") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        Text("لا توقيع قبل إظهار مراجعة المستلم والمبلغ والرسوم المستلمة من العقدة.", style = MaterialTheme.typography.bodySmall)
        Button(onClick = { vm.prepareTransfer(recipient, amount) }, enabled = !vm.busy, modifier = Modifier.fillMaxWidth()) {
            Text(if (vm.busy) "جارٍ التحقق من العقدة…" else "معاينة المبلغ والرسوم")
        }
        vm.sendError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        vm.sendResult?.let { Text(it, color = Emerald) }
        TextButton(onClick = onBack) { Text("رجوع") }
    }
    val quote = vm.quote
    if (quote != null) {
        AlertDialog(
            onDismissRequest = { if (!vm.busy) vm.cancelQuote() },
            title = { Text("راجع المعاملة قبل التوقيع") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                    Text("المستلم: ${quote.recipient}")
                    Text("المبلغ: ${WalletCrypto.formatKen(quote.amountBase)} KEN")
                    Text("الرسوم: ${WalletCrypto.formatKen(quote.feeBase)} KEN")
                    Text("الإجمالي: ${WalletCrypto.formatKen(quote.amountBase + quote.feeBase)} KEN")
                    Text("Nonce: ${quote.nonce} • الرصيد الحالي: ${WalletCrypto.formatKen(quote.balanceBase)} KEN")
                    Text("سيُوقّع المفتاح محليًا، ثم يُتحقق من المعاملة عبر RPC.", style = MaterialTheme.typography.bodySmall)
                }
            },
            confirmButton = { Button(onClick = { vm.confirmTransfer() }, enabled = !vm.busy) { Text(if (vm.busy) "جارٍ الإرسال والتحقق…" else "أوافق: وقّع وأرسل") } },
            dismissButton = { TextButton(onClick = { vm.cancelQuote() }, enabled = !vm.busy) { Text("إلغاء") } }
        )
    }
}

@Composable
private fun ReceiveScreen(vm: WalletViewModel, onBack: () -> Unit) {
    val address = vm.wallet?.address.orEmpty()
    val clipboard = LocalClipboardManager.current
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        Text("استقبال KEN", style = MaterialTheme.typography.headlineMedium)
        Text("أرسل KEN التجريبي إلى هذا العنوان فقط على kenan-devnet-1.", color = Gold)
        if (address.isNotBlank()) {
            AddressQr(address)
            Text(address, style = MaterialTheme.typography.bodySmall)
            OutlinedButton(onClick = { clipboard.setText(AnnotatedString(address)) }, modifier = Modifier.fillMaxWidth()) { Text("نسخ العنوان") }
        }
        TextButton(onClick = onBack) { Text("رجوع") }
    }
}

@Composable
private fun AddressQr(address: String) {
    val matrix = remember(address) {
        QRCodeWriter().encode(address, BarcodeFormat.QR_CODE, 320, 320, mapOf(EncodeHintType.MARGIN to 1))
    }
    Canvas(Modifier.size(240.dp).background(Color.White).padding(8.dp)) {
        drawRect(Color.White)
        val cellWidth = size.width / matrix.width
        val cellHeight = size.height / matrix.height
        for (y in 0 until matrix.height) for (x in 0 until matrix.width) {
            if (matrix[x, y]) drawRect(Color.Black, Offset(x * cellWidth, y * cellHeight), Size(cellWidth, cellHeight))
        }
    }
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { KenanApp() }
    }
}

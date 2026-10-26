package com.kenan.wallet

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.*
import com.kenan.wallet.data.*
import kotlinx.coroutines.launch

// Colours: emerald green, dark blue, ivory white, gold accent.
private val Emerald = androidx.compose.ui.graphics.Color(0xFF0E7C5B)
private val DarkBlue = androidx.compose.ui.graphics.Color(0xFF0B2A3A)
private val Ivory = androidx.compose.ui.graphics.Color(0xFFF8F3E7)
private val Gold = androidx.compose.ui.graphics.Color(0xFFC9A227)

sealed class UiState<out T> { data object Loading : UiState<Nothing>(); data class Ok<T>(val v: T) : UiState<T>(); data class Err(val msg: String) : UiState<Nothing>() }

class WalletViewModel(private val api: NodeApi = NodeApi()) : ViewModel() {
    var address by mutableStateOf(""); var account by mutableStateOf<UiState<AccountState>>(UiState.Loading)
    var chain by mutableStateOf<UiState<ChainInfo>>(UiState.Loading)
    fun load(addr: String) {
        address = addr
        viewModelScope.launch {
            account = try { UiState.Ok(api.account(addr)) } catch (e: Exception) { UiState.Err(e.message ?: "network error") }
            chain = try { UiState.Ok(api.chainInfo()) } catch (e: Exception) { UiState.Err(e.message ?: "network error") }
        }
    }
}

@Composable
fun KenanApp(vm: WalletViewModel = viewModel()) {
    // Arabic-first RTL: real apps switch on locale; this dev build defaults to RTL.
    CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
        MaterialTheme(colorScheme = lightColorScheme(primary = Emerald, secondary = DarkBlue, tertiary = Gold, background = Ivory)) {
            val nav = rememberNavController()
            NavHost(nav, startDestination = "home") {
                composable("home") { HomeScreen(vm, onSend = { nav.navigate("send") }, onReceive = { nav.navigate("receive") }, onTrade = { nav.navigate("trade") }, onIntel = { nav.navigate("intel") }) }
                composable("send") { SendScreen(vm, onBack = { nav.popBackStack() }) }
                composable("receive") { ReceiveScreen(vm, onBack = { nav.popBackStack() }) }
                composable("trade") { InfoScreen("التداول — تجريبي فقط", "أرصدة DEMO فقط. لا أموال حقيقية هنا، والتسوية على الشبكة غير مفعّلة بعد.", onBack = { nav.popBackStack() }) }
                composable("intel") { InfoScreen("Kenan Intelligence", "المساعد القاعدي يشرح الأرصدة والرسوم فقط، ولا يملك مفاتيحك ولا يوقّع نيابة عنك. مزود النماذج الخارجي غير متصل حالياً.", onBack = { nav.popBackStack() }) }
            }
        }
    }
}

@Composable
fun HomeScreen(vm: WalletViewModel, onSend: () -> Unit, onReceive: () -> Unit, onTrade: () -> Unit, onIntel: () -> Unit) {
    Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("KenanWallet", style = MaterialTheme.typography.headlineMedium)
        Text("شبكة تجريبية — أرصدة اختبارية فقط", color = Gold)
        // Address entry: previously load() was never called, so the home
        // screen stayed on Loading forever. The user pastes a devnet
        // address (created/restored in the release Keystore flow) and the
        // balance is then read LIVE from the node via NodeApi, not a mock.
        var addrInput by remember { mutableStateOf(vm.address) }
        OutlinedTextField(addrInput, { addrInput = it }, label = { Text("عنوان kenan1…") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = { vm.load(addrInput.trim()) }, modifier = Modifier.fillMaxWidth(), enabled = addrInput.startsWith("kenan1")) { Text("عرض الرصيد من العقدة") }
        when (val s = vm.account) {
            is UiState.Loading -> CircularProgressIndicator()
            is UiState.Err -> { Text("تعذّر تحميل الرصيد: ${s.msg}"); Button(onClick = { if (vm.address.isNotBlank()) vm.load(vm.address) }) { Text("إعادة المحاولة") } }
            is UiState.Ok -> {
                Text("العنوان: ${s.v.address}", style = MaterialTheme.typography.bodySmall)
                Text("الرصيد: ${s.v.balanceKen} KEN", style = MaterialTheme.typography.headlineSmall)
                Text("Nonce: ${s.v.nonce} • الشبكة: ${s.v.network}", style = MaterialTheme.typography.bodySmall)
            }
        }
        Button(onClick = onSend, modifier = Modifier.fillMaxWidth()) { Text("إرسال") }
        Button(onClick = onReceive, modifier = Modifier.fillMaxWidth()) { Text("استقبال") }
        Button(onClick = onTrade, modifier = Modifier.fillMaxWidth()) { Text("التداول (تجريبي)") }
        Button(onClick = onIntel, modifier = Modifier.fillMaxWidth()) { Text("Kenan Intelligence") }
        Text("ملاحظة أمان: احفظ عبارة الاستعادة خارج الجهاز. البصمة لا تغني عن النسخ الاحتياطي.", style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
fun SendScreen(vm: WalletViewModel, onBack: () -> Unit) {
    var to by remember { mutableStateOf("") }; var amount by remember { mutableStateOf("") }; var note by remember { mutableStateOf<String?>(null) }
    Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("إرسال KEN", style = MaterialTheme.typography.headlineMedium)
        OutlinedTextField(to, { to = it }, label = { Text("عنوان المستلم (kenan1…)") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(amount, { amount = it }, label = { Text("المبلغ KEN") }, modifier = Modifier.fillMaxWidth())
        Text("الرسوم التقديرية: 0.001 KEN — راجع المستلم والمبلغ والرسوم قبل التوقيع. التوقيع يتم محلياً فقط في نسخة الإصدار.")
        Button(onClick = { note = "تم إعداد مسودة فقط: التوقيع المحلي عبر Keystore غير مفعّل في نسخة التطوير هذه، ولن تُرسل أي معاملة." }, modifier = Modifier.fillMaxWidth(), enabled = to.startsWith("kenan1") && amount.toDoubleOrNull() != null) { Text("مراجعة وتوقيع") }
        note?.let { Text(it, color = Gold) }
        TextButton(onClick = onBack) { Text("رجوع") }
    }
}

@Composable
fun ReceiveScreen(vm: WalletViewModel, onBack: () -> Unit) {
    Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("استقبال", style = MaterialTheme.typography.headlineMedium)
        Text(vm.address.ifBlank { "أنشئ محفظة أولاً من شاشة التهيئة (غير مفعّلة في نسخة التطوير هذه)." })
        Text("رمز QR للعنوان يُعرض هنا في نسخة الإصدار.")
        TextButton(onClick = onBack) { Text("رجوع") }
    }
}

@Composable
fun InfoScreen(title: String, body: String, onBack: () -> Unit) {
    Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(title, style = MaterialTheme.typography.headlineMedium); Text(body); TextButton(onClick = onBack) { Text("رجوع") }
    }
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // SECURITY NOTE: production build must create/restore the wallet via
        // AndroidKeyStore (AES/GCM key in Keystore, seed never in plaintext
        // SharedPreferences) and prove backup before marking setup complete.
        // That flow is documented in docs/SECURITY.md; it is NOT claimed as
        // implemented in this uncompiled source snapshot.
        setContent { KenanApp() }
    }
}

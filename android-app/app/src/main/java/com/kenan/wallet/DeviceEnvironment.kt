package com.kenan.wallet

import android.os.Build
import java.util.Locale

/** Physical devices must configure a local RPC endpoint explicitly. */
internal fun isAndroidEmulator(): Boolean {
    val fingerprint = Build.FINGERPRINT.lowercase(Locale.ROOT)
    val model = Build.MODEL.lowercase(Locale.ROOT)
    val product = Build.PRODUCT.lowercase(Locale.ROOT)
    val hardware = Build.HARDWARE.lowercase(Locale.ROOT)
    return fingerprint.startsWith("generic") || fingerprint.contains("emulator") ||
        model.contains("google_sdk") || model.contains("emulator") || model.contains("android sdk built for") ||
        product.contains("sdk") || product.contains("emulator") ||
        hardware.contains("goldfish") || hardware.contains("ranchu")
}

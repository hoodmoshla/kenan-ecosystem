plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}
val kenanNodeUrl = providers.gradleProperty("kenanNodeUrl").orElse("").get()
fun String.asJavaStringLiteral(): String = "\"" + replace("\\", "\\\\").replace("\"", "\\\"") + "\""
android {
    namespace = "com.kenan.wallet"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.kenan.wallet"
        minSdk = 26
        targetSdk = 34
        versionCode = 2
        versionName = "0.2.0-devnet"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField("String", "KENAN_NODE_URL", kenanNodeUrl.asJavaStringLiteral())
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
}
dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.0")
    implementation("androidx.navigation:navigation-compose:2.7.7")
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("org.bouncycastle:bcprov-jdk18on:1.80.2")
    implementation("com.neovisionaries:nv-websocket-client:2.10")
    implementation("com.google.code.gson:gson:2.11.0")
    // The wallet uses its own BIP39 implementation; exclude NovaCrypto's GPL-3.0 mnemonic stack.
    implementation("io.github.nova-wallet.substrate-sdk-android:core:2.12.2") {
        exclude(group = "com.github.NovaCrypto", module = "BIP39")
        exclude(group = "com.github.NovaCrypto", module = "Sha256")
        exclude(group = "com.github.NovaCrypto", module = "ToRuntime")
        exclude(group = "io.github.novacrypto", module = "SHA256")
        exclude(group = "io.github.novacrypto", module = "ToRuntime")
    }
    implementation("com.google.zxing:core:3.5.3")
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}

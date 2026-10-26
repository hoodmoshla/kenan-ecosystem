plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }
val kenanNodeUrl = providers.gradleProperty("kenanNodeUrl").orElse("http://10.0.2.2:8545").get()
android {
    namespace = "com.kenan.wallet"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.kenan.wallet"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0-devnet"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    buildFeatures { compose = true; buildConfig = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    defaultConfig { buildConfigField("String", "KENAN_NODE_URL", "\"$kenanNodeUrl\"") }
}
dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.0")
    implementation("androidx.navigation:navigation-compose:2.7.7")
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("org.bouncycastle:bcprov-jdk18on:1.78.1")
    implementation("com.google.zxing:core:3.5.3")
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
}

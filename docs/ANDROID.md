# Android build (KenanWallet)

Source: android-app/ (Kotlin, Jetpack Compose, Material 3, RTL Arabic-first).
This snapshot is SOURCE ONLY in this environment — no JDK/Android SDK here.

To build on a developer machine:
1. Install Android Studio (JDK 17 bundled) + SDK 34.
2. Open android-app/, let Gradle sync (Compose BOM 2024.06.00, Kotlin 1.9.24).
3. Run the devnet node on the host; the emulator reaches it at 10.0.2.2:8545
   (NodeApi default). On a physical device, replace the base URL with the
   host LAN address and keep the network on a trusted network only.
4. `./gradlew :app:assembleDebug` — debug APK only. Release signing must
   use a keystore kept OUTSIDE the repo (see docs/OPERATIONS.md); never
   commit signing keys.

Still to implement before release: wallet create/restore UI wired to
AndroidKeyStore, QR receive, transaction history list, Shield pre-sign
screen, Intelligence provider settings with a disable switch, biometric
unlock, CSV export, accessibility pass on small screens.

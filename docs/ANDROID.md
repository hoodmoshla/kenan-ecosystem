# Android build (KenanWallet)

Source: android-app/ (Kotlin, Jetpack Compose, Material 3, RTL Arabic-first).
This snapshot is SOURCE ONLY in this environment — no JDK/Android SDK here.

## In-VM build attempt, 2026-10-26 — FAILED for an environment reason, not code
- JDK 17.0.20.1 installed via apt; Android command-line tools downloaded;
  platform-34 (ext12) and build-tools 34.0.0 installed manually by direct
  download, because `sdkmanager` fails in this VM's proxy (Java proxy
  tunnelling error). Gradle 8.7 installed.
- `gradle --version` works, but every `assembleDebug` build fails BEFORE
  dependency resolution: the single-use Gradle daemon starts, then the
  client fails with `DaemonConnectionException: Could not receive a
  message from the daemon` / `Unexpected type tag 109`. This happens
  with and without proxy settings, so it is a localhost-socket /
  sandbox limitation of this VM, not a Kenan source error.
- No APK file was produced or claimed. Do NOT re-run the same Gradle
  build in this VM expecting a different result.
- Realistic APK route: the GitHub runner workflow
  `.github/workflows/android.yml` (JDK 17, SDK setup, `assembleDebug`,
  upload APK artifact, debug only, no signing/publishing). It is blocked
  only by the same workflow-file upload permission as `ci.yml`.

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

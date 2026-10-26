# KenanWallet on Android — development guide

The app is Kotlin/Compose with RTL layout. It supports creation and restoration of the Python-reference 24-word BIP39 wallet, encrypted local storage, live balance reads, receive QR, and locally signed devnet transfers.

## Build and tests

Use Android SDK platform 34, JDK 17 or newer, and the repository Gradle wrapper:

```bash
cd android-app
./gradlew --no-daemon :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
```

Debug APK: `app/build/outputs/apk/debug/app-debug.apk`. This is a development APK signed with the standard Debug certificate, not a release artifact. Do not use that certificate for upgrades or distribution.

## Connect to the Python devnet

Start the node from the repository root:

```bash
python3 -m blockchain.node_server --host 127.0.0.1 --port 8545 --data data/chain.json
```

The Debug default is `http://10.0.2.2:8545`, the Android Emulator route to the host computer. A different reachable development endpoint can be supplied at build time:

```bash
cd android-app
./gradlew -PkenanNodeUrl=http://<devnet-host>:8545 :app:assembleDebug
```

HTTP cleartext is permitted only by `src/debug/AndroidManifest.xml`; the base manifest keeps it disabled. Do not point this Debug wallet at a public or untrusted network. A physical-device connection and a release HTTPS node are not verified here. The node defaults to loopback; faucet and empty-block mining are loopback-only unless the operator explicitly configures `KENAN_FAUCET_TOKEN` for a private network.

The wallet refuses to sign unless RPC identifies `kenan-devnet-1` and `devnet-test-only`. The transfer flow obtains the fee, balance and nonce from RPC, displays recipient/amount/fee/total, asks for an explicit confirmation, checks nonce/balance again, signs locally, submits once, then fetches the deterministic transaction hash and compares the confirmed fields. It does not automatically retry an ambiguous send.

## Wallet backup and storage

- New wallets use 24-word BIP39 English phrases matching `wallet-core/wallet.py`.
- The phrase is encrypted with AES-GCM; the AES key is generated and kept by Android Keystore. Only ciphertext and the public address are stored in app-private files. Android app backup is disabled so the encrypted file is not restored without its device-bound Keystore key.
- The phrase is shown to the user at creation and must be written down outside the app. It is never sent to the node. A restored phrase is validated before encrypted storage.
- Signing decrypts the phrase briefly in-process to derive the compatible Ed25519 key. This is local signing, not hardware-backed Ed25519 signing; the phrase/key can exist in process memory during use. No independent mobile security audit has been performed.

## What was and was not tested

Four JVM unit tests check the 24-word format, known reference address/public key/signature/hash, checksum failures and base-unit conversion. Python integration tests exercise the node’s actual HTTP transfer path. The Debug APK was built and its package/signature checked, but there was no connected emulator or physical device; Keystore behavior, UI interaction with a running node, QR readability and device accessibility remain unverified.

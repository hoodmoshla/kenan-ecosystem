package com.kenan.wallet.data

import com.google.gson.Gson
import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.neovisionaries.ws.client.WebSocket
import com.neovisionaries.ws.client.WebSocketAdapter
import com.neovisionaries.ws.client.WebSocketException
import com.neovisionaries.ws.client.WebSocketFactory
import com.neovisionaries.ws.client.WebSocketFrame
import io.novasama.substrate_sdk_android.encrypt.SignatureWrapper
import io.novasama.substrate_sdk_android.runtime.RuntimeSnapshot
import io.novasama.substrate_sdk_android.runtime.definitions.dynamic.DynamicTypeResolver
import io.novasama.substrate_sdk_android.runtime.definitions.registry.TypeRegistry
import io.novasama.substrate_sdk_android.runtime.definitions.registry.v14Preset
import io.novasama.substrate_sdk_android.runtime.definitions.types.composite.DictEnum
import io.novasama.substrate_sdk_android.runtime.definitions.types.composite.Struct
import io.novasama.substrate_sdk_android.runtime.definitions.types.fromHex
import io.novasama.substrate_sdk_android.runtime.definitions.types.generics.Era
import io.novasama.substrate_sdk_android.runtime.definitions.types.generics.Extrinsic
import io.novasama.substrate_sdk_android.runtime.definitions.types.generics.GenericEvent
import io.novasama.substrate_sdk_android.runtime.definitions.v14.TypesParserV14
import io.novasama.substrate_sdk_android.runtime.extrinsic.ExtrinsicBuilder
import io.novasama.substrate_sdk_android.runtime.extrinsic.ExtrinsicVersion
import io.novasama.substrate_sdk_android.runtime.extrinsic.call
import io.novasama.substrate_sdk_android.runtime.extrinsic.v5.transactionExtension.InheritedImplication
import io.novasama.substrate_sdk_android.runtime.extrinsic.v5.transactionExtension.extensions.verifySignature.GeneralTransactionSigner
import io.novasama.substrate_sdk_android.runtime.metadata.RuntimeMetadataReader
import io.novasama.substrate_sdk_android.runtime.metadata.callOrNull
import io.novasama.substrate_sdk_android.runtime.metadata.module
import io.novasama.substrate_sdk_android.runtime.metadata.storage
import io.novasama.substrate_sdk_android.runtime.metadata.storageKey
import io.novasama.substrate_sdk_android.runtime.metadata.storageOrNull
import io.novasama.substrate_sdk_android.runtime.metadata.builder.VersionedRuntimeBuilder
import io.novasama.substrate_sdk_android.wsrpc.request.runtime.chain.RuntimeVersion
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.bouncycastle.crypto.digests.Blake2bDigest
import java.io.IOException
import java.math.BigDecimal
import java.math.BigInteger
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

class SubstrateNodeApi(private val endpoint: String) {
    suspend fun inspectIdentity(): PolkadotNetworkIdentity = withContext(Dispatchers.IO) {
        RpcSession(endpoint).use { session ->
            session.connect()
            readContext(session).identity
        }
    }

    suspend fun account(accountId: ByteArray): PolkadotAccountState = withContext(Dispatchers.IO) {
        RpcSession(endpoint).use { session ->
            session.connect()
            val context = readContext(session)
            accountAt(session, context, accountId, context.identity.finalizedHash)
        }
    }

    suspend fun quoteTransfer(
        senderAccountId: ByteArray,
        recipientAccountId: ByteArray,
        recipientAddress: String,
        amountBase: Long
    ): PolkadotTransferQuote = withContext(Dispatchers.IO) {
        require(senderAccountId.size == 32 && recipientAccountId.size == 32)
        require(amountBase > 0)
        RpcSession(endpoint).use { session ->
            session.connect()
            val context = readContext(session)
            val current = accountAt(session, context, senderAccountId, context.identity.finalizedHash)
            val (era, checkpointHash, expiresAt) = eraCheckpoint(session, context.identity.finalizedHeight)
            val dummySigner = zeroSignatureSigner()
            val draft = buildTransfer(context, senderAccountId, recipientAccountId, amountBase, current.nonce, era, checkpointHash, dummySigner)
            val fee = queryFee(session, draft.extrinsicHex, context.identity.finalizedHash)
            PolkadotTransferQuote(
                recipient = recipientAddress,
                amountBase = amountBase,
                feeBase = fee,
                nonce = current.nonce,
                balanceBase = current.freeBalanceBase,
                identity = context.identity,
                expiresAtHeight = expiresAt
            )
        }
    }

    suspend fun sendTransfer(
        senderAccountId: ByteArray,
        recipientAccountId: ByteArray,
        quote: PolkadotTransferQuote,
        signer: GeneralTransactionSigner,
        onPrepared: (PolkadotPendingTransfer) -> Unit
    ): PolkadotTransaction = withContext(Dispatchers.IO) {
        require(senderAccountId.size == 32 && recipientAccountId.size == 32)
        var requestWasSent = false
        RpcSession(endpoint).use { session ->
            session.connect()
            val context = readContext(session)
            require(quote.identity.sameSigningIdentity(context.identity)) {
                "Chain identity or asset properties changed; review the transfer again"
            }
            val current = accountAt(session, context, senderAccountId, context.identity.finalizedHash)
            require(current.nonce == quote.nonce) { "Account nonce changed; review the transfer again" }
            require(current.freeBalanceBase == quote.balanceBase) { "Account balance changed; review the transfer again" }
            require(quote.amountBase <= Long.MAX_VALUE - quote.feeBase && current.freeBalanceBase >= quote.amountBase + quote.feeBase) {
                "Insufficient free balance for amount and estimated fee"
            }

            val (era, checkpointHash, expiresAt) = eraCheckpoint(session, context.identity.finalizedHeight)
            val draft = buildTransfer(context, senderAccountId, recipientAccountId, quote.amountBase, current.nonce, era, checkpointHash, zeroSignatureSigner())
            val feeNow = queryFee(session, draft.extrinsicHex, context.identity.finalizedHash)
            require(feeNow == quote.feeBase) { "Estimated network fee changed; review the transfer again" }

            val sendable = buildTransfer(context, senderAccountId, recipientAccountId, quote.amountBase, current.nonce, era, checkpointHash, signer)
            val extrinsicHex = normalizeHex(sendable.extrinsicHex)
            val txHash = blake2b256Hex(decodeHex(extrinsicHex))
            val pending = PolkadotPendingTransfer(
                hash = txHash,
                extrinsicHex = extrinsicHex,
                nonce = current.nonce,
                genesisHash = context.identity.genesisHash,
                submittedAtHeight = context.identity.finalizedHeight,
                expiresAtHeight = expiresAt
            )
            onPrepared(pending)

            val finalizedHash = try {
                session.submitAndWatch(extrinsicHex) { requestWasSent = true }
            } catch (rejected: SubstrateRpcError) {
                throw SubstrateRejectedException(rejected.message ?: "Node rejected the signed extrinsic", rejected)
            } catch (rejected: SubstrateRejectedException) {
                throw rejected
            } catch (error: Exception) {
                if (!requestWasSent) throw SubstrateNotSubmittedException(error)
                throw SubstrateAmbiguousSubmissionException(txHash, error)
            }

            val finalized = finalizedTransaction(
                session = session,
                context = context,
                finalizedHash = finalizedHash,
                expectedExtrinsicHex = extrinsicHex,
                expectedHash = txHash,
                senderAccountId = senderAccountId,
                recipientAccountId = recipientAccountId,
                amountBase = quote.amountBase,
                nonce = quote.nonce,
                balanceBefore = current.freeBalanceBase
            )
            finalized
        }
    }

    suspend fun recentTransactions(accountId: ByteArray, limit: Int = 8): List<PolkadotTransaction> = withContext(Dispatchers.IO) {
        require(accountId.size == 32)
        RpcSession(endpoint).use { session ->
            session.connect()
            val context = readContext(session)
            val head = context.identity.finalizedHeight
            val start = maxOf(0L, head - 31L)
            val found = mutableListOf<PolkadotTransaction>()
            for (height in head downTo start) {
                val blockHash = rpcString(session, "chain_getBlockHash", listOf(height))
                val block = session.call("chain_getBlock", listOf(blockHash)).asJsonObject.getAsJsonObject("block")
                val extrinsics = block.getAsJsonArray("extrinsics")
                val events = eventsAt(session, context, blockHash)
                for (index in extrinsics.size() - 1 downTo 0) {
                    val extHex = normalizeHex(extrinsics[index].asString)
                    val decoded = runCatching { Extrinsic.fromHex(context.runtime, extHex) }.getOrNull() ?: continue
                    val signed = decoded.type as? Extrinsic.ExtrinsicType.Signed ?: continue
                    val from = accountIdFromAddressValue(signed.accountIdentifier) ?: continue
                    val call = decoded.call
                    if (call.module.name != "Balances" || call.function.name !in TRANSFER_CALLS) continue
                    val to = accountIdFromAddressValue(call.arguments["dest"]) ?: continue
                    val amount = bigInteger(call.arguments["value"]).longValueExact()
                    if (!from.contentEquals(accountId) && !to.contentEquals(accountId)) continue
                    val phaseEvents = eventsForIndex(events, index)
                    if (!phaseEvents.any { it.module.name == "System" && it.event.name == "ExtrinsicSuccess" }) continue
                    val event = phaseEvents.firstOrNull { it.module.name == "Balances" && it.event.name == "Transfer" } ?: continue
                    if (!transferEventMatches(event, from, to, amount)) continue
                    found += PolkadotTransaction(
                        hash = blake2b256Hex(decodeHex(extHex)),
                        from = SubstrateAddressCodec.fromPublicKeyHex(toHex(from)),
                        to = SubstrateAddressCodec.fromPublicKeyHex(toHex(to)),
                        amountBase = amount,
                        nonce = 0,
                        blockHeight = height
                    )
                    if (found.size >= limit) break
                }
                if (found.size >= limit) break
            }
            found
        }
    }

    suspend fun resolvePending(pending: PolkadotPendingTransfer): PolkadotPendingResolution = withContext(Dispatchers.IO) {
        RpcSession(endpoint).use { session ->
            session.connect()
            val context = readContext(session)
            if (!context.identity.genesisHash.equals(pending.genesisHash, ignoreCase = true)) return@use PolkadotPendingResolution.Unknown
            val pool = session.call("author_pendingExtrinsics").asJsonArray
            if (pool.any { normalizeHex(it.asString) == pending.extrinsicHex }) return@use PolkadotPendingResolution.StillPending
            val start = maxOf(0L, pending.submittedAtHeight - 1)
            for (height in start..context.identity.finalizedHeight) {
                val blockHash = rpcString(session, "chain_getBlockHash", listOf(height))
                val block = session.call("chain_getBlock", listOf(blockHash)).asJsonObject.getAsJsonObject("block")
                val extrinsics = block.getAsJsonArray("extrinsics")
                for (index in 0 until extrinsics.size()) {
                    val extHex = normalizeHex(extrinsics[index].asString)
                    if (blake2b256Hex(decodeHex(extHex)) != pending.hash) continue
                    val events = eventsForIndex(eventsAt(session, context, blockHash), index)
                    if (events.any { it.module.name == "System" && it.event.name == "ExtrinsicSuccess" }) {
                        val decoded = Extrinsic.fromHex(context.runtime, extHex)
                        val signed = decoded.type as? Extrinsic.ExtrinsicType.Signed ?: return@use PolkadotPendingResolution.Unknown
                        val from = accountIdFromAddressValue(signed.accountIdentifier) ?: return@use PolkadotPendingResolution.Unknown
                        val to = accountIdFromAddressValue(decoded.call.arguments["dest"]) ?: return@use PolkadotPendingResolution.Unknown
                        val amount = bigInteger(decoded.call.arguments["value"]).longValueExact()
                        return@use PolkadotPendingResolution.Finalized(
                            PolkadotTransaction(pending.hash, SubstrateAddressCodec.fromPublicKeyHex(toHex(from)), SubstrateAddressCodec.fromPublicKeyHex(toHex(to)), amount, pending.nonce, height)
                        )
                    }
                    if (events.any { it.module.name == "System" && it.event.name == "ExtrinsicFailed" }) return@use PolkadotPendingResolution.Rejected
                }
            }
            if (context.identity.finalizedHeight >= pending.expiresAtHeight) PolkadotPendingResolution.ExpiredNotIncluded
            else PolkadotPendingResolution.Unknown
        }
    }

    private fun readContext(session: RpcSession): ChainContext {
        val chainName = rpcString(session, "system_chain")
        val systemName = rpcString(session, "system_name")
        val systemVersion = rpcString(session, "system_version")
        val genesisHash = rpcString(session, "chain_getBlockHash", listOf(0))
        val finalizedHash = rpcString(session, "chain_getFinalizedHead")
        val finalizedHeader = session.call("chain_getHeader", listOf(finalizedHash)).asJsonObject
        val finalizedHeight = parseHexOrDecimal(finalizedHeader.get("number"), "finalized header number").longValueExact()
        val runtimeVersionJson = session.call("state_getRuntimeVersion").asJsonObject
        val specName = runtimeVersionJson.get("specName")?.asString ?: ""
        val specVersion = parseHexOrDecimal(runtimeVersionJson.get("specVersion"), "runtime specVersion").intValueExact()
        val transactionVersion = parseHexOrDecimal(runtimeVersionJson.get("transactionVersion"), "runtime transactionVersion").intValueExact()
        val properties = session.call("system_properties").asJsonObject
        val officialTemplate = systemName == TEMPLATE_NODE_NAME && specName == TEMPLATE_RUNTIME_NAME
        val tokenSymbol = propertyString(properties.get("tokenSymbol")).ifBlank {
            if (officialTemplate) TEMPLATE_TOKEN_SYMBOL else ""
        }
        val tokenDecimalsValue = propertyFirst(properties.get("tokenDecimals"))
        val tokenDecimals = if (tokenDecimalsValue == null && officialTemplate) TEMPLATE_TOKEN_DECIMALS
        else parseHexOrDecimal(tokenDecimalsValue, "system_properties.tokenDecimals").intValueExact()
        require(chainName.isNotBlank() && systemName.isNotBlank() && specName.isNotBlank()) { "Node identity is incomplete" }
        require(genesisHash.matches(Regex("0x[0-9a-fA-F]{64}"))) { "Invalid genesis hash from RPC" }
        require(tokenSymbol.isNotBlank() && tokenDecimals in 0..18) { "Unsupported token properties from RPC" }
        val metadataHex = rpcString(session, "state_getMetadata")
        val reader = RuntimeMetadataReader.read(metadataHex)
        require(reader.metadataVersion >= 14) { "Runtime metadata older than v14 is unsupported" }
        val typePreset = TypesParserV14.parse(reader.lookup, v14Preset())
        val registry = TypeRegistry(typePreset, DynamicTypeResolver.defaultCompoundResolver())
        val runtime = RuntimeSnapshot(registry, VersionedRuntimeBuilder.buildMetadata(reader, registry))
        val balances = runtime.metadata.module("Balances")
        require(balances.callOrNull("transfer_keep_alive") != null) { "Runtime does not expose Balances.transfer_keep_alive" }
        require(runtime.metadata.module("System").storageOrNull("Account") != null) { "Runtime metadata has no System.Account storage" }
        require(runtime.metadata.module("System").storageOrNull("Events") != null) { "Runtime metadata has no System.Events storage" }
        val identity = PolkadotNetworkIdentity(
            chainName = chainName,
            systemName = systemName,
            systemVersion = systemVersion,
            genesisHash = normalizeHex(genesisHash),
            specName = specName,
            specVersion = specVersion,
            transactionVersion = transactionVersion,
            tokenSymbol = tokenSymbol,
            tokenDecimals = tokenDecimals,
            finalizedHash = normalizeHex(finalizedHash),
            finalizedHeight = finalizedHeight
        )
        return ChainContext(identity, runtime, RuntimeVersion(specVersion, transactionVersion))
    }

    private fun accountAt(session: RpcSession, context: ChainContext, accountId: ByteArray, atHash: String): PolkadotAccountState {
        val storage = context.runtime.metadata.module("System").storage("Account")
        val key = storage.storageKey(context.runtime, accountId)
        val value = session.call("state_getStorage", listOf(key, atHash))
        if (value.isJsonNull) return PolkadotAccountState("0x${toHex(accountId)}", 0L, 0L, 0L)
        val valueType = storage.type.value ?: error("System.Account storage type is unresolved")
        val account = valueType.fromHex(context.runtime, value.asString) as? Struct.Instance
            ?: error("System.Account did not decode to a struct")
        val data = account.mapping["data"] as? Struct.Instance ?: error("System.Account.data did not decode")
        val nonce = bigInteger(account.mapping["nonce"]).longValueExact()
        val free = bigInteger(data.mapping["free"]).longValueExact()
        val reserved = bigInteger(data.mapping["reserved"] ?: BigInteger.ZERO).longValueExact()
        return PolkadotAccountState("0x${toHex(accountId)}", free, reserved, nonce)
    }

    private fun eraCheckpoint(session: RpcSession, currentHeight: Long): EraCheckpoint {
        val height = currentHeight.coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
        val (period, phase) = Era.getPeriodPhaseFromBlockPeriod(height, MORTAL_PERIOD)
        val birthBlock = (height - phase).coerceAtLeast(0)
        val checkpointHash = rpcString(session, "chain_getBlockHash", listOf(birthBlock))
        return EraCheckpoint(Era.Mortal(period, phase), normalizeHex(checkpointHash), birthBlock.toLong() + period)
    }

    private suspend fun buildTransfer(
        context: ChainContext,
        sender: ByteArray,
        recipient: ByteArray,
        amount: Long,
        nonce: Long,
        era: Era.Mortal,
        checkpointHash: String,
        signer: GeneralTransactionSigner
    ): io.novasama.substrate_sdk_android.runtime.extrinsic.signer.SendableExtrinsic {
        val builder = ExtrinsicBuilder(
            runtime = context.runtime,
            nonce = BigInteger.valueOf(nonce),
            runtimeVersion = context.runtimeVersion,
            genesisHash = decodeHex(context.identity.genesisHash),
            accountId = sender,
            signer = signer,
            blockHash = decodeHex(checkpointHash),
            era = era,
            extrinsicVersion = ExtrinsicVersion.V4
        )
        return builder.call(
            moduleName = "Balances",
            callName = "transfer_keep_alive",
            arguments = mapOf(
                "dest" to DictEnum.Entry(name = "Id", value = recipient),
                "value" to BigInteger.valueOf(amount)
            )
        ).buildExtrinsic()
    }

    private suspend fun queryFee(session: RpcSession, extrinsicHex: String, atHash: String): Long {
        val result = session.call("payment_queryInfo", listOf(normalizeHex(extrinsicHex), atHash)).asJsonObject
        val partialFee = parseHexOrDecimal(result.get("partialFee"), "payment_queryInfo.partialFee").longValueExact()
        require(partialFee >= 0) { "Runtime returned a negative fee" }
        return partialFee
    }

    private fun zeroSignatureSigner() = object : GeneralTransactionSigner {
        override suspend fun signInheritedImplication(
            inheritedImplication: InheritedImplication,
            accountId: ByteArray
        ): SignatureWrapper = SignatureWrapper.Ed25519(ByteArray(64))
    }

    private fun finalizedTransaction(
        session: RpcSession,
        context: ChainContext,
        finalizedHash: String,
        expectedExtrinsicHex: String,
        expectedHash: String,
        senderAccountId: ByteArray,
        recipientAccountId: ByteArray,
        amountBase: Long,
        nonce: Long,
        balanceBefore: Long
    ): PolkadotTransaction {
        val block = session.call("chain_getBlock", listOf(finalizedHash)).asJsonObject.getAsJsonObject("block")
        val extrinsics = block.getAsJsonArray("extrinsics")
        val index = (0 until extrinsics.size()).firstOrNull { normalizeHex(extrinsics[it].asString) == expectedExtrinsicHex }
            ?: error("Finalized block does not contain the submitted SCALE extrinsic")
        val header = session.call("chain_getHeader", listOf(finalizedHash)).asJsonObject
        val blockHeight = parseHexOrDecimal(header.get("number"), "finalized block header number").longValueExact()
        val phaseEvents = eventsForIndex(eventsAt(session, context, finalizedHash), index)
        require(phaseEvents.any { it.module.name == "System" && it.event.name == "ExtrinsicSuccess" }) {
            "Finalized transfer did not emit System.ExtrinsicSuccess"
        }
        val transferEvent = phaseEvents.firstOrNull { it.module.name == "Balances" && it.event.name == "Transfer" }
            ?: error("Finalized transfer did not emit Balances.Transfer")
        require(transferEventMatches(transferEvent, senderAccountId, recipientAccountId, amountBase)) {
            "Finalized Balances.Transfer event differs from reviewed sender, recipient, or amount"
        }
        val balanceAfter = accountAt(session, context, senderAccountId, finalizedHash).freeBalanceBase
        val eventFee = phaseEvents.firstOrNull { it.module.name == "TransactionPayment" && it.event.name == "TransactionFeePaid" }
            ?.event?.arguments?.getOrNull(1)?.let { runCatching { bigInteger(it).longValueExact() }.getOrNull() }
        val actualFee = eventFee ?: (balanceBefore - balanceAfter - amountBase)
        require(actualFee >= 0 && balanceBefore - balanceAfter == amountBase + actualFee) {
            "Finalized sender balance delta does not reconcile to the transfer and fee"
        }
        val includedHash = blake2b256Hex(decodeHex(expectedExtrinsicHex))
        require(includedHash == expectedHash) { "Finalized extrinsic hash does not match the prepared transaction" }
        return PolkadotTransaction(
            hash = includedHash,
            from = SubstrateAddressCodec.fromPublicKeyHex(toHex(senderAccountId)),
            to = SubstrateAddressCodec.fromPublicKeyHex(toHex(recipientAccountId)),
            amountBase = amountBase,
            nonce = nonce,
            blockHeight = blockHeight,
            actualFeeBase = actualFee
        )
    }

    private fun eventsAt(session: RpcSession, context: ChainContext, blockHash: String): List<GenericEventRecord> {
        val eventsStorage = context.runtime.metadata.module("System").storage("Events")
        val eventsKey = eventsStorage.storageKey()
        val value = session.call("state_getStorage", listOf(eventsKey, blockHash))
        if (value.isJsonNull) return emptyList()
        val valueType = eventsStorage.type.value ?: error("System.Events storage type is unresolved")
        val records = valueType.fromHex(context.runtime, value.asString) as? List<*>
            ?: error("System.Events did not decode to a vector")
        return records.mapNotNull { record ->
            val struct = record as? Struct.Instance ?: return@mapNotNull null
            val phase = struct.mapping["phase"] as? DictEnum.Entry<*> ?: return@mapNotNull null
            val event = struct.mapping["event"] as? GenericEvent.Instance ?: return@mapNotNull null
            GenericEventRecord(phase.name, phase.value, event)
        }
    }

    private fun eventsForIndex(events: List<GenericEventRecord>, index: Int): List<GenericEvent.Instance> = events
        .filter { it.phaseName == "ApplyExtrinsic" && runCatching { bigInteger(it.phaseValue).intValueExact() == index }.getOrDefault(false) }
        .map { it.event }

    private fun transferEventMatches(event: GenericEvent.Instance, from: ByteArray, to: ByteArray, amount: Long): Boolean {
        val eventFrom = event.arguments.getOrNull(0) as? ByteArray ?: return false
        val eventTo = event.arguments.getOrNull(1) as? ByteArray ?: return false
        val eventAmount = runCatching { bigInteger(event.arguments.getOrNull(2)).longValueExact() }.getOrNull() ?: return false
        return eventFrom.contentEquals(from) && eventTo.contentEquals(to) && eventAmount == amount
    }

    private fun accountIdFromAddressValue(value: Any?): ByteArray? = when (value) {
        is ByteArray -> value.takeIf { it.size == 32 }
        is DictEnum.Entry<*> -> if (value.name == "Id") (value.value as? ByteArray)?.takeIf { it.size == 32 } else null
        else -> null
    }

    private fun propertyFirst(value: JsonElement?): JsonElement? =
        if (value == null || value.isJsonNull) null else if (value.isJsonArray) value.asJsonArray.firstOrNull() else value

    private fun propertyString(value: JsonElement?): String =
        propertyFirst(value)?.let { if (it.isJsonPrimitive) it.asString else "" }.orEmpty()

    private fun rpcString(session: RpcSession, method: String, params: List<Any?> = emptyList()): String {
        val result = session.call(method, params)
        require(result.isJsonPrimitive && !result.asJsonPrimitive.isBoolean) { "$method returned a non-string result" }
        return result.asString
    }

    private fun parseHexOrDecimal(element: JsonElement?, fieldName: String): BigInteger {
        require(element != null && !element.isJsonNull) { "Missing numeric RPC field: $fieldName" }
        val raw = if (element.isJsonPrimitive) element.asString else error("$fieldName is not a primitive numeric RPC value")
        return if (raw.startsWith("0x", ignoreCase = true)) BigInteger(raw.drop(2), 16) else BigDecimal(raw).toBigIntegerExact()
    }

    private fun bigInteger(value: Any?): BigInteger = when (value) {
        is BigInteger -> value
        is BigDecimal -> value.toBigIntegerExact()
        is Byte, is Short, is Int, is Long -> BigInteger.valueOf((value as Number).toLong())
        is UInt -> BigInteger(value.toString())
        is ULong -> BigInteger(value.toString())
        else -> BigInteger(value?.toString() ?: error("Missing SCALE integer"))
    }

    private fun normalizeHex(value: String): String = if (value.startsWith("0x", true)) "0x" + value.drop(2).lowercase() else "0x" + value.lowercase()

    private fun decodeHex(value: String): ByteArray {
        val hex = value.removePrefix("0x").removePrefix("0X")
        require(hex.length % 2 == 0 && hex.all { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' }) { "Invalid RPC hex" }
        return ByteArray(hex.length / 2) { index -> hex.substring(index * 2, index * 2 + 2).toInt(16).toByte() }
    }

    private fun toHex(value: ByteArray): String = value.joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun blake2b256Hex(bytes: ByteArray): String {
        val digest = Blake2bDigest(256)
        digest.update(bytes, 0, bytes.size)
        val result = ByteArray(32)
        digest.doFinal(result, 0)
        return "0x${toHex(result)}"
    }

    private data class ChainContext(
        val identity: PolkadotNetworkIdentity,
        val runtime: RuntimeSnapshot,
        val runtimeVersion: RuntimeVersion
    )

    private data class EraCheckpoint(val era: Era.Mortal, val checkpointHash: String, val expiresAt: Long)
    private data class GenericEventRecord(val phaseName: String, val phaseValue: Any?, val event: GenericEvent.Instance)

    private inner class RpcSession(private val url: String) : AutoCloseable {
        private val gson = Gson()
        private val nextId = AtomicLong(0)
        private val pending = ConcurrentHashMap<Long, CompletableFuture<JsonElement>>()
        private val notifications = LinkedBlockingQueue<JsonObject>()
        private val closed = AtomicBoolean(false)
        private lateinit var socket: WebSocket

        fun connect() {
            try {
                socket = WebSocketFactory().createSocket(url, RPC_CONNECT_TIMEOUT_MS)
                socket.addListener(object : WebSocketAdapter() {
                    override fun onTextMessage(websocket: WebSocket, text: String) {
                        val message = try { JsonParser().parse(text).asJsonObject } catch (_: Exception) { return }
                        val id = message.get("id")?.takeUnless { it.isJsonNull }?.let { runCatching { it.asLong }.getOrNull() }
                        if (id != null) {
                            val future = pending.remove(id) ?: return
                            val error = message.get("error")
                            if (error != null && !error.isJsonNull) future.completeExceptionally(SubstrateRpcError(error.toString()))
                            else future.complete(message.get("result") ?: com.google.gson.JsonNull.INSTANCE)
                        } else {
                            notifications.offer(message)
                        }
                    }

                    override fun onDisconnected(
                        websocket: WebSocket,
                        serverCloseFrame: WebSocketFrame?,
                        clientCloseFrame: WebSocketFrame?,
                        closedByServer: Boolean
                    ) {
                        closed.set(true)
                        pending.values.forEach { it.completeExceptionally(IOException("WebSocket disconnected")) }
                        pending.clear()
                    }

                    override fun onError(websocket: WebSocket, cause: WebSocketException) {
                        if (!websocket.isOpen) closed.set(true)
                        pending.values.forEach { it.completeExceptionally(cause) }
                        pending.clear()
                    }
                })
                socket.connect()
            } catch (error: Exception) {
                closed.set(true)
                throw IOException("Could not connect to local Substrate RPC", error)
            }
        }

        fun call(method: String, params: List<Any?> = emptyList(), onSent: (() -> Unit)? = null): JsonElement {
            check(!closed.get()) { "Substrate RPC WebSocket is closed" }
            val id = nextId.incrementAndGet()
            val future = CompletableFuture<JsonElement>()
            pending[id] = future
            val request = JsonObject().apply {
                addProperty("jsonrpc", "2.0")
                addProperty("id", id)
                addProperty("method", method)
                add("params", gson.toJsonTree(params))
            }
            try {
                socket.sendText(gson.toJson(request))
                onSent?.invoke()
            } catch (error: Exception) {
                pending.remove(id)
                throw IOException("Failed to write Substrate RPC request", error)
            }
            return try {
                future.get(RPC_REQUEST_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            } catch (error: java.util.concurrent.ExecutionException) {
                val cause = error.cause ?: error
                if (cause is SubstrateRpcError) throw cause
                throw IOException("Substrate RPC request failed", cause)
            } catch (error: Exception) {
                pending.remove(id)
                throw IOException("Substrate RPC request timed out or disconnected", error)
            }
        }

        fun submitAndWatch(extrinsicHex: String, onSent: () -> Unit): String {
            val subscription = call("author_submitAndWatchExtrinsic", listOf(extrinsicHex), onSent)
            val subscriptionId = subscription.asString
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(RPC_FINALITY_TIMEOUT_SECONDS)
            while (true) {
                val remaining = deadline - System.nanoTime()
                if (remaining <= 0) throw IOException("Timed out waiting for finalized transaction status")
                val message = notifications.poll(remaining, TimeUnit.NANOSECONDS)
                    ?: throw IOException("Timed out waiting for finalized transaction status")
                val params = message.getAsJsonObject("params") ?: continue
                if (params.get("subscription")?.asString != subscriptionId) continue
                val status = params.get("result")?.takeIf { it.isJsonObject }?.asJsonObject ?: continue
                when {
                    status.has("finalized") -> {
                        val hash = status.get("finalized").asString
                        runCatching { call("author_unwatchExtrinsic", listOf(subscriptionId)) }
                        return normalizeHex(hash)
                    }
                    status.has("invalid") || status.has("dropped") || status.has("usurped") || status.has("finalityTimeout") -> {
                        throw SubstrateRejectedException("Node reported terminal extrinsic status: $status")
                    }
                }
            }
        }

        override fun close() {
            if (::socket.isInitialized) runCatching { socket.disconnect() }
            closed.set(true)
        }
    }

    class SubstrateRpcError(message: String) : IOException(message)
    class SubstrateNotSubmittedException(cause: Throwable) : IOException("The transaction was not submitted to the node", cause)
    class SubstrateRejectedException(message: String, cause: Throwable? = null) : IOException(message, cause)
    class SubstrateAmbiguousSubmissionException(val txHash: String, cause: Throwable) : IOException("Transaction submission outcome is unknown; do not resend", cause)

    companion object {
        private const val RPC_CONNECT_TIMEOUT_MS = 10_000
        private const val RPC_REQUEST_TIMEOUT_SECONDS = 20L
        private const val RPC_FINALITY_TIMEOUT_SECONDS = 90L
        private const val MORTAL_PERIOD = 64
        private const val TEMPLATE_NODE_NAME = "solochain-template-node"
        private const val TEMPLATE_RUNTIME_NAME = "solochain-template-runtime"
        private const val TEMPLATE_TOKEN_SYMBOL = "UNIT"
        private const val TEMPLATE_TOKEN_DECIMALS = 12
        private val TRANSFER_CALLS = setOf("transfer", "transfer_allow_death", "transfer_keep_alive")
    }
}

package com.kenan.wallet.data

import org.json.JSONObject

data class PolkadotNetworkIdentity(
    val chainName: String,
    val systemName: String,
    val systemVersion: String,
    val genesisHash: String,
    val specName: String,
    val specVersion: Int,
    val transactionVersion: Int,
    val tokenSymbol: String,
    val tokenDecimals: Int,
    val finalizedHash: String,
    val finalizedHeight: Long
)

fun PolkadotNetworkIdentity.sameSigningIdentity(other: PolkadotNetworkIdentity): Boolean =
    chainName == other.chainName && systemName == other.systemName &&
        genesisHash.equals(other.genesisHash, ignoreCase = true) && specName == other.specName &&
        specVersion == other.specVersion && transactionVersion == other.transactionVersion &&
        tokenSymbol == other.tokenSymbol && tokenDecimals == other.tokenDecimals

data class PolkadotChainPin(
    val chainName: String,
    val systemName: String,
    val genesisHash: String,
    val specName: String,
    val tokenSymbol: String,
    val tokenDecimals: Int
) {
    fun matches(identity: PolkadotNetworkIdentity): Boolean =
        chainName == identity.chainName &&
            systemName == identity.systemName &&
            genesisHash.equals(identity.genesisHash, ignoreCase = true) &&
            specName == identity.specName &&
            tokenSymbol == identity.tokenSymbol &&
            tokenDecimals == identity.tokenDecimals

    companion object {
        fun from(identity: PolkadotNetworkIdentity) = PolkadotChainPin(
            chainName = identity.chainName,
            systemName = identity.systemName,
            genesisHash = identity.genesisHash.lowercase(),
            specName = identity.specName,
            tokenSymbol = identity.tokenSymbol,
            tokenDecimals = identity.tokenDecimals
        )

        fun fromJson(value: String?): PolkadotChainPin? = runCatching {
            val json = JSONObject(value ?: return null)
            PolkadotChainPin(
                chainName = json.getString("chainName"),
                systemName = json.getString("systemName"),
                genesisHash = json.getString("genesisHash"),
                specName = json.getString("specName"),
                tokenSymbol = json.getString("tokenSymbol"),
                tokenDecimals = json.getInt("tokenDecimals")
            )
        }.getOrNull()
    }

    fun toJson(): String = JSONObject()
        .put("chainName", chainName)
        .put("systemName", systemName)
        .put("genesisHash", genesisHash.lowercase())
        .put("specName", specName)
        .put("tokenSymbol", tokenSymbol)
        .put("tokenDecimals", tokenDecimals)
        .toString()
}

data class PolkadotAccountState(
    val accountIdHex: String,
    val freeBalanceBase: Long,
    val reservedBalanceBase: Long,
    val nonce: Long
)

data class PolkadotTransferQuote(
    val recipient: String,
    val amountBase: Long,
    val feeBase: Long,
    val nonce: Long,
    val balanceBase: Long,
    val identity: PolkadotNetworkIdentity,
    val expiresAtHeight: Long
)

data class PolkadotPendingTransfer(
    val hash: String,
    val extrinsicHex: String,
    val nonce: Long,
    val genesisHash: String,
    val submittedAtHeight: Long,
    val expiresAtHeight: Long
) {
    fun toJson(): String = JSONObject()
        .put("hash", hash)
        .put("extrinsicHex", extrinsicHex)
        .put("nonce", nonce)
        .put("genesisHash", genesisHash)
        .put("submittedAtHeight", submittedAtHeight)
        .put("expiresAtHeight", expiresAtHeight)
        .toString()

    companion object {
        fun fromJson(value: String?): PolkadotPendingTransfer? = runCatching {
            val json = JSONObject(value ?: return null)
            PolkadotPendingTransfer(
                hash = json.getString("hash"),
                extrinsicHex = json.getString("extrinsicHex"),
                nonce = json.getLong("nonce"),
                genesisHash = json.getString("genesisHash"),
                submittedAtHeight = json.getLong("submittedAtHeight"),
                expiresAtHeight = json.getLong("expiresAtHeight")
            )
        }.getOrNull()
    }
}

data class PolkadotTransaction(
    val hash: String,
    val from: String,
    val to: String,
    val amountBase: Long,
    val nonce: Long,
    val blockHeight: Long,
    val actualFeeBase: Long? = null
)

sealed class PolkadotPendingResolution {
    data class Finalized(val transaction: PolkadotTransaction) : PolkadotPendingResolution()
    data object Rejected : PolkadotPendingResolution()
    data object StillPending : PolkadotPendingResolution()
    data object Unknown : PolkadotPendingResolution()
    data object ExpiredNotIncluded : PolkadotPendingResolution()
}

const { ApiPromise, WsProvider } = require('@polkadot/api');
const { Keyring } = require('@polkadot/keyring');
const { cryptoWaitReady, encodeAddress } = require('@polkadot/util-crypto');
const { hexToU8a } = require('@polkadot/util');

const endpoint = process.env.POLKADOT_RPC_URL || 'ws://127.0.0.1:9944';
const publicKeyHex = '0x1de352e44cd333672593f2334a730e180aaf290de89aa16d480de594e34e2961';

async function main() {
  await cryptoWaitReady();
  const api = await ApiPromise.create({ provider: new WsProvider(endpoint) });
  try {
    const props = await api.rpc.system.properties();
    const decimalsJson = props.tokenDecimals.toJSON();
    const decimals = Number(Array.isArray(decimalsJson) ? (decimalsJson[0] ?? 12) : (decimalsJson ?? 12));
    if (!Number.isInteger(decimals) || decimals < 0 || decimals > 18) throw new Error(`unsupported decimals: ${decimals}`);
    const target = encodeAddress(hexToU8a(publicKeyHex), 42);
    const unit = 10n ** BigInt(decimals);
    const funding = 4n * unit; // stays within signed Long even for 18 decimals
    const keyring = new Keyring({ type: 'sr25519', ss58Format: 42 });
    const alice = keyring.addFromUri('//Alice');

    await new Promise((resolve, reject) => {
      let unsubscribe;
      api.tx.balances.transferKeepAlive(target, funding.toString()).signAndSend(alice, ({ status, dispatchError }) => {
        if (dispatchError) {
          const message = dispatchError.isModule
            ? api.registry.findMetaError(dispatchError.asModule).docs.join(' ')
            : dispatchError.toString();
          unsubscribe?.();
          reject(new Error(`test-account funding failed: ${message}`));
          return;
        }
        if (status.isFinalized) {
          unsubscribe?.();
          resolve(status.asFinalized.toHex());
        }
      }).then((stop) => { unsubscribe = stop; }).catch(reject);
    });

    const account = await api.query.system.account(target);
    if (account.data.free.toBigInt() < funding) throw new Error('test account balance did not reach the expected funding amount');
    console.log(`Funded isolated Android integration-test account at ${target}; decimals=${decimals}`);
  } finally {
    await api.disconnect();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exitCode = 1;
});

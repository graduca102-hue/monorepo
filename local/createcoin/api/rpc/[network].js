import {
  getServerRpcUrl,
  readRawBody,
  relayUpstreamResponse,
  rejectCrossSite,
  sendJson,
  takeRateLimit,
} from '../_serverUtil.js';

export const config = {
  api: {
    bodyParser: false,
  },
};

const ALLOWED_RPC_METHODS = new Set([
  'getAccountInfo',
  'getBalance',
  'getBlock',
  'getBlockHeight',
  'getBlockTime',
  'getBlocks',
  'getEpochInfo',
  'getFeeForMessage',
  'getGenesisHash',
  'getHealth',
  'getIdentity',
  'getLatestBlockhash',
  'getLatestBlockhashAndContext',
  'getMinimumBalanceForRentExemption',
  'getMultipleAccounts',
  'getParsedAccountInfo',
  'getParsedTokenAccountsByOwner',
  'getParsedTransaction',
  'getProgramAccounts',
  'getRecentPerformanceSamples',
  'getRecentPrioritizationFees',
  'getSignatureStatuses',
  'getSignaturesForAddress',
  'getSlot',
  'getSupply',
  'getTokenAccountBalance',
  'getTokenAccountsByOwner',
  'getTokenLargestAccounts',
  'getTokenSupply',
  'getTransaction',
  'getTransactionCount',
  'getVersion',
  'isBlockhashValid',
  'sendTransaction',
  'simulateTransaction',
]);

const HEAVY_RPC_METHODS = new Set([
  'getProgramAccounts',
  'getTokenAccountsByOwner',
  'getParsedTokenAccountsByOwner',
  'getMultipleAccounts',
  'getSignaturesForAddress',
]);

const TX_RPC_METHODS = new Set(['sendTransaction', 'simulateTransaction']);
const MAX_RPC_BATCH_ITEMS = 20;
const MAX_RPC_BODY_BYTES = 256 * 1024;

function getConfigObject(row, index = 1) {
  if (!Array.isArray(row.params)) return null;
  const value = row.params[index];
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

function validateGetProgramAccounts(row) {
  const config = getConfigObject(row, 1);
  const filters = Array.isArray(config?.filters) ? config.filters : null;
  if (!config || !filters || filters.length === 0 || filters.length > 8) {
    throw new Error('getProgramAccounts requires 1-8 filters');
  }
  if (config.withContext === true) {
    throw new Error('getProgramAccounts withContext is not allowed');
  }
  if (config.dataSlice && typeof config.dataSlice === 'object') {
    const length = Number(config.dataSlice.length ?? 0);
    if (!Number.isFinite(length) || length < 0 || length > 512) {
      throw new Error('getProgramAccounts dataSlice.length must be between 0 and 512');
    }
  }
}

function validateGetMultipleAccounts(row) {
  const keys = Array.isArray(row.params?.[0]) ? row.params[0] : null;
  if (!keys || keys.length === 0 || keys.length > 100) {
    throw new Error('getMultipleAccounts supports 1-100 addresses per call');
  }
}

function validateGetSignaturesForAddress(row) {
  const config = getConfigObject(row, 1);
  const limit = Number(config?.limit ?? 1000);
  if (!Number.isFinite(limit) || limit < 1 || limit > 100) {
    throw new Error('getSignaturesForAddress limit must be between 1 and 100');
  }
}

function validateRpcRows(rows) {
  for (const row of rows) {
    switch (row.method) {
      case 'getProgramAccounts':
        validateGetProgramAccounts(row);
        break;
      case 'getMultipleAccounts':
        validateGetMultipleAccounts(row);
        break;
      case 'getSignaturesForAddress':
        validateGetSignaturesForAddress(row);
        break;
      default:
        break;
    }
  }
}

function extractRpcMethods(payload) {
  const rows = Array.isArray(payload) ? payload : [payload];
  if (rows.length === 0 || rows.length > MAX_RPC_BATCH_ITEMS) {
    throw new Error(`RPC batch must contain between 1 and ${MAX_RPC_BATCH_ITEMS} items`);
  }
  const methods = [];

  for (const row of rows) {
    if (!row || typeof row !== 'object' || typeof row.method !== 'string') {
      throw new Error('Invalid JSON-RPC payload');
    }
    methods.push(row.method);
  }

  return methods;
}

function rateLimitCost(methods) {
  return methods.reduce((sum, method) => {
    if (TX_RPC_METHODS.has(method)) return sum + 20;
    if (HEAVY_RPC_METHODS.has(method)) return sum + 10;
    return sum + 1;
  }, 0);
}

export default async function rpcProxy(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return sendJson(res, 405, { error: 'method_not_allowed' });
  }
  if (rejectCrossSite(req, res)) return;

  let rawBody;
  let payload;
  let methods;

  try {
    rawBody = await readRawBody(req, MAX_RPC_BODY_BYTES);
    payload = JSON.parse(rawBody.toString('utf8'));
    methods = extractRpcMethods(payload);
    validateRpcRows(Array.isArray(payload) ? payload : [payload]);
  } catch (error) {
    return sendJson(res, 400, {
      error: 'invalid_rpc_payload',
      message: error instanceof Error ? error.message : 'Could not parse JSON-RPC body',
    });
  }

  for (const method of methods) {
    if (!ALLOWED_RPC_METHODS.has(method)) {
      return sendJson(res, 403, { error: 'rpc_method_blocked', method });
    }
  }

  const limited = takeRateLimit(req, `rpc:${req.query.network}`, {
    limit: 1200,
    windowMs: 60_000,
    cost: rateLimitCost(methods),
  });
  if (!limited.ok) {
    res.setHeader('Retry-After', String(limited.retryAfterSeconds));
    return sendJson(res, 429, { error: 'rate_limited', retryAfter: limited.retryAfterSeconds });
  }

  let upstreamUrl;
  try {
    upstreamUrl = getServerRpcUrl(req.query.network);
  } catch (error) {
    return sendJson(res, 500, {
      error: 'rpc_not_configured',
      message: error instanceof Error ? error.message : 'RPC is not configured',
    });
  }

  try {
    const upstream = await fetch(upstreamUrl, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: rawBody,
      redirect: 'follow',
    });
    return await relayUpstreamResponse(res, upstream);
  } catch {
    return sendJson(res, 502, { error: 'rpc_upstream_failed' });
  }
}

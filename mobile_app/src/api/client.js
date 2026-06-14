import AsyncStorage from '@react-native-async-storage/async-storage';
import { API_BASE_URL } from '../config';

const CACHE_KEYS = {
  status:    'cache_status',
  trades:    'cache_trades',
  positions: 'cache_positions',
  activity:  'cache_activity',
  pnlHistory:'cache_pnl_history',
  detective: 'cache_detective',
};

async function fetchJSON(path, opts = {}) {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...opts.headers },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

// Fetch with offline fallback to cached value
async function fetchWithCache(path, cacheKey, ttlMs = 30_000) {
  try {
    const data = await fetchJSON(path);
    await AsyncStorage.setItem(cacheKey, JSON.stringify({ data, ts: Date.now() }));
    return { data, fromCache: false };
  } catch (err) {
    const raw = await AsyncStorage.getItem(cacheKey);
    if (raw) {
      const { data } = JSON.parse(raw);
      return { data, fromCache: true };
    }
    throw err;
  }
}

export async function getStatus()     { return fetchWithCache('/api/status',      CACHE_KEYS.status);    }
export async function getTrades()     { return fetchWithCache('/api/trades',       CACHE_KEYS.trades);    }
export async function getPositions()  { return fetchWithCache('/api/positions',    CACHE_KEYS.positions); }
export async function getActivity()   { return fetchWithCache('/api/activity',     CACHE_KEYS.activity);  }
export async function getPnlHistory() { return fetchWithCache('/api/pnl-history',  CACHE_KEYS.pnlHistory);}
export async function getDetective()  { return fetchWithCache('/api/detective',    CACHE_KEYS.detective); }
export async function getKalshi()     { return fetchWithCache('/api/platforms/kalshi',  'cache_kalshi');  }
export async function getCrypto()     { return fetchWithCache('/api/platforms/crypto',  'cache_crypto');  }

export async function postReset() {
  return fetchJSON('/reset', { method: 'POST' });
}

export async function postGoLive() {
  return fetchJSON('/golive', { method: 'POST' });
}

export async function triggerDetectiveScan() {
  return fetchJSON('/api/detective/scan', { method: 'POST' });
}

// Pre-warm cache from AsyncStorage on app start
export async function loadCachedAll() {
  const result = {};
  for (const [key, storageKey] of Object.entries(CACHE_KEYS)) {
    try {
      const raw = await AsyncStorage.getItem(storageKey);
      if (raw) result[key] = JSON.parse(raw).data;
    } catch {}
  }
  return result;
}

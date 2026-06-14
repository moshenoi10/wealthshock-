export const C = {
  bg:      '#000000',  // true OLED black
  surface: '#0d0d0d',
  card:    '#111111',
  border:  '#222222',
  green:   '#00ff99',
  red:     '#ff3333',
  blue:    '#4a90ff',
  yellow:  '#ffcc00',
  text:    '#ffffff',
  text2:   '#cccccc',
  text3:   '#888888',
  text4:   '#555555',
};

export const F = {
  mono: Platform.select({ ios: 'Courier New', android: 'monospace' }),
  sans: Platform.select({ ios: 'SF Pro Display', android: 'Roboto' }),
};

import { Platform } from 'react-native';

export const shadow = {
  shadowColor: C.green,
  shadowOffset: { width: 0, height: 2 },
  shadowOpacity: 0.08,
  shadowRadius: 8,
  elevation: 4,
};

export function pnlColor(val) {
  if (val > 0) return C.green;
  if (val < 0) return C.red;
  return C.text3;
}

export function fmtUSD(val, decimals = 2) {
  const abs = Math.abs(val);
  const sign = val >= 0 ? '' : '-';
  return `${sign}$${abs.toFixed(decimals)}`;
}

export function fmtPct(val, decimals = 1) {
  return `${(val * 100).toFixed(decimals)}%`;
}

export function fmtTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch { return '—'; }
}

import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  View, Text, ScrollView, StyleSheet, RefreshControl,
  TouchableOpacity, Alert, Animated, AppState,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { C, pnlColor, fmtUSD, fmtPct, fmtTime } from '../theme';
import MetricCard from '../components/MetricCard';
import PnLChart from '../components/PnLChart';
import { getStatus, getPnlHistory, getTrades, postReset, loadCachedAll } from '../api/client';
import { POLL_INTERVAL_MS, SLOW_POLL_MS } from '../config';

const STRATEGY_HE = {
  near_resolution: 'קרוב לפקיעה',
  pure_arbitrage: 'ארביטראז׳ טהור',
  directional_arb: 'ארביטראז׳ כיווני',
  repricing: 'תמחור מחדש',
  cross_timeframe: 'רב-זמני',
  copy_trading: 'העתקת עסקאות',
  exploit_new_market: '🔍 שוק חדש',
  exploit_liquidity_trap: '🪤 מלכודת נזילות',
};

export default function DashboardScreen() {
  const insets = useSafeAreaInsets();
  const [status, setStatus] = useState(null);
  const [pnlHistory, setPnlHistory] = useState([]);
  const [winRate, setWinRate] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [fromCache, setFromCache] = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);
  const prevBalance = useRef(null);
  const balanceAnim = useRef(new Animated.Value(1)).current;
  const timerRef = useRef(null);
  const appState = useRef(AppState.currentState);

  const flashBalance = useCallback((newBal) => {
    if (prevBalance.current !== null && prevBalance.current !== newBal) {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      Animated.sequence([
        Animated.timing(balanceAnim, { toValue: 1.04, duration: 150, useNativeDriver: true }),
        Animated.timing(balanceAnim, { toValue: 1, duration: 200, useNativeDriver: true }),
      ]).start();
    }
    prevBalance.current = newBal;
  }, [balanceAnim]);

  const refresh = useCallback(async (isManual = false) => {
    try {
      const [sRes, hRes, tRes] = await Promise.all([
        getStatus(), getPnlHistory(), getTrades(),
      ]);
      const s = sRes.data;
      setStatus(s);
      setFromCache(sRes.fromCache || hRes.fromCache);
      setLastUpdate(new Date());
      flashBalance(s?.balance);

      const hist = hRes.data || [];
      setPnlHistory(hist);

      const closed = (tRes.data || []).filter(t => t.pnl != null);
      const wins   = closed.filter(t => t.pnl > 0).length;
      setWinRate(closed.length ? wins / closed.length : 0);
    } catch (e) {
      // silent fail — cached data stays on screen
    } finally {
      if (isManual) setRefreshing(false);
    }
  }, [flashBalance]);

  // Start / stop polling based on AppState
  useEffect(() => {
    const start = () => {
      refresh();
      timerRef.current = setInterval(refresh, POLL_INTERVAL_MS);
    };
    const stop = () => clearInterval(timerRef.current);
    start();
    const sub = AppState.addEventListener('change', next => {
      if (next === 'active') start();
      else stop();
      appState.current = next;
    });
    return () => { stop(); sub.remove(); };
  }, [refresh]);

  // Load cache immediately on mount
  useEffect(() => {
    loadCachedAll().then(cached => {
      if (cached.status) setStatus(cached.status);
      if (cached.pnlHistory) setPnlHistory(cached.pnlHistory);
    });
  }, []);

  const handleReset = async () => {
    Alert.alert(
      'איפוס יתרה',
      'יתרה תחזור ל-$18.00 והסוכן יתחדש. להמשיך?',
      [
        { text: 'ביטול', style: 'cancel' },
        {
          text: 'אפס', style: 'destructive',
          onPress: async () => {
            try {
              Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
              await postReset();
              await refresh();
              Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
            } catch (e) {
              Alert.alert('שגיאה', e.message);
            }
          },
        },
      ]
    );
  };

  const s = status || {};
  const totalPnl = s.total_pnl ?? 0;
  const dailyPnl = s.daily_pnl ?? 0;
  const bal      = s.balance ?? 0;
  const isPaused = s.is_paused ?? false;

  const stratStats = Object.entries(s.strategy_stats || {}).filter(([, v]) => v.trades > 0);

  return (
    <ScrollView
      style={styles.scroll}
      contentContainerStyle={[styles.container, { paddingTop: insets.top + 8 }]}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => { setRefreshing(true); refresh(true); }}
          tintColor={C.green}
        />
      }
    >
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.appName}>WealthShock</Text>
        <View style={styles.headerRight}>
          {fromCache && <Text style={styles.cacheTag}>מטמון</Text>}
          <View style={[styles.modeBadge, { backgroundColor: s.mode === 'live' ? C.red : '#1a2a1a' }]}>
            <Text style={[styles.modeText, { color: s.mode === 'live' ? '#fff' : C.green }]}>
              {s.mode === 'live' ? 'חי' : 'הדמיה'}
            </Text>
          </View>
          {isPaused && (
            <TouchableOpacity style={styles.resetBtn} onPress={handleReset}>
              <Text style={styles.resetBtnText}>↺ איפוס</Text>
            </TouchableOpacity>
          )}
        </View>
      </View>

      {/* Balance Card */}
      <Animated.View style={[styles.balanceCard, { transform: [{ scale: balanceAnim }] }]}>
        <Text style={styles.balanceLabel}>יתרה</Text>
        <Text style={styles.balanceValue}>${bal.toFixed(2)}</Text>
        <View style={styles.pnlRow}>
          <Text style={[styles.pnlVal, { color: pnlColor(totalPnl) }]}>
            {totalPnl >= 0 ? '+' : ''}{fmtUSD(totalPnl, 4)} כולל
          </Text>
          <Text style={[styles.pnlVal, { color: pnlColor(dailyPnl) }]}>
            {dailyPnl >= 0 ? '+' : ''}{fmtUSD(dailyPnl, 4)} היום
          </Text>
        </View>
        {isPaused && (
          <View style={styles.pausedBanner}>
            <Text style={styles.pausedText}>⏸ מושהה — לחץ "איפוס" לחידוש</Text>
          </View>
        )}
      </Animated.View>

      {/* Metrics row */}
      <View style={styles.metricRow}>
        <MetricCard label="% הצלחה" value={fmtPct(winRate)} small
          valueStyle={{ color: winRate >= 0.5 ? C.green : C.red }} />
        <View style={styles.spacer} />
        <MetricCard label="עסקאות היום" value={s.trades_today ?? 0} small
          valueStyle={{ color: C.text }} />
        <View style={styles.spacer} />
        <MetricCard label="פוזיציות פתוחות" value={s.open_positions ?? 0} small
          valueStyle={{ color: s.open_positions > 0 ? C.blue : C.text3 }} />
      </View>

      {/* PnL Chart */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>ביצועי יתרה</Text>
        <PnLChart data={pnlHistory} />
      </View>

      {/* Strategy breakdown */}
      {stratStats.length > 0 && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>אסטרטגיות פעילות</Text>
          {stratStats.map(([name, v]) => {
            const wr = v.trades > 0 ? v.wins / v.trades : 0;
            return (
              <View key={name} style={styles.stratRow}>
                <Text style={styles.stratName}>{STRATEGY_HE[name] || name}</Text>
                <View style={styles.stratBar}>
                  <View style={[styles.stratFill, {
                    width: `${Math.min(wr * 100, 100)}%`,
                    backgroundColor: v.pnl >= 0 ? C.green : C.red,
                  }]} />
                </View>
                <Text style={styles.stratStats}>{v.trades} | {(wr * 100).toFixed(0)}%</Text>
                <Text style={[styles.stratPnl, { color: pnlColor(v.pnl) }]}>
                  {v.pnl >= 0 ? '+' : ''}{v.pnl.toFixed(4)}
                </Text>
              </View>
            );
          })}
        </View>
      )}

      {/* System health */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>בריאות המערכת</Text>
        <View style={styles.healthRow}>
          {[
            { label: 'לולאה', value: `#${(s.loop_count || 0).toLocaleString()}` },
            { label: 'שגיאות', value: s.errors_today ?? 0, highlight: s.errors_today > 0 },
            { label: 'סוכן', value: isPaused ? '⏸' : s.mode === 'live' ? '● חי' : '○ נייר',
              highlight: !isPaused },
          ].map(h => (
            <View key={h.label} style={styles.healthItem}>
              <Text style={styles.healthLabel}>{h.label}</Text>
              <Text style={[styles.healthVal, h.highlight && { color: isPaused ? C.yellow : C.green }]}>
                {String(h.value)}
              </Text>
            </View>
          ))}
        </View>
        {lastUpdate && (
          <Text style={styles.updateTs}>עודכן: {fmtTime(lastUpdate.toISOString())}</Text>
        )}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll:   { flex: 1, backgroundColor: C.bg },
  container:{ paddingHorizontal: 16, paddingBottom: 24 },

  header:       { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 },
  appName:      { fontSize: 20, fontWeight: '700', color: C.text },
  headerRight:  { flexDirection: 'row', alignItems: 'center', gap: 8 },
  cacheTag:     { fontSize: 10, color: C.yellow, backgroundColor: '#2a2400', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4 },
  modeBadge:    { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 4 },
  modeText:     { fontSize: 12, fontWeight: '600' },
  resetBtn:     { backgroundColor: 'rgba(255,204,0,0.15)', borderWidth: 1, borderColor: C.yellow, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 4 },
  resetBtnText: { color: C.yellow, fontSize: 12, fontWeight: '600' },

  balanceCard: {
    backgroundColor: '#0a0a0a',
    borderWidth: 1,
    borderColor: C.green,
    borderRadius: 8,
    padding: 24,
    marginBottom: 16,
    shadowColor: C.green,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.15,
    shadowRadius: 12,
    elevation: 8,
  },
  balanceLabel: { fontSize: 13, color: C.text3, textAlign: 'right', marginBottom: 4 },
  balanceValue: { fontSize: 52, fontWeight: '800', color: C.text, textAlign: 'right', letterSpacing: -1 },
  pnlRow:       { flexDirection: 'row', justifyContent: 'flex-end', gap: 16, marginTop: 8 },
  pnlVal:       { fontSize: 14, fontFamily: 'Courier New' },
  pausedBanner: { backgroundColor: 'rgba(255,204,0,0.1)', borderRadius: 4, padding: 8, marginTop: 12 },
  pausedText:   { color: C.yellow, fontSize: 13, textAlign: 'center' },

  metricRow: { flexDirection: 'row', marginBottom: 16 },
  spacer:    { width: 8 },

  card:      { backgroundColor: C.card, borderWidth: 1, borderColor: C.border, borderRadius: 6, padding: 16, marginBottom: 12 },
  cardTitle: { fontSize: 13, color: C.text3, marginBottom: 12, textAlign: 'right' },

  stratRow:   { flexDirection: 'row', alignItems: 'center', marginBottom: 10, gap: 8 },
  stratName:  { fontSize: 11, color: C.text2, width: 100, textAlign: 'right' },
  stratBar:   { flex: 1, height: 4, backgroundColor: '#222', borderRadius: 2, overflow: 'hidden' },
  stratFill:  { height: '100%', borderRadius: 2 },
  stratStats: { fontSize: 11, color: C.text3, width: 48, textAlign: 'right', fontFamily: 'Courier New' },
  stratPnl:   { fontSize: 11, width: 60, textAlign: 'right', fontFamily: 'Courier New' },

  healthRow:   { flexDirection: 'row', justifyContent: 'space-around' },
  healthItem:  { alignItems: 'center' },
  healthLabel: { fontSize: 11, color: C.text4, marginBottom: 4 },
  healthVal:   { fontSize: 16, fontWeight: '600', color: C.text, fontFamily: 'Courier New' },
  updateTs:    { fontSize: 10, color: C.text4, textAlign: 'center', marginTop: 10 },
});

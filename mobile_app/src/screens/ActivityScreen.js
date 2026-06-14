import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  View, Text, FlatList, StyleSheet, RefreshControl,
  TouchableOpacity, Modal, Pressable,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { C, fmtTime } from '../theme';
import { getActivity } from '../api/client';
import { POLL_INTERVAL_MS } from '../config';

const TYPE_STYLE = {
  trade: { color: C.green,  icon: '💰' },
  error: { color: C.red,    icon: '⚠️' },
  loop:  { color: C.text3,  icon: '○'  },
};

export default function ActivityScreen() {
  const insets = useSafeAreaInsets();
  const [events, setEvents] = useState([]);
  const [selected, setSelected] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const prevCount = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const r = await getActivity();
      const data = r.data || [];
      if (data.length > prevCount.current && prevCount.current > 0) {
        // New events arrived
        const newest = data[0];
        if (newest?.type === 'trade') {
          Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        } else if (newest?.type === 'error') {
          Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
        }
      }
      prevCount.current = data.length;
      setEvents(data);
    } catch {}
    setRefreshing(false);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const renderItem = ({ item: e }) => {
    const ts = TYPE_STYLE[e.type] || { color: C.text4, icon: '·' };
    return (
      <TouchableOpacity
        style={styles.row}
        onPress={() => { setSelected(e); Haptics.selectionAsync(); }}
      >
        <Text style={styles.rowIcon}>{ts.icon}</Text>
        <View style={styles.rowBody}>
          <Text style={[styles.rowMsg, { color: ts.color }]} numberOfLines={2}>
            {e.msg}
          </Text>
          <Text style={styles.rowTs}>{fmtTime(e.ts)}</Text>
        </View>
        <View style={[styles.typeBadge, { backgroundColor: `${ts.color}18` }]}>
          <Text style={[styles.typeTxt, { color: ts.color }]}>{e.type}</Text>
        </View>
      </TouchableOpacity>
    );
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Text style={styles.title}>פעילות חיה</Text>
        <View style={styles.countBadge}>
          <Text style={styles.countTxt}>{events.filter(e => e.type === 'trade').length} עסקאות</Text>
        </View>
      </View>

      <FlatList
        data={events}
        keyExtractor={(e, i) => `${e.ts}-${i}`}
        renderItem={renderItem}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); refresh(); }}
            tintColor={C.green}
          />
        }
        ListEmptyComponent={<Text style={styles.empty}>אין אירועים עדיין</Text>}
        contentContainerStyle={{ paddingBottom: 24 }}
      />

      {/* Detail Modal */}
      <Modal visible={!!selected} transparent animationType="slide">
        <Pressable style={styles.overlay} onPress={() => setSelected(null)}>
          <View style={styles.modal}>
            {selected && (
              <>
                <Text style={styles.modalType}>{TYPE_STYLE[selected.type]?.icon} {selected.type}</Text>
                <Text style={styles.modalMsg}>{selected.msg}</Text>
                <Text style={styles.modalTs}>{fmtTime(selected.ts)}</Text>
                <TouchableOpacity style={styles.closeBtn} onPress={() => setSelected(null)}>
                  <Text style={styles.closeTxt}>סגור</Text>
                </TouchableOpacity>
              </>
            )}
          </View>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },

  header:     { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 16, borderBottomWidth: 1, borderColor: C.border },
  title:      { fontSize: 18, fontWeight: '700', color: C.text },
  countBadge: { backgroundColor: 'rgba(0,255,153,0.1)', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  countTxt:   { fontSize: 12, color: C.green },

  row:      { flexDirection: 'row', alignItems: 'flex-start', padding: 14, borderBottomWidth: 1, borderColor: '#111', gap: 10 },
  rowIcon:  { fontSize: 16, width: 24, textAlign: 'center', marginTop: 1 },
  rowBody:  { flex: 1 },
  rowMsg:   { fontSize: 13, lineHeight: 18, textAlign: 'right' },
  rowTs:    { fontSize: 10, color: C.text4, marginTop: 4, textAlign: 'right', fontFamily: 'Courier New' },
  typeBadge:{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, height: 20, justifyContent: 'center' },
  typeTxt:  { fontSize: 9, fontWeight: '600' },

  overlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.7)', justifyContent: 'flex-end' },
  modal:   { backgroundColor: '#0d0d0d', borderTopLeftRadius: 16, borderTopRightRadius: 16, padding: 24, borderTopWidth: 1, borderColor: C.border },
  modalType:{ fontSize: 14, color: C.text3, marginBottom: 8, textAlign: 'right' },
  modalMsg: { fontSize: 15, color: C.text, textAlign: 'right', lineHeight: 22, marginBottom: 12 },
  modalTs:  { fontSize: 12, color: C.text4, textAlign: 'right', fontFamily: 'Courier New', marginBottom: 20 },
  closeBtn: { backgroundColor: C.border, borderRadius: 8, padding: 14, alignItems: 'center' },
  closeTxt: { color: C.text, fontWeight: '600' },

  empty: { color: C.text4, textAlign: 'center', marginTop: 60, fontSize: 14 },
});

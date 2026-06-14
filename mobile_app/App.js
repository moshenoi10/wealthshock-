import React from 'react';
import { StatusBar } from 'expo-status-bar';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { Text, View } from 'react-native';
import { C } from './src/theme';

import DashboardScreen  from './src/screens/DashboardScreen';
import PositionsScreen  from './src/screens/PositionsScreen';
import DetectiveScreen  from './src/screens/DetectiveScreen';
import ActivityScreen   from './src/screens/ActivityScreen';
import SettingsScreen   from './src/screens/SettingsScreen';

const Tab = createBottomTabNavigator();

const TAB_ICON = {
  Dashboard: '📊',
  Positions: '📈',
  Detective: '🔍',
  Activity:  '⚡',
  Settings:  '⚙️',
};

const TAB_HE = {
  Dashboard: 'דשבורד',
  Positions: 'פוזיציות',
  Detective: 'בלש',
  Activity:  'פעילות',
  Settings:  'הגדרות',
};

export default function App() {
  return (
    <SafeAreaProvider>
      <NavigationContainer theme={{ dark: true, colors: {
        primary: C.green,
        background: C.bg,
        card: '#0d0d0d',
        text: C.text,
        border: C.border,
        notification: C.green,
      }}}>
        <StatusBar style="light" backgroundColor={C.bg} />
        <Tab.Navigator
          screenOptions={({ route }) => ({
            headerShown: false,
            tabBarStyle: {
              backgroundColor: '#080808',
              borderTopColor: C.border,
              borderTopWidth: 1,
              height: 60,
              paddingBottom: 8,
              paddingTop: 6,
            },
            tabBarActiveTintColor:   C.green,
            tabBarInactiveTintColor: C.text4,
            tabBarLabelStyle: { fontSize: 10, marginTop: -2 },
            tabBarIcon: ({ color, size }) => (
              <Text style={{ fontSize: 18, color }}>{TAB_ICON[route.name]}</Text>
            ),
            tabBarLabel: TAB_HE[route.name] || route.name,
          })}
        >
          <Tab.Screen name="Dashboard"  component={DashboardScreen}  />
          <Tab.Screen name="Positions"  component={PositionsScreen}  />
          <Tab.Screen name="Detective"  component={DetectiveScreen}  />
          <Tab.Screen name="Activity"   component={ActivityScreen}   />
          <Tab.Screen name="Settings"   component={SettingsScreen}   />
        </Tab.Navigator>
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

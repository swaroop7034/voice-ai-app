import { Ionicons } from "@expo/vector-icons";
import { Audio } from "expo-av";
import { BlurView } from "expo-blur";
import * as Haptics from "expo-haptics";
import { LinearGradient } from "expo-linear-gradient";
import React, { useEffect, useState } from "react";
import {
  Dimensions,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
} from "react-native-reanimated";

const { width, height } = Dimensions.get("window");

type ArisStatus = "IDLE" | "LISTENING" | "THINKING";

export default function HomeScreen() {
  const [status, setStatus] = useState<ArisStatus>("IDLE");
  const [recording, setRecording] = useState<Audio.Recording | null>(null);
  const [metering, setMetering] = useState<number[]>(new Array(9).fill(-160));

  // Modal Visibility States
  const [historyVisible, setHistoryVisible] = useState(false);
  const [settingsVisible, setSettingsVisible] = useState(false);

  // Animation Values
  const rotation = useSharedValue(0);
  const pulse = useSharedValue(1);

  useEffect(() => {
    rotation.value = withRepeat(
      withTiming(360, { duration: 12000, easing: Easing.linear }),
      -1,
      false,
    );
  }, []);

  // --- RECORDING LOGIC ---
  async function startRecording() {
    try {
      if (recording) {
        await recording.stopAndUnloadAsync();
        setRecording(null);
      }

      const { granted } = await Audio.requestPermissionsAsync();
      if (!granted) return;

      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
      });

      const { recording: newRecording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY,
        (status) => {
          if (status.metering !== undefined) {
            setMetering((prev) => [...prev.slice(1), status.metering!]);
          }
        },
        100,
      );

      setRecording(newRecording);
      setStatus("LISTENING");
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
    } catch (err) {
      console.error("Failed to start recording", err);
      setRecording(null);
    }
  }

  async function stopRecording() {
    if (!recording) return;
    try {
      setStatus("THINKING");
      await recording.stopAndUnloadAsync();
      const uri = recording.getURI();
      setRecording(null);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      console.log("Recording ready at:", uri);
      // Backend upload logic would go here
      setTimeout(() => setStatus("IDLE"), 2000); // Simulate processing
    } catch (error) {
      console.error("Error stopping recording", error);
      setRecording(null);
      setStatus("IDLE");
    }
  }

  // --- UI STYLES ---
  const ringStyle = (offset: number) =>
    useAnimatedStyle(() => ({
      transform: [
        { rotateZ: `${rotation.value * offset}deg` },
        { scale: pulse.value },
      ],
    }));

  const COLORS = {
    IDLE: ["#00d4ff", "#0055ff", "transparent"],
    LISTENING: ["#00ffaa", "#00cc88", "transparent"],
    THINKING: ["#bf5af2", "#5e5ce6", "transparent"],
  };

  // Reusable Modal Component for UI consistency
  const ArisModal = ({ visible, onClose, title, children }: any) => (
    <Modal visible={visible} animationType="slide" transparent={true}>
      <BlurView intensity={80} tint="dark" style={styles.modalOverlay}>
        <View style={styles.modalContent}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>{title}</Text>
            <TouchableOpacity onPress={onClose}>
              <Ionicons name="close" size={28} color="#fff" />
            </TouchableOpacity>
          </View>
          <ScrollView>{children}</ScrollView>
        </View>
      </BlurView>
    </Modal>
  );

  return (
    <View style={styles.container}>
      {/* 1. HISTORY MODAL */}
      <ArisModal
        visible={historyVisible}
        onClose={() => setHistoryVisible(false)}
        title="HISTORY"
      >
        <Text style={styles.placeholderText}>
          Recent commands will appear here...
        </Text>
      </ArisModal>

      {/* 2. SETTINGS MODAL */}
      <ArisModal
        visible={settingsVisible}
        onClose={() => setSettingsVisible(false)}
        title="SETTINGS"
      >
        <View style={styles.settingItem}>
          <Text style={styles.settingText}>Voice Sensitivity</Text>
          <Text style={styles.settingSub}>High</Text>
        </View>
        <View style={styles.settingItem}>
          <Text style={styles.settingText}>System Status</Text>
          <Text style={[styles.settingSub, { color: "#00ffaa" }]}>Online</Text>
        </View>
      </ArisModal>

      <View style={styles.textContainer}>
        <Text style={styles.subText}>ARIS SYSTEM • ACTIVE</Text>
        <Text style={styles.mainQuestion}>
          {status === "IDLE"
            ? "How can I help you?"
            : status === "LISTENING"
              ? "I’m listening..."
              : "One moment..."}
        </Text>
      </View>

      <View style={styles.orbContainer}>
        <Animated.View style={[styles.ring, ringStyle(1)]}>
          <LinearGradient
            colors={COLORS[status] as any}
            style={styles.gradient}
          />
        </Animated.View>
        <Animated.View style={[styles.ring, styles.ringSmall, ringStyle(-1.2)]}>
          <LinearGradient
            colors={COLORS[status] as any}
            style={styles.gradient}
          />
        </Animated.View>
        <Animated.View style={[styles.ring, styles.ringLarge, ringStyle(0.8)]}>
          <LinearGradient
            colors={COLORS[status] as any}
            style={styles.gradient}
          />
        </Animated.View>
      </View>

      <View style={styles.waveformContainer}>
        {status === "LISTENING" &&
          metering.map((level, i) => {
            const barHeight = Math.max(6, (level + 160) / 3);
            return (
              <View
                key={i}
                style={[
                  styles.waveLine,
                  { backgroundColor: "#00ffaa", height: barHeight },
                ]}
              />
            );
          })}
      </View>

      <View style={styles.bottomSection}>
        <TouchableOpacity
          style={styles.iconButton}
          onPress={() => setHistoryVisible(true)}
        >
          <Ionicons name="time-outline" size={22} color="#555" />
        </TouchableOpacity>

        <TouchableOpacity
          activeOpacity={1}
          onPressIn={startRecording}
          onPressOut={stopRecording}
          style={[styles.mainButton, { borderColor: COLORS[status][0] }]}
        >
          <BlurView intensity={20} tint="dark" style={styles.blurContainer}>
            <Text style={[styles.buttonText, { color: COLORS[status][0] }]}>
              {status === "LISTENING" ? "RELEASE TO SEND" : "HOLD TO COMMAND"}
            </Text>
          </BlurView>
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.iconButton}
          onPress={() => setSettingsVisible(true)}
        >
          <Ionicons name="settings-outline" size={22} color="#555" />
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#000",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 80,
  },
  textContainer: { alignItems: "center", zIndex: 10 },
  subText: {
    color: "#00d4ff",
    fontSize: 10,
    letterSpacing: 4,
    fontWeight: "bold",
    marginBottom: 10,
  },
  mainQuestion: { color: "#fff", fontSize: 26, fontWeight: "300" },
  orbContainer: {
    width: 300,
    height: 300,
    alignItems: "center",
    justifyContent: "center",
  },
  ring: {
    position: "absolute",
    width: 200,
    height: 200,
    borderRadius: 100,
    borderWidth: 0.5,
    borderColor: "rgba(255,255,255,0.05)",
  },
  ringSmall: { width: 140, height: 140, borderRadius: 70 },
  ringLarge: { width: 260, height: 260, borderRadius: 130 },
  gradient: { flex: 1, borderRadius: 130, opacity: 0.45 },
  waveformContainer: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    height: 60,
    justifyContent: "center",
  },
  waveLine: { width: 3, borderRadius: 2 },
  bottomSection: {
    flexDirection: "row",
    alignItems: "center",
    width: "100%",
    paddingHorizontal: 25,
    gap: 15,
  },
  mainButton: {
    flex: 1,
    height: 64,
    borderRadius: 32,
    borderWidth: 1,
    overflow: "hidden",
  },
  blurContainer: { flex: 1, alignItems: "center", justifyContent: "center" },
  buttonText: { fontWeight: "bold", fontSize: 12, letterSpacing: 2 },
  iconButton: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: "rgba(255,255,255,0.03)",
    alignItems: "center",
    justifyContent: "center",
  },

  // Modal Styles
  modalOverlay: { flex: 1, justifyContent: "flex-end" },
  modalContent: {
    height: height * 0.7,
    backgroundColor: "rgba(0,0,0,0.9)",
    borderTopLeftRadius: 30,
    borderTopRightRadius: 30,
    padding: 30,
  },
  modalHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 30,
  },
  modalTitle: {
    color: "#00d4ff",
    fontSize: 14,
    letterSpacing: 4,
    fontWeight: "bold",
  },
  placeholderText: {
    color: "#444",
    fontSize: 16,
    textAlign: "center",
    marginTop: 100,
  },
  settingItem: {
    paddingVertical: 20,
    borderBottomWidth: 0.5,
    borderBottomColor: "#222",
  },
  settingText: { color: "#fff", fontSize: 18 },
  settingSub: { color: "#555", fontSize: 14, marginTop: 4 },
});

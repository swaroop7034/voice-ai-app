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
  Alert,
} from "react-native";
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
  withSpring,
} from "react-native-reanimated";

const { width, height } = Dimensions.get("window");

// --- CONFIGURATION ---
// IMPORTANT: Replace with your Laptop's IPv4 address (found via 'ipconfig')
const BACKEND_URL = "http://10.11.24.6:8000/chat"; 

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
  const pulse = useSharedValue(0); // 0 to 1 based on microphone volume

  useEffect(() => {
    rotation.value = withRepeat(
      withTiming(360, { duration: 25000, easing: Easing.linear }),
      -1,
      false,
    );
  }, []);

  // --- RECORDING & UPLOAD LOGIC ---
  async function startRecording() {
    try {
      const { granted } = await Audio.requestPermissionsAsync();
      if (!granted) {
        Alert.alert("Permission Error", "Microphone access is required!");
        return;
      }

      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
      });

      const { recording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY,
        (status) => {
          if (status.metering !== undefined) {
            setMetering((prev) => [...prev.slice(1), status.metering!]);
            // Map metering (-60 to 0) to a 0-1 pulse scale
            const vol = Math.max(0, (status.metering + 60) / 60);
            pulse.value = withSpring(vol, { damping: 15, stiffness: 100 });
          }
        },
        100
      );
      
      setRecording(recording);
      setStatus("LISTENING");
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    } catch (err) {
      console.error("Failed to start recording", err);
    }
  }

  async function stopRecording() {
    if (!recording) return;

    try {
      setStatus("THINKING");
      await recording.stopAndUnloadAsync();
      const uri = recording.getURI(); 
      setRecording(null);
      pulse.value = withSpring(0); // Reset visualizer

      if (uri) {
        console.log("Captured audio at:", uri);
        
        // --- STEP 1: SEND TO BACKEND ---
        const formData = new FormData();
        formData.append('file', {
          uri: uri,
          name: 'voice_input.m4a',
          type: 'audio/m4a',
        } as any);

        try {
          const response = await fetch(BACKEND_URL, {
            method: 'POST',
            body: formData,
            headers: { 'Content-Type': 'multipart/form-data' },
          });

          const data = await response.json();
          
          if (data.status === "success" || data.text) {
             Alert.alert("Aries Recognized", data.text || "Command Processed");
          }
        } catch (fetchError) {
          Alert.alert("Connection Error", "Check your backend server and IP address.");
          console.error(fetchError);
        }
      }

      setStatus("IDLE");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (error) {
      console.error("Failed to stop recording", error);
      setStatus("IDLE");
    }
  }

  // --- NEBULA CORE STYLES ---
  const ringStyle = (speedMultiplier: number, baseScale: number) =>
    useAnimatedStyle(() => ({
      transform: [
        { rotateZ: `${rotation.value * speedMultiplier}deg` },
        { scale: baseScale + pulse.value * 0.2 },
      ],
      opacity: status === "LISTENING" ? 0.7 : 0.4,
    }));

  const COLORS = {
    IDLE: ["#00d4ff", "#0055ff", "transparent"],
    LISTENING: ["#00ffaa", "#00cc88", "transparent"],
    THINKING: ["#ff2d55", "#800020", "transparent"],
  };

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
      <ArisModal visible={historyVisible} onClose={() => setHistoryVisible(false)} title="HISTORY">
        <Text style={styles.placeholderText}>Recent commands will appear here...</Text>
      </ArisModal>

      <ArisModal visible={settingsVisible} onClose={() => setSettingsVisible(false)} title="SETTINGS">
        <View style={styles.settingItem}>
          <Text style={styles.settingText}>System Mode</Text>
          <Text style={[styles.settingSub, { color: "#00ffaa" }]}>Nebula V4 (Voice Reactive)</Text>
        </View>
        <View style={styles.settingItem}>
          <Text style={styles.settingText}>Backend Target</Text>
          <Text style={styles.settingSub}>{BACKEND_URL}</Text>
        </View>
      </ArisModal>

      <View style={styles.textContainer}>
        <Text style={styles.subText}>ARIES NEBULA • ACTIVE</Text>
        <Text style={styles.mainQuestion}>
          {status === "IDLE" ? "How can I help you?" : status === "LISTENING" ? "Listening..." : "Synthesizing..."}
        </Text>
      </View>

      <View style={styles.orbContainer}>
        {/* Outer Cyan Smoke */}
        <Animated.View style={[styles.ring, styles.ringLarge, ringStyle(0.5, 1)]}>
          <LinearGradient colors={COLORS[status] as any} style={styles.gradient} />
        </Animated.View>
        
        {/* Inner Shifting Energy */}
        <Animated.View style={[styles.ring, ringStyle(-0.8, 0.7)]}>
          <LinearGradient colors={COLORS[status] as any} style={styles.gradient} />
        </Animated.View>

        {/* The Reactive Core */}
        <Animated.View style={[styles.ring, styles.ringSmall, ringStyle(1.2, 0.5)]}>
          <LinearGradient colors={["#ff2d55", "#800020"]} style={styles.gradient} />
        </Animated.View>
      </View>

      <View style={styles.waveformContainer}>
        {status === "LISTENING" &&
          metering.map((level, i) => (
            <View
              key={i}
              style={[
                styles.waveLine,
                { backgroundColor: "#00ffaa", height: Math.max(6, (level + 160) / 4) },
              ]}
            />
          ))}
      </View>

      <View style={styles.bottomSection}>
        <TouchableOpacity style={styles.iconButton} onPress={() => setHistoryVisible(true)}>
          <Ionicons name="time-outline" size={22} color="#555" />
        </TouchableOpacity>

        <TouchableOpacity
          activeOpacity={1}
          onPressIn={startRecording}
          onPressOut={stopRecording}
          style={[styles.mainButton, { borderColor: status === "LISTENING" ? "#00ffaa" : "#333" }]}
        >
          <BlurView intensity={20} tint="dark" style={styles.blurContainer}>
            <Text style={[styles.buttonText, { color: status === "LISTENING" ? "#00ffaa" : "#fff" }]}>
              {status === "LISTENING" ? "RELEASE TO SEND" : "HOLD TO COMMAND"}
            </Text>
          </BlurView>
        </TouchableOpacity>

        <TouchableOpacity style={styles.iconButton} onPress={() => setSettingsVisible(true)}>
          <Ionicons name="settings-outline" size={22} color="#555" />
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#020205", alignItems: "center", justifyContent: "space-between", paddingVertical: 80 },
  textContainer: { alignItems: "center", zIndex: 10 },
  subText: { color: "#00d4ff", fontSize: 10, letterSpacing: 4, fontWeight: "bold", marginBottom: 10 },
  mainQuestion: { color: "#fff", fontSize: 24, fontWeight: "300" },
  orbContainer: { width: width, height: 350, alignItems: "center", justifyContent: "center" },
  ring: { position: "absolute", width: 220, height: 220, borderRadius: 110, borderWidth: 0.5, borderColor: "rgba(255,255,255,0.1)" },
  ringSmall: { width: 100, height: 100, borderRadius: 50, shadowColor: "#ff2d55", shadowRadius: 20, shadowOpacity: 0.8, elevation: 15 },
  ringLarge: { width: 300, height: 300, borderRadius: 150 },
  gradient: { flex: 1, borderRadius: 150, opacity: 0.5 },
  waveformContainer: { flexDirection: "row", alignItems: "center", gap: 5, height: 60, justifyContent: "center" },
  waveLine: { width: 3, borderRadius: 2 },
  bottomSection: { flexDirection: "row", alignItems: "center", width: "100%", paddingHorizontal: 25, gap: 15 },
  mainButton: { flex: 1, height: 64, borderRadius: 32, borderWidth: 1, overflow: "hidden" },
  blurContainer: { flex: 1, alignItems: "center", justifyContent: "center" },
  buttonText: { fontWeight: "bold", fontSize: 12, letterSpacing: 2 },
  iconButton: { width: 56, height: 56, borderRadius: 28, backgroundColor: "rgba(255,255,255,0.03)", alignItems: "center", justifyContent: "center" },
  modalOverlay: { flex: 1, justifyContent: "flex-end" },
  modalContent: { height: height * 0.7, backgroundColor: "#000", borderTopLeftRadius: 30, borderTopRightRadius: 30, padding: 30 },
  modalHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 30 },
  modalTitle: { color: "#00d4ff", fontSize: 14, letterSpacing: 4, fontWeight: "bold" },
  placeholderText: { color: "#444", fontSize: 16, textAlign: "center", marginTop: 100 },
  settingItem: { paddingVertical: 20, borderBottomWidth: 0.5, borderBottomColor: "#222" },
  settingText: { color: "#fff", fontSize: 18 },
  settingSub: { color: "#555", fontSize: 14, marginTop: 4 },
});
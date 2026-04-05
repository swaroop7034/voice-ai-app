import { Ionicons } from "@expo/vector-icons";
import { Audio } from "expo-av";
import { BlurView } from "expo-blur";
import * as Haptics from "expo-haptics";
import { LinearGradient } from "expo-linear-gradient";
import React, { useEffect, useState, useRef, useCallback } from "react";
import {
  Dimensions, StyleSheet, Text, TouchableOpacity,
  View, Alert, Modal, ScrollView, FlatList,
} from "react-native";
import Animated, {
  Easing, useAnimatedStyle, useSharedValue,
  withRepeat, withTiming, withSpring,
  interpolateColor, useDerivedValue,
} from "react-native-reanimated";
import { useRouter } from "expo-router";
import { API_BASE_URL } from "@/lib/safeFolderApi";

const { width } = Dimensions.get("window");

const BASE_URL  = API_BASE_URL;
const CHAT_URL  = `${BASE_URL}/chat`;
const ALERT_URL = `${BASE_URL}/check-alerts`;

type AriesStatus = "IDLE" | "LISTENING" | "THINKING";
type SlotOption  = { index: number; label: string };

function parseSlotOptions(text: string): SlotOption[] {
  const regex = /Option\s+(\d+):\s*([^,]+?)(?=,\s*Option\s+\d+:|\.?\s*Which|$)/gi;
  const slots: SlotOption[] = [];
  let match;
  while ((match = regex.exec(text)) !== null) {
    slots.push({ index: parseInt(match[1]), label: match[2].trim() });
  }
  return slots;
}

// ─── PARTICLE ───────────────────────────────────────────────────
const Particle = ({ index }: { index: number }) => {
  const translateY = useSharedValue(Math.random() * 200);
  useEffect(() => {
    translateY.value = withRepeat(
      withTiming(-150, { duration: 5000 + Math.random() * 3000, easing: Easing.linear }),
      -1, false
    );
  }, []);
  const animStyle = useAnimatedStyle(() => ({ transform: [{ translateY: translateY.value }] }));
  return (
    <Animated.View style={[
      { position: "absolute", width: 1, height: 60, backgroundColor: "#00d4ff22",
        left: (width / 20) * index, bottom: -50 },
      animStyle,
    ]} />
  );
};

// ─── FREQUENCY BAR ──────────────────────────────────────────────
const FrequencyBar = ({ isSpeaking }: { isSpeaking: boolean }) => {
  const barHeight = useSharedValue(2);
  useEffect(() => {
    if (isSpeaking) {
      barHeight.value = withRepeat(
        withTiming(12 + Math.random() * 28, { duration: 120 }), -1, true
      );
    } else {
      barHeight.value = withSpring(2);
    }
  }, [isSpeaking]);
  const barStyle = useAnimatedStyle(() => ({
    height: barHeight.value,
    opacity: withTiming(isSpeaking ? 1 : 0),
  }));
  return <Animated.View style={[styles.freqBar, barStyle]} />;
};

// ─── SLOT PANEL ─────────────────────────────────────────────────
// Inline panel above the speak button — never blocks controls
const SlotPanel = ({
  slots,
  onSelect,
  onDismiss,
}: {
  slots: SlotOption[];
  onSelect: (slot: SlotOption) => void;
  onDismiss: () => void;
}) => {
  if (slots.length === 0) return null;
  return (
    <View style={styles.slotPanel}>
      {/* Header */}
      <View style={styles.slotPanelHeader}>
        <View style={styles.slotPanelHeaderLeft}>
          <View style={styles.slotPanelDot} />
          <Text style={styles.slotPanelTitle}>AVAILABLE SLOTS</Text>
        </View>
        <Text style={styles.slotPanelHint}>TAP OR SPEAK TO CHOOSE</Text>
      </View>

      {/* Horizontal scrollable slot chips */}
      <FlatList
        data={slots}
        horizontal
        showsHorizontalScrollIndicator={false}
        keyExtractor={(item) => String(item.index)}
        contentContainerStyle={styles.slotChipList}
        renderItem={({ item }) => (
          <TouchableOpacity style={styles.slotChip} onPress={() => onSelect(item)}>
            <Text style={styles.slotChipIndex}>{item.index}</Text>
            <Text style={styles.slotChipLabel}>{item.label.toUpperCase()}</Text>
          </TouchableOpacity>
        )}
      />

      {/* Dismiss */}
      <TouchableOpacity style={styles.slotDismiss} onPress={onDismiss}>
        <Text style={styles.slotDismissText}>KEEP ORIGINAL TIME</Text>
      </TouchableOpacity>
    </View>
  );
};

// ─── MAIN SCREEN ────────────────────────────────────────────────
export default function HomeScreen() {
  const router = useRouter();
  const [status, setStatus]               = useState<AriesStatus>("IDLE");
  const [isSpeaking, setIsSpeaking]       = useState(false);
  const [historyVisible, setHistoryVisible] = useState(false);
  const [chatHistory, setChatHistory]     = useState<{ role: string; content: string }[]>([]);
  const [slotOptions, setSlotOptions]     = useState<SlotOption[]>([]);

  const recording   = useRef<Audio.Recording | null>(null);
  const soundRef    = useRef<Audio.Sound | null>(null);
  const isRecording = useRef(false);

  const rotation      = useSharedValue(0);
  const pulse         = useSharedValue(0);
  const stateProgress = useSharedValue(0);

  const glowColor = useDerivedValue(() =>
    interpolateColor(stateProgress.value, [0, 1, 2], ["#00d4ff", "#00ffaa", "#ff2d55"])
  );

  const ringLargeStyle = useAnimatedStyle(() => ({
    transform: [{ rotateZ: `${rotation.value * 0.5}deg` }, { scale: 1 + pulse.value * 0.1 }],
    borderColor: glowColor.value,
    opacity: withTiming(status === "IDLE" ? 0.2 : 0.8),
  }));

  const ringMidStyle = useAnimatedStyle(() => ({
    transform: [{ rotateZ: `${rotation.value * -0.8}deg` }, { scale: 0.75 + pulse.value * 0.1 }],
    borderColor: glowColor.value,
    opacity: withTiming(status === "IDLE" ? 0.2 : 0.8),
  }));

  const coreStyle = useAnimatedStyle(() => ({
    borderColor: glowColor.value,
    shadowColor: glowColor.value,
  }));

  useEffect(() => {
    rotation.value = withRepeat(
      withTiming(360, { duration: 15000, easing: Easing.linear }), -1, false
    );
    return () => { soundRef.current?.unloadAsync(); };
  }, []);

  useEffect(() => {
    const target = status === "IDLE" ? 0 : status === "LISTENING" ? 1 : 2;
    stateProgress.value = withTiming(target, { duration: 500 });
  }, [status]);

  // ── Audio playback ──
  const playVoiceResponse = useCallback(async (base64Audio: string) => {
    try {
      setIsSpeaking(true);
      await Audio.setAudioModeAsync({
        allowsRecordingIOS: false,
        playsInSilentModeIOS: true,
      });
      const { sound } = await Audio.Sound.createAsync(
        { uri: `data:audio/mp3;base64,${base64Audio.replace(/\s/g, "")}` },
        { shouldPlay: true, volume: 1.0 }
      );
      soundRef.current = sound;
      sound.setOnPlaybackStatusUpdate((s) => {
        if (s.isLoaded && s.didJustFinish) {
          setIsSpeaking(false);
          sound.unloadAsync();
          soundRef.current = null;
          setStatus("IDLE");
        }
      });
    } catch (e) {
      console.error("Audio error:", e);
      setIsSpeaking(false);
      setStatus("IDLE");
    }
  }, []);

  // ── Send text directly (for tap-to-select slot) ──
  const sendText = useCallback(async (text: string) => {
    setStatus("THINKING");
    try {
      const res  = await fetch(`${BASE_URL}/text-input`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      const assistantText = String(data.aries_text || data.message || "...");
      setChatHistory(prev => [
        ...prev,
        { role: "USER",  content: text },
        { role: "ARIES", content: assistantText },
      ]);
      setSlotOptions([]);
      if (data.audio) await playVoiceResponse(data.audio);
      else if (data.status !== "success") setStatus("IDLE");
    } catch (_) { setStatus("IDLE"); }
  }, [playVoiceResponse]);

  // ── Alert polling ──
  useEffect(() => {
    const poll = async () => {
      try {
        const res  = await fetch(ALERT_URL);
        const data = await res.json();
        if (data.has_alert && data.audio) {
          setStatus("THINKING");
          setChatHistory(prev => [...prev, { role: "ARIES (ALERT)", content: data.message }]);
          setSlotOptions([]);
          await playVoiceResponse(data.audio);
        }
      } catch (_) {}
    };
    const interval = setInterval(poll, 20000);
    return () => clearInterval(interval);
  }, [playVoiceResponse]);

  // ── Recording ──
  const startRecording = async () => {
    if (isRecording.current) return;
    try {
      if (soundRef.current) {
        await soundRef.current.stopAsync();
        await soundRef.current.unloadAsync();
        soundRef.current = null;
        setIsSpeaking(false);
      }
      const { granted } = await Audio.requestPermissionsAsync();
      if (!granted) return;
      await Audio.setAudioModeAsync({ allowsRecordingIOS: true, playsInSilentModeIOS: true });
      const { recording: rec } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY,
        (s) => {
          if (s.metering !== undefined)
            pulse.value = withSpring(Math.max(0, (s.metering + 60) / 60));
        },
        100
      );
      recording.current   = rec;
      isRecording.current = true;
      setStatus("LISTENING");
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
    } catch (err) { console.error(err); }
  };

  const stopRecording = async () => {
    if (!recording.current || !isRecording.current) return;
    isRecording.current = false;
    setStatus("THINKING");
    await recording.current.stopAndUnloadAsync();
    const uri = recording.current.getURI();
    recording.current = null;
    pulse.value = withSpring(0);
    if (!uri) { setStatus("IDLE"); return; }

    const formData = new FormData();
    formData.append("file", { uri, name: "voice.m4a", type: "audio/m4a" } as any);

    try {
      const res  = await fetch(CHAT_URL, { method: "POST", body: formData });
      const data = await res.json();
      const normalizedUserText = String(data.user_text || "").toLowerCase().replace(/[^a-z\s]/g, " ").replace(/\s+/g, " ").trim();
      const normalizedAriesText = String(data.aries_text || data.message || "").toLowerCase();
      const safeFolderIntent = normalizedUserText.includes("safe folder");
      const grantedByMessage = normalizedAriesText.includes("access granted");

      if (data.access_granted || (safeFolderIntent && grantedByMessage)) {
        setStatus("IDLE");
        setSlotOptions([]);
        setChatHistory(prev => [
          ...prev,
          { role: "USER", content: data.user_text || "safe folder" },
          { role: "ARIES", content: data.message || data.aries_text || "Access granted" },
        ]);
        router.replace("/safe-folder-screen");
        return;
      }

      if (data.status === "enrolled") {
        Alert.alert("Success", data.message || "Voice registered successfully");
        setStatus("IDLE");
        return;
      }

      if (data.status && data.status !== "success") {
        const assistantText = String(data.aries_text || data.message || "...");
        setChatHistory(prev => [
          ...prev,
          { role: "USER", content: data.user_text || "..." },
          { role: "ARIES", content: assistantText },
        ]);
        if (data.audio) await playVoiceResponse(data.audio);
        else setStatus("IDLE");
        return;
      }

      if (data.status === "success") {
        const ariesText = data.aries_text || data.message || "...";
        setChatHistory(prev => [
          ...prev,
          { role: "USER",  content: data.user_text || "..." },
          { role: "ARIES", content: ariesText },
        ]);
        // Show slot panel if response has options; clear it otherwise
        const parsed = parseSlotOptions(ariesText);
        setSlotOptions(parsed);
        if (data.audio) await playVoiceResponse(data.audio);
      }
    } catch (_) {
      Alert.alert("Neural Core Offline", "Check your local server connection.");
      setStatus("IDLE");
    }
  };

  const handleSlotTap  = (slot: SlotOption) => sendText(`option ${slot.index}`);
  const handleDismiss  = () => sendText("no keep it");

  const statusColor = status === "IDLE" ? "#00d4ff" : status === "LISTENING" ? "#00ffaa" : "#ff2d55";

  return (
    <View style={styles.container}>
      <LinearGradient colors={["#02050a", "#050a15", "#000"]} style={StyleSheet.absoluteFill} />
      <View style={StyleSheet.absoluteFill}>
        {[...Array(20)].map((_, i) => <Particle key={i} index={i} />)}
      </View>

      {/* Vitals */}
      <View style={styles.vitalsContainer}>
        <View style={[styles.dot, { backgroundColor: statusColor }]} />
        <Text style={styles.vitalsText}>ARIES: {status} | ENCRYPTED LINK</Text>
      </View>

      {/* Labels */}
      <View style={styles.textContainer}>
        <Text style={[styles.subText, { color: statusColor }]}>NEURAL INTERFACE • ACTIVE</Text>
        <Text style={styles.mainQuestion}>
          {status === "IDLE" ? "STANDBY" : status === "LISTENING" ? "MONITORING" : "PROCESSING"}
        </Text>
      </View>

      {/* Orb — shrinks slightly when slot panel is visible to give room */}
      <View style={[styles.orbContainer, slotOptions.length > 0 && { height: 240 }]}>
        <Animated.View style={[styles.ring, styles.ringLarge, ringLargeStyle]} />
        <Animated.View style={[styles.ring, styles.ringMid,   ringMidStyle]}  />
        <Animated.View style={[styles.core, coreStyle]}>
          <LinearGradient
            colors={["rgba(0,0,0,1)", "rgba(0,212,255,0.05)"]}
            style={styles.coreGradient}
          />
        </Animated.View>
      </View>

      {/* Bottom stack: slot panel → frequency → controls */}
      <View style={styles.bottomAreaWrapper}>

        {/* Slot panel — sits above frequency bar, never overlaps speak button */}
        <SlotPanel
          slots={slotOptions}
          onSelect={handleSlotTap}
          onDismiss={handleDismiss}
        />

        {/* Frequency visualiser */}
        <View style={styles.frequencyWrapper}>
          <Text style={[styles.freqLabel, { opacity: isSpeaking ? 0.6 : 0 }]}>
            TRANSMISSION FREQUENCY
          </Text>
          <View style={styles.frequencyContainer}>
            {[...Array(18)].map((_, i) => <FrequencyBar key={i} isSpeaking={isSpeaking} />)}
          </View>
        </View>

        {/* Speak button row — always visible and accessible */}
        <View style={styles.bottomSection}>
          <TouchableOpacity style={styles.iconButton} onPress={() => setHistoryVisible(true)}>
            <Ionicons name="time-outline" size={26} color="rgba(255,255,255,0.4)" />
          </TouchableOpacity>

          <TouchableOpacity
            activeOpacity={1}
            onPressIn={startRecording}
            onPressOut={stopRecording}
            style={[styles.mainButton, { borderColor: status === "LISTENING" ? "#00ffaa" : "#333" }]}
          >
            <BlurView intensity={20} tint="dark" style={styles.blurContainer}>
              <Text style={[styles.buttonText, { color: status === "LISTENING" ? "#00ffaa" : "#fff" }]}>
                {status === "LISTENING" ? "RECORDING" : "COMMUNICATE"}
              </Text>
            </BlurView>
          </TouchableOpacity>

          <TouchableOpacity style={styles.iconButton} onPress={() => router.push('/settings')}>
            <Ionicons name="settings-outline" size={26} color="rgba(255,255,255,0.4)" />
          </TouchableOpacity>
        </View>
      </View>

      {/* Neural Logs Modal */}
      <Modal visible={historyVisible} animationType="slide" transparent>
        <BlurView intensity={90} tint="dark" style={styles.modalOverlay}>
          <View style={styles.historyCard}>
            <View style={styles.historyHeader}>
              <Text style={styles.historyTitle}>NEURAL LOGS</Text>
              <TouchableOpacity onPress={() => setHistoryVisible(false)}>
                <Ionicons name="close-circle-outline" size={28} color="#ff2d55" />
              </TouchableOpacity>
            </View>
            <ScrollView showsVerticalScrollIndicator={false}>
              {chatHistory.map((item, i) => (
                <View key={i} style={styles.logEntry}>
                  <Text style={[styles.logRole, {
                    color: item.role === "USER" ? "#00d4ff"
                         : item.role.includes("ALERT") ? "#ff9500" : "#00ffaa"
                  }]}>[{item.role}] </Text>
                  <Text style={styles.logText}>{item.content}</Text>
                </View>
              ))}
            </ScrollView>
          </View>
        </BlurView>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container:          { flex: 1, alignItems: "center", justifyContent: "space-between", paddingVertical: 60 },
  textContainer:      { alignItems: "center", marginTop: 40 },
  subText:            { fontSize: 10, letterSpacing: 5, fontWeight: "900", marginBottom: 8, opacity: 0.8 },
  mainQuestion:       { color: "#fff", fontSize: 26, fontWeight: "100", letterSpacing: 3 },
  vitalsContainer:    { position: "absolute", top: 55, left: 25, flexDirection: "row", alignItems: "center", opacity: 0.5 },
  vitalsText:         { color: "#00d4ff", fontSize: 8, letterSpacing: 1, fontWeight: "bold" },
  dot:                { width: 5, height: 5, borderRadius: 2.5, marginRight: 6 },
  orbContainer:       { width, height: 300, alignItems: "center", justifyContent: "center" },
  core:               { width: 100, height: 100, borderRadius: 50, borderWidth: 1, shadowRadius: 20, shadowOpacity: 1, elevation: 10 },
  coreGradient:       { width: "100%", height: "100%", borderRadius: 50 },
  ring:               { position: "absolute", borderWidth: 0.5, borderRadius: 1000, borderStyle: "dashed" },
  ringMid:            { width: 200, height: 200 },
  ringLarge:          { width: 300, height: 300 },

  // ── Bottom stack ──
  bottomAreaWrapper:  { width: "100%", alignItems: "center" },

  // Slot panel — inline, not a modal
  slotPanel:          { width: "92%", backgroundColor: "rgba(0,10,20,0.95)", borderWidth: 1, borderColor: "#00d4ff22", borderRadius: 18, paddingTop: 14, paddingBottom: 6, marginBottom: 10 },
  slotPanelHeader:    { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingHorizontal: 16, marginBottom: 10 },
  slotPanelHeaderLeft:{ flexDirection: "row", alignItems: "center", gap: 8 },
  slotPanelDot:       { width: 5, height: 5, borderRadius: 3, backgroundColor: "#00d4ff" },
  slotPanelTitle:     { color: "#00d4ff", fontSize: 9, letterSpacing: 3, fontWeight: "bold" },
  slotPanelHint:      { color: "rgba(255,255,255,0.2)", fontSize: 7, letterSpacing: 1 },
  slotChipList:       { paddingHorizontal: 12, gap: 8 },
  slotChip:           { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: "rgba(0,212,255,0.06)", borderWidth: 1, borderColor: "#00d4ff33", borderRadius: 10, paddingHorizontal: 14, paddingVertical: 10 },
  slotChipIndex:      { color: "#00d4ff", fontSize: 16, fontWeight: "200", width: 18 },
  slotChipLabel:      { color: "rgba(255,255,255,0.75)", fontSize: 11, letterSpacing: 0.5, fontWeight: "300" },
  slotDismiss:        { alignItems: "center", paddingVertical: 10 },
  slotDismissText:    { color: "rgba(255,255,255,0.2)", fontSize: 8, letterSpacing: 2 },

  // Frequency
  frequencyWrapper:   { height: 55, alignItems: "center", justifyContent: "center", marginBottom: 8 },
  freqLabel:          { color: "#00ffaa", fontSize: 7, letterSpacing: 2, marginBottom: 8, fontWeight: "bold" },
  frequencyContainer: { flexDirection: "row", alignItems: "flex-end", gap: 3, height: 28 },
  freqBar:            { width: 2, backgroundColor: "#00ffaa", borderRadius: 1 },

  // Controls
  bottomSection:      { flexDirection: "row", width: "100%", paddingHorizontal: 30, alignItems: "center", gap: 15, marginBottom: 20 },
  mainButton:         { flex: 1, height: 60, borderRadius: 30, borderWidth: 1, overflow: "hidden" },
  blurContainer:      { flex: 1, alignItems: "center", justifyContent: "center" },
  buttonText:         { fontWeight: "bold", fontSize: 12, letterSpacing: 2 },
  iconButton:         { width: 56, height: 56, borderRadius: 28, backgroundColor: "rgba(255,255,255,0.03)", alignItems: "center", justifyContent: "center" },
  // Modal
  modalOverlay:       { flex: 1, justifyContent: "flex-end" },
  historyCard:        { height: "70%", backgroundColor: "rgba(0,0,0,0.95)", borderTopLeftRadius: 30, borderTopRightRadius: 30, padding: 25, borderTopWidth: 1, borderTopColor: "#00d4ff44" },
  historyHeader:      { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 20 },
  historyTitle:       { color: "#00d4ff", fontSize: 10, letterSpacing: 2, fontWeight: "bold" },
  logEntry:           { flexDirection: "row", marginBottom: 15 },
  logRole:            { fontSize: 10, fontWeight: "bold", marginRight: 10 },
  logText:            { color: "#fff", fontSize: 13, flex: 1, fontWeight: "300" },
});


import { Ionicons } from "@expo/vector-icons";
import { Audio } from "expo-av";
import { BlurView } from "expo-blur";
import * as Haptics from "expo-haptics";
import { LinearGradient } from "expo-linear-gradient";
import React, { useEffect, useState, useRef } from "react";
import {
  Dimensions,

  StyleSheet,
  Text,
  TouchableOpacity,
  View,
  Alert,
  Modal,
  ScrollView,
} from "react-native";
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
  withSpring,
  interpolateColor,
  useDerivedValue,
} from "react-native-reanimated";

const { width, height } = Dimensions.get("window");
const BACKEND_URL = "http://192.168.1.6:8000/chat"; 

type ArisStatus = "IDLE" | "LISTENING" | "THINKING";

// --- BACKGROUND DATA STREAM ---
const Particle = ({ index }: { index: number }) => {
  const translateY = useSharedValue(Math.random() * 200);
  useEffect(() => {
    translateY.value = withRepeat(
      withTiming(-150, { duration: 5000 + Math.random() * 3000, easing: Easing.linear }),
      -1,
      false
    );
  }, []);
  const animatedStyle = useAnimatedStyle(() => ({ transform: [{ translateY: translateY.value }] }));
  return (
    <Animated.View style={[{ position: 'absolute', width: 1, height: 60, backgroundColor: '#00d4ff22', left: (width / 20) * index, bottom: -50 }, animatedStyle]} />
  );
};

// --- FREQUENCY BAR ---
const FrequencyBar = ({ isSpeaking }: { isSpeaking: boolean }) => {
  const barHeight = useSharedValue(2);
  useEffect(() => {
    if (isSpeaking) {
      barHeight.value = withRepeat(withTiming(12 + Math.random() * 28, { duration: 120 }), -1, true);
    } else {
      barHeight.value = withSpring(2);
    }
  }, [isSpeaking]);
  return <Animated.View style={[styles.freqBar, useAnimatedStyle(() => ({ height: barHeight.value, opacity: withTiming(isSpeaking ? 1 : 0) }))]}/>;
};

export default function HomeScreen() {
  const [status, setStatus] = useState<ArisStatus>("IDLE");
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [historyVisible, setHistoryVisible] = useState(false);
  const [chatHistory, setChatHistory] = useState<{role: string, content: string}[]>([]);
  
  const [recording, setRecording] = useState<Audio.Recording | null>(null);
  const [metering, setMetering] = useState<number[]>(new Array(12).fill(-160));
  const soundRef = useRef<Audio.Sound | null>(null);

  const rotation = useSharedValue(0);
  const pulse = useSharedValue(0); 
  const stateProgress = useSharedValue(0); 

  useEffect(() => {
    rotation.value = withRepeat(withTiming(360, { duration: 15000, easing: Easing.linear }), -1, false);
    return () => { if (soundRef.current) soundRef.current.unloadAsync(); };
  }, []);

  useEffect(() => {
    const target = status === "IDLE" ? 0 : status === "LISTENING" ? 1 : 2;
    stateProgress.value = withTiming(target, { duration: 500 });
  }, [status]);

  const glowColor = useDerivedValue(() => interpolateColor(stateProgress.value, [0, 1, 2], ["#00d4ff", "#00ffaa", "#ff2d55"]));

  // --- AUDIO PLAYBACK ---
  async function playVoiceResponse(base64Audio: string) {
    try {
      setIsSpeaking(true);
      const cleanBase64 = base64Audio.replace(/\s/g, '');
      const audioUri = `data:audio/mp3;base64,${cleanBase64}`;

      const { sound } = await Audio.Sound.createAsync(
        { uri: audioUri },
        { shouldPlay: true, volume: 1.0 }
      );

      soundRef.current = sound;
      sound.setOnPlaybackStatusUpdate((s) => {
        if (s.isLoaded && s.didJustFinish) {
          setIsSpeaking(false);
          sound.unloadAsync();
          soundRef.current = null;
        }
      });
    } catch (e) { 
      console.error("Audio Load Error:", e);
      setIsSpeaking(false); 
    }
  }

  // --- ACTIONS ---
  async function startRecording() {
    try {
      // INTERRUPT: Stop any current speech immediately
      if (soundRef.current) {
        await soundRef.current.stopAsync();
        await soundRef.current.unloadAsync();
        soundRef.current = null;
        setIsSpeaking(false);
      }

      const { granted } = await Audio.requestPermissionsAsync();
      if (!granted) return;

      await Audio.setAudioModeAsync({ allowsRecordingIOS: true, playsInSilentModeIOS: true });

      const { recording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY,
        (s) => {
          if (s.metering !== undefined) {
            setMetering((prev) => [...prev.slice(1), s.metering!]);
            pulse.value = withSpring(Math.max(0, (s.metering + 60) / 60));
          }
        },
        100
      );

      setRecording(recording);
      setStatus("LISTENING");
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
    } catch (err) { console.error(err); }
  }

  async function stopRecording() {
    if (!recording) return;
    setStatus("THINKING");
    await recording.stopAndUnloadAsync();
    const uri = recording.getURI();

    if (uri) {
      const formData = new FormData();
      formData.append('file', { uri, name: 'voice.m4a', type: 'audio/m4a' } as any);
      try {
        const response = await fetch(BACKEND_URL, { method: 'POST', body: formData });
        const data = await response.json();
        
        if (data.status === "success") {
          // SYNCED WITH UPDATED main.py KEYS
          setChatHistory(prev => [...prev, 
            { role: "USER", content: data.user_text || "..." },
            { role: "ARIES", content: data.aries_text || "..." }
          ]);
          if (data.audio) await playVoiceResponse(data.audio);
        }
      } catch (e) { Alert.alert("Neural Core Offline", "Ensure PC is active."); }
    }
    setStatus("IDLE");
    pulse.value = withSpring(0);
  }

  const ringStyle = (speed: number, baseScale: number) => useAnimatedStyle(() => ({
    transform: [{ rotateZ: `${rotation.value * speed}deg` }, { scale: baseScale + pulse.value * 0.1 }],
    borderColor: glowColor.value,
    opacity: withTiming(status === "IDLE" ? 0.2 : 0.8),
  }));

  return (
    <View style={styles.container}>
      <LinearGradient colors={["#02050a", "#050a15", "#000"]} style={StyleSheet.absoluteFill} />
      <View style={StyleSheet.absoluteFill}>{[...Array(20)].map((_, i) => <Particle key={i} index={i} />)}</View>

      <View style={styles.vitalsContainer}>
        <View style={[styles.dot, { backgroundColor: status === "IDLE" ? "#00d4ff" : "#00ffaa" }]} />
        <Text style={styles.vitalsText}>ARIES: {status} | LINKED</Text>
      </View>

      <View style={styles.textContainer}>
        <Text style={[styles.subText, { color: status === "THINKING" ? "#ff2d55" : "#00d4ff" }]}>NEURAL INTERFACE • ACTIVE</Text>
        <Text style={styles.mainQuestion}>{status === "IDLE" ? "READY" : status === "LISTENING" ? "LISTENING" : "THINKING"}</Text>
      </View>

      <View style={styles.orbContainer}>
        <Animated.View style={[styles.ring, styles.ringLarge, ringStyle(0.5, 1)]} />
        <Animated.View style={[styles.ring, styles.ringMid, ringStyle(-0.8, 0.75)]} />
        <Animated.View style={[styles.core, useAnimatedStyle(() => ({ borderColor: glowColor.value, shadowColor: glowColor.value }))]}>
           <LinearGradient colors={["rgba(0,0,0,1)", "rgba(0,212,255,0.05)"]} style={styles.coreGradient} />
        </Animated.View>
      </View>

      <View style={styles.bottomAreaWrapper}>
        <View style={styles.frequencyWrapper}>
          <Text style={[styles.freqLabel, { opacity: isSpeaking ? 0.6 : 0 }]}>OUTPUT FREQUENCY</Text>
          <View style={styles.frequencyContainer}>{[...Array(18)].map((_, i) => <FrequencyBar key={i} isSpeaking={isSpeaking} />)}</View>
        </View>

        <View style={styles.bottomSection}>
          <TouchableOpacity style={styles.iconButton} onPress={() => setHistoryVisible(true)}>
            <Ionicons name="time-outline" size={26} color="rgba(255,255,255,0.4)" />
          </TouchableOpacity>
          <TouchableOpacity activeOpacity={1} onPressIn={startRecording} onPressOut={stopRecording} style={[styles.mainButton, { borderColor: status === "LISTENING" ? "#00ffaa" : "#333" }]}>
            <BlurView intensity={20} tint="dark" style={styles.blurContainer}>
              <Text style={[styles.buttonText, { color: status === "LISTENING" ? "#00ffaa" : "#fff" }]}>
                {status === "LISTENING" ? "RECEIVING..." : "INITIATE"}
              </Text>
            </BlurView>
          </TouchableOpacity>
          <TouchableOpacity style={styles.iconButton} onPress={() => {}}><Ionicons name="settings-outline" size={26} color="rgba(255,255,255,0.4)" /></TouchableOpacity>
        </View>
      </View>

      <Modal visible={historyVisible} animationType="slide" transparent={true}>
        <BlurView intensity={90} tint="dark" style={styles.modalOverlay}>
          <View style={styles.historyCard}>
            <View style={styles.historyHeader}>
              <Text style={styles.historyTitle}>CONVERSATION LOG</Text>
              <TouchableOpacity onPress={() => setHistoryVisible(false)}><Ionicons name="close-circle-outline" size={28} color="#ff2d55" /></TouchableOpacity>
            </View>
            <ScrollView>{chatHistory.map((item, i) => (
              <View key={i} style={styles.logEntry}>
                <Text style={[styles.logRole, { color: item.role === "USER" ? "#00d4ff" : "#00ffaa" }]}>[{item.role}] </Text>
                <Text style={styles.logText}>{item.content}</Text>
              </View>
            ))}</ScrollView>
          </View>
        </BlurView>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "space-between", paddingVertical: 60 },
  textContainer: { alignItems: "center", marginTop: 40 },
  subText: { fontSize: 10, letterSpacing: 5, fontWeight: "900", marginBottom: 8, opacity: 0.8 },
  mainQuestion: { color: "#fff", fontSize: 26, fontWeight: "100", letterSpacing: 3 },
  vitalsContainer: { position: 'absolute', top: 55, left: 25, flexDirection: 'row', alignItems: 'center', opacity: 0.5 },
  vitalsText: { color: '#00d4ff', fontSize: 8, letterSpacing: 1, fontWeight: 'bold' },
  dot: { width: 5, height: 5, borderRadius: 2.5, marginRight: 6 },
  orbContainer: { width: width, height: 350, alignItems: "center", justifyContent: "center" },
  core: { width: 100, height: 100, borderRadius: 50, borderWidth: 1, shadowRadius: 20, shadowOpacity: 1, elevation: 10 },
  coreGradient: { width: '100%', height: '100%', borderRadius: 50 },
  ring: { position: "absolute", borderWidth: 0.5, borderRadius: 1000, borderStyle: 'dashed' },
  ringMid: { width: 200, height: 200 },
  ringLarge: { width: 300, height: 300 },
  bottomAreaWrapper: { width: '100%', alignItems: 'center' },
  frequencyWrapper: { height: 60, alignItems: 'center', justifyContent: 'center', marginBottom: 10 },
  freqLabel: { color: '#00ffaa', fontSize: 7, letterSpacing: 2, marginBottom: 10, fontWeight: 'bold' },
  frequencyContainer: { flexDirection: 'row', alignItems: 'flex-end', gap: 3, height: 30 },
  freqBar: { width: 2, backgroundColor: '#00ffaa', borderRadius: 1 },
  bottomSection: { flexDirection: "row", width: "100%", paddingHorizontal: 30, alignItems: "center", gap: 15, marginBottom: 20 },
  mainButton: { flex: 1, height: 60, borderRadius: 30, borderWidth: 1, overflow: "hidden" },
  blurContainer: { flex: 1, alignItems: "center", justifyContent: "center" },
  buttonText: { fontWeight: "bold", fontSize: 12, letterSpacing: 2 },
  iconButton: { width: 56, height: 56, borderRadius: 28, backgroundColor: "rgba(255,255,255,0.03)", alignItems: "center", justifyContent: "center" },
  modalOverlay: { flex: 1, justifyContent: 'flex-end' },
  historyCard: { height: '70%', backgroundColor: 'rgba(0,0,0,0.95)', borderTopLeftRadius: 30, borderTopRightRadius: 30, padding: 25, borderTopWidth: 1, borderTopColor: '#00d4ff44' },
  historyHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 },
  historyTitle: { color: '#00d4ff', fontSize: 10, letterSpacing: 2, fontWeight: 'bold' },
  logEntry: { flexDirection: 'row', marginBottom: 15 },
  logRole: { fontSize: 10, fontWeight: 'bold', marginRight: 10 },
  logText: { color: '#fff', fontSize: 13, flex: 1, fontWeight: '300' },
});
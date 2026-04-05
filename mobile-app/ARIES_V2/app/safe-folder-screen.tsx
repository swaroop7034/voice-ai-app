import { useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';

export default function SafeFolderScreen() {
  const router = useRouter();

  const handleGoBack = () => {
    if (router.canGoBack()) {
      router.back();
      return;
    }
    router.replace('/(tabs)');
  };

  return (
    <View style={styles.container}>
      <View style={styles.card}>
        <Text style={styles.title}>Safe Folder</Text>
        <Text style={styles.subtitle}>Access granted. Your protected files can live here.</Text>

        <Pressable style={styles.backButton} onPress={handleGoBack}>
          <Text style={styles.backButtonText}>Go Back</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#02050a',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  card: {
    width: '100%',
    maxWidth: 460,
    padding: 28,
    borderRadius: 28,
    backgroundColor: '#0b1220',
    borderWidth: 1,
    borderColor: '#00ffaa33',
    alignItems: 'center',
  },
  title: {
    color: '#f8fafc',
    fontSize: 34,
    fontWeight: '800',
    textAlign: 'center',
  },
  subtitle: {
    color: '#93c5fd',
    textAlign: 'center',
    marginTop: 12,
    fontSize: 16,
    lineHeight: 22,
  },
  backButton: {
    marginTop: 20,
    backgroundColor: '#00d4ff',
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 14,
  },
  backButtonText: {
    color: '#06121f',
    fontSize: 14,
    fontWeight: '800',
    letterSpacing: 0.3,
  },
});
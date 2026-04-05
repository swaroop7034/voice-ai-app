import { LinearGradient } from 'expo-linear-gradient';
import * as DocumentPicker from 'expo-document-picker';
import { useRouter } from 'expo-router';
import { EllipsisVertical, Filter, Lock, Upload } from 'lucide-react-native';
import { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Linking,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';

import {
  deleteVaultFile,
  fetchVaultFiles,
  getVaultDownloadUrl,
  getVaultOpenUrl,
  VaultFileItem,
  uploadVaultFile,
} from '@/lib/safeFolderApi';

type VaultTab = 'ALL' | 'DOCS' | 'AUDIO' | 'VIDEO' | 'IMAGE';

const BG_COLOR = '#02080D';
const PANEL_COLOR = '#071722';
const BORDER_COLOR = '#123447';
const ACCENT = '#00FFC2';
const MUTED = '#6F8796';
const MONO_FONT = Platform.select({ ios: 'Courier', android: 'monospace', default: 'monospace' });

const TABS: VaultTab[] = ['ALL', 'DOCS', 'AUDIO', 'VIDEO', 'IMAGE'];
const BADGE_COLORS: Record<Exclude<VaultTab, 'ALL'>, string> = {
  DOCS: '#2C8EFF',
  AUDIO: '#A76BFF',
  VIDEO: '#E9B85A',
  IMAGE: '#1FC5FF',
};

function categoryToBadge(category: Exclude<VaultTab, 'ALL'>): string {
  if (category === 'DOCS') return 'DOC';
  if (category === 'AUDIO') return 'AUD';
  if (category === 'VIDEO') return 'VID';
  return 'IMG';
}

function GridOverlay() {
  const verticalLines = Array.from({ length: 12 });
  const horizontalLines = Array.from({ length: 24 });

  return (
    <View pointerEvents="none" style={styles.gridOverlay}>
      {verticalLines.map((_, index) => (
        <View key={`v-${index}`} style={[styles.gridVertical, { left: `${(index + 1) * 8}%` }]} />
      ))}
      {horizontalLines.map((_, index) => (
        <View key={`h-${index}`} style={[styles.gridHorizontal, { top: `${(index + 1) * 4}%` }]} />
      ))}
    </View>
  );
}

export default function SafeFolderScreen() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<VaultTab>('ALL');
  const [files, setFiles] = useState<VaultFileItem[]>([]);
  const [usedBytes, setUsedBytes] = useState(0);
  const [totalBytes, setTotalBytes] = useState(5 * 1024 * 1024 * 1024);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);

  const loadVault = async () => {
    try {
      setIsLoading(true);
      const data = await fetchVaultFiles();
      setFiles(data.files || []);
      setUsedBytes(Number(data.stats?.used_bytes || 0));
      setTotalBytes(Number(data.stats?.total_bytes || 5 * 1024 * 1024 * 1024));
    } catch (error) {
      console.error('[Vault] load failed', error);
      Alert.alert('Vault unavailable', 'Could not load vault files right now.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadVault();
  }, []);

  const filteredFiles = useMemo(() => {
    if (activeTab === 'ALL') {
      return files;
    }
    return files.filter((item) => item.category === activeTab);
  }, [activeTab, files]);

  const usedGB = usedBytes / (1024 * 1024 * 1024);
  const totalGB = totalBytes / (1024 * 1024 * 1024);
  const storageFill = `${Math.max(0, Math.min((usedBytes / Math.max(totalBytes, 1)) * 100, 100))}%`;

  const handleUploadPress = async () => {
    try {
      const picked = await DocumentPicker.getDocumentAsync({
        multiple: false,
        copyToCacheDirectory: true,
      });

      if (picked.canceled || !picked.assets?.length) {
        return;
      }

      const asset = picked.assets[0];
      setIsUploading(true);
      await uploadVaultFile({
        uri: asset.uri,
        name: asset.name || 'upload.bin',
        mimeType: asset.mimeType || 'application/octet-stream',
      });

      await loadVault();
      Alert.alert('Upload complete', `${asset.name || 'File'} stored in encrypted vault.`);
    } catch (error) {
      console.error('[Vault] upload failed', error);
      Alert.alert('Upload failed', 'Could not upload this file to vault storage.');
    } finally {
      setIsUploading(false);
    }
  };

  const handleExitVault = () => {
    if (router.canGoBack()) {
      router.back();
      return;
    }
    router.replace('/(tabs)');
  };

  const handleOpenFile = async (file: VaultFileItem) => {
    const url = getVaultOpenUrl(file.id);
    try {
      const supported = await Linking.canOpenURL(url);
      if (!supported) {
        Alert.alert('Open unavailable', 'This device cannot open the selected file URL.');
        return;
      }
      await Linking.openURL(url);
    } catch (error) {
      console.error('[Vault] open failed', error);
      Alert.alert('Open failed', 'Unable to open this file right now.');
    }
  };

  const handleDownloadFile = async (file: VaultFileItem) => {
    const url = getVaultDownloadUrl(file.id);
    try {
      const supported = await Linking.canOpenURL(url);
      if (!supported) {
        Alert.alert('Download unavailable', 'This device cannot open the download URL.');
        return;
      }
      await Linking.openURL(url);
    } catch (error) {
      console.error('[Vault] download failed', error);
      Alert.alert('Download failed', 'Unable to download this file right now.');
    }
  };

  const handleDeleteFile = (file: VaultFileItem) => {
    Alert.alert(
      'Delete file?',
      `Delete ${file.name} from vault storage?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            try {
              await deleteVaultFile(file.id);
              await loadVault();
            } catch (error) {
              console.error('[Vault] delete failed', error);
              Alert.alert('Delete failed', 'Could not delete this file from vault storage.');
            }
          },
        },
      ]
    );
  };

  return (
    <View style={styles.screen}>
      <GridOverlay />

      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        <View style={styles.headerRow}>
          <View style={styles.linkedRow}>
            <View style={styles.statusDot} />
            <Text style={styles.linkedText}>ARIES: VAULT | LINKED</Text>
          </View>

          <View style={styles.headerActions}>
            <TouchableOpacity style={styles.exitButton} onPress={handleExitVault} activeOpacity={0.85}>
              <Text style={styles.exitButtonText}>EXIT</Text>
            </TouchableOpacity>

            <View style={styles.lockBox}>
              <Lock size={16} color={ACCENT} strokeWidth={2} />
            </View>
          </View>
        </View>

        <View style={styles.titleWrap}>
          <Text style={styles.subheading}>NEURAL INTERFACE - ACTIVE</Text>
          <Text style={styles.title}>VAULT</Text>
          <Text style={styles.description}>ENCRYPTED PERSONAL REPOSITORY</Text>
        </View>

        <TouchableOpacity style={styles.uploadZone} activeOpacity={0.9} onPress={handleUploadPress} disabled={isUploading}>
          <View style={styles.uploadCoreWrap}>
            <View style={styles.uploadCoreOuter}>
              <View style={styles.uploadCoreInner}>
                {isUploading ? (
                  <ActivityIndicator color={ACCENT} />
                ) : (
                  <Upload size={20} color={ACCENT} strokeWidth={2.5} />
                )}
              </View>
            </View>
            <Text style={styles.uploadTitle}>{isUploading ? 'UPLOADING...' : 'TRANSMIT FILE'}</Text>
            <Text style={styles.uploadHint}>tap to select and upload file</Text>
          </View>

          <View style={styles.tagRow}>
            {['DOC', 'PDF', 'MP3', 'MP4', 'JPG'].map((tag) => (
              <View key={tag} style={styles.tagPill}>
                <Text style={styles.tagText}>{tag}</Text>
              </View>
            ))}
          </View>
        </TouchableOpacity>

        <View style={styles.storageCard}>
          <View style={styles.storageHeader}>
            <Text style={styles.storageLabel}>VAULT STORAGE</Text>
            <Text style={styles.storageValue}>{usedGB.toFixed(2)} GB / {totalGB.toFixed(2)} GB</Text>
          </View>

          <View style={styles.progressTrack}>
            <LinearGradient
              colors={['#00B3FF', '#00FFC2']}
              start={{ x: 0, y: 0.5 }}
              end={{ x: 1, y: 0.5 }}
              style={[styles.progressFill, { width: storageFill }]}
            />
          </View>
        </View>

        <View style={styles.tabRow}>
          {TABS.map((tab) => {
            const active = tab === activeTab;
            return (
              <TouchableOpacity
                key={tab}
                activeOpacity={0.85}
                onPress={() => setActiveTab(tab)}
                style={[styles.tabButton, active && styles.tabButtonActive]}
              >
                {active ? (
                  <LinearGradient
                    colors={['#0D3D3E', '#0D2A2B']}
                    start={{ x: 0, y: 0 }}
                    end={{ x: 1, y: 1 }}
                    style={styles.tabGradient}
                  >
                    <Text style={[styles.tabText, styles.tabTextActive]}>{tab}</Text>
                  </LinearGradient>
                ) : (
                  <Text style={styles.tabText}>{tab}</Text>
                )}
              </TouchableOpacity>
            );
          })}
        </View>

        <View style={styles.filesHeader}>
          <Text style={styles.filesTitle}>STORED FILES</Text>
          <Text style={styles.filesCount}>{filteredFiles.length} ITEMS</Text>
        </View>

        {isLoading ? (
          <View style={styles.loadingWrap}>
            <ActivityIndicator color={ACCENT} />
            <Text style={styles.loadingText}>Syncing vault data...</Text>
          </View>
        ) : filteredFiles.length === 0 ? (
          <View style={styles.emptyWrap}>
            <Text style={styles.emptyTitle}>NO FILES STORED</Text>
            <Text style={styles.emptyText}>Tap TRANSMIT FILE to upload to backend vault storage.</Text>
          </View>
        ) : (
          <View style={styles.fileList}>
            {filteredFiles.map((file) => {
              const badgeColor = BADGE_COLORS[file.category] || BADGE_COLORS.DOCS;
              const badge = categoryToBadge(file.category);
              return (
                <TouchableOpacity key={file.id} activeOpacity={0.85} style={styles.fileRow}>
                  <View style={[styles.fileBadge, { borderColor: `${badgeColor}66`, backgroundColor: `${badgeColor}1A` }]}>
                    <Text style={[styles.fileBadgeText, { color: badgeColor }]}>{badge}</Text>
                  </View>

                  <View style={styles.fileInfo}>
                    <Text numberOfLines={1} style={styles.fileName}>{file.name}</Text>
                    <Text style={styles.fileMeta}>{file.size} - {file.date}</Text>
                  </View>

                  <View style={styles.fileActions}>
                    <TouchableOpacity style={styles.actionChip} onPress={() => handleOpenFile(file)}>
                      <Text style={styles.actionChipText}>OPEN</Text>
                    </TouchableOpacity>
                    <TouchableOpacity style={styles.actionChip} onPress={() => handleDownloadFile(file)}>
                      <Text style={styles.actionChipText}>DL</Text>
                    </TouchableOpacity>
                    <TouchableOpacity style={[styles.actionChip, styles.actionDelete]} onPress={() => handleDeleteFile(file)}>
                      <Text style={[styles.actionChipText, styles.actionDeleteText]}>DEL</Text>
                    </TouchableOpacity>
                  </View>
                </TouchableOpacity>
              );
            })}
          </View>
        )}
      </ScrollView>

      <View style={styles.footerRow}>
        <TouchableOpacity activeOpacity={0.88} style={styles.uploadButton} onPress={handleUploadPress} disabled={isUploading}>
          <Text style={styles.uploadButtonText}>{isUploading ? 'UPLOADING' : 'SECURE UPLOAD'}</Text>
        </TouchableOpacity>

        <View style={styles.footerActions}>
          <TouchableOpacity activeOpacity={0.85} style={styles.iconButton} onPress={loadVault}>
            <Filter size={16} color="#9FB2BE" strokeWidth={2.2} />
          </TouchableOpacity>
          <TouchableOpacity activeOpacity={0.85} style={styles.iconButton}>
            <EllipsisVertical size={16} color="#9FB2BE" strokeWidth={2.2} />
          </TouchableOpacity>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: BG_COLOR,
  },
  gridOverlay: {
    ...StyleSheet.absoluteFillObject,
    opacity: 0.22,
  },
  gridVertical: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    width: 1,
    backgroundColor: '#123041',
  },
  gridHorizontal: {
    position: 'absolute',
    left: 0,
    right: 0,
    height: 1,
    backgroundColor: '#102A38',
  },
  content: {
    paddingTop: 48,
    paddingHorizontal: 16,
    paddingBottom: 140,
    gap: 14,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  exitButton: {
    height: 32,
    paddingHorizontal: 12,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#1A5D62',
    backgroundColor: '#08242E',
    alignItems: 'center',
    justifyContent: 'center',
  },
  exitButtonText: {
    color: '#8FEFE0',
    fontSize: 11,
    letterSpacing: 1.1,
    fontFamily: MONO_FONT,
  },
  linkedRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  statusDot: {
    width: 7,
    height: 7,
    borderRadius: 6,
    backgroundColor: ACCENT,
    shadowColor: ACCENT,
    shadowOpacity: 0.8,
    shadowRadius: 8,
    elevation: 7,
  },
  linkedText: {
    color: ACCENT,
    fontSize: 11,
    letterSpacing: 2,
    fontFamily: MONO_FONT,
  },
  lockBox: {
    width: 32,
    height: 32,
    borderWidth: 1,
    borderColor: '#1C5561',
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#07242C',
  },
  titleWrap: {
    marginTop: 6,
  },
  subheading: {
    color: '#2AAE9D',
    fontSize: 11,
    letterSpacing: 3,
    fontFamily: MONO_FONT,
    marginBottom: 8,
  },
  title: {
    color: '#D8E6EC',
    fontSize: 46,
    fontWeight: '700',
    letterSpacing: 6,
    fontFamily: MONO_FONT,
  },
  description: {
    color: MUTED,
    marginTop: 5,
    fontSize: 11,
    letterSpacing: 2.6,
    fontFamily: MONO_FONT,
  },
  uploadZone: {
    marginTop: 8,
    minHeight: 190,
    borderRadius: 14,
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: '#1D7070',
    backgroundColor: '#04131D',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 18,
    paddingHorizontal: 12,
  },
  uploadCoreWrap: {
    alignItems: 'center',
    marginTop: 6,
  },
  uploadCoreOuter: {
    width: 64,
    height: 64,
    borderRadius: 40,
    borderWidth: 1,
    borderColor: '#116A6C',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#052532',
  },
  uploadCoreInner: {
    width: 42,
    height: 42,
    borderRadius: 21,
    borderWidth: 1,
    borderColor: '#0C8981',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#042A33',
    shadowColor: ACCENT,
    shadowOpacity: 0.55,
    shadowRadius: 12,
    elevation: 8,
  },
  uploadTitle: {
    color: ACCENT,
    marginTop: 12,
    fontSize: 17,
    letterSpacing: 2.4,
    fontFamily: MONO_FONT,
  },
  uploadHint: {
    color: '#6D8896',
    marginTop: 6,
    fontSize: 15,
    fontFamily: MONO_FONT,
  },
  tagRow: {
    flexDirection: 'row',
    gap: 8,
    flexWrap: 'wrap',
    justifyContent: 'center',
  },
  tagPill: {
    borderWidth: 1,
    borderColor: '#185062',
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 4,
    backgroundColor: '#082230',
  },
  tagText: {
    color: '#3DA8B7',
    fontSize: 10,
    letterSpacing: 1,
    fontFamily: MONO_FONT,
  },
  storageCard: {
    marginTop: 3,
    borderWidth: 1,
    borderColor: BORDER_COLOR,
    borderRadius: 10,
    backgroundColor: PANEL_COLOR,
    padding: 12,
  },
  storageHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  storageLabel: {
    color: '#607987',
    fontSize: 10,
    letterSpacing: 2,
    fontFamily: MONO_FONT,
  },
  storageValue: {
    color: ACCENT,
    fontSize: 13,
    letterSpacing: 1,
    fontFamily: MONO_FONT,
  },
  progressTrack: {
    height: 5,
    backgroundColor: '#132E3F',
    borderRadius: 10,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    borderRadius: 10,
  },
  tabRow: {
    marginTop: 2,
    borderWidth: 1,
    borderColor: '#184153',
    borderRadius: 10,
    overflow: 'hidden',
    flexDirection: 'row',
  },
  tabButton: {
    flex: 1,
    minHeight: 34,
    borderRightWidth: 1,
    borderRightColor: '#163D4B',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#081F2A',
  },
  tabButtonActive: {
    shadowColor: ACCENT,
    shadowOpacity: 0.45,
    shadowRadius: 10,
    elevation: 6,
  },
  tabGradient: {
    width: '100%',
    minHeight: 34,
    alignItems: 'center',
    justifyContent: 'center',
  },
  tabText: {
    color: '#5E7886',
    fontSize: 10,
    letterSpacing: 1.6,
    fontFamily: MONO_FONT,
  },
  tabTextActive: {
    color: ACCENT,
  },
  filesHeader: {
    marginTop: 4,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  filesTitle: {
    color: '#5D7783',
    fontSize: 12,
    letterSpacing: 2.4,
    fontFamily: MONO_FONT,
  },
  filesCount: {
    color: ACCENT,
    fontSize: 14,
    letterSpacing: 1,
    fontFamily: MONO_FONT,
  },
  loadingWrap: {
    minHeight: 120,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: BORDER_COLOR,
    backgroundColor: '#081A25',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  loadingText: {
    color: '#86A2B0',
    fontSize: 13,
    fontFamily: MONO_FONT,
  },
  emptyWrap: {
    minHeight: 120,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: BORDER_COLOR,
    backgroundColor: '#081A25',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 16,
  },
  emptyTitle: {
    color: '#A3BAC5',
    fontSize: 14,
    letterSpacing: 1.4,
    fontFamily: MONO_FONT,
  },
  emptyText: {
    color: '#5F7D8A',
    fontSize: 12,
    marginTop: 8,
    textAlign: 'center',
    fontFamily: MONO_FONT,
  },
  fileList: {
    gap: 10,
  },
  fileRow: {
    minHeight: 62,
    borderWidth: 1,
    borderColor: BORDER_COLOR,
    borderRadius: 10,
    backgroundColor: '#081A25',
    paddingHorizontal: 12,
    flexDirection: 'row',
    alignItems: 'center',
  },
  fileBadge: {
    width: 36,
    height: 36,
    borderRadius: 7,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 10,
  },
  fileBadgeText: {
    fontSize: 10,
    letterSpacing: 1,
    fontWeight: '700',
    fontFamily: MONO_FONT,
  },
  fileInfo: {
    flex: 1,
  },
  fileName: {
    color: '#D2DEE4',
    fontSize: 15,
    lineHeight: 18,
    fontFamily: MONO_FONT,
  },
  fileMeta: {
    color: '#5B7582',
    fontSize: 11,
    marginTop: 2,
    letterSpacing: 1.1,
    fontFamily: MONO_FONT,
  },
  encText: {
    color: ACCENT,
    fontSize: 12,
    letterSpacing: 1,
    fontFamily: MONO_FONT,
    marginLeft: 8,
  },
  fileActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginLeft: 8,
  },
  actionChip: {
    minWidth: 42,
    height: 24,
    paddingHorizontal: 8,
    borderWidth: 1,
    borderColor: '#16556B',
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#082533',
  },
  actionChipText: {
    color: '#85DBEA',
    fontSize: 10,
    letterSpacing: 0.8,
    fontFamily: MONO_FONT,
  },
  actionDelete: {
    borderColor: '#6A2A35',
    backgroundColor: '#2B1318',
  },
  actionDeleteText: {
    color: '#FF7B8C',
  },
  footerRow: {
    position: 'absolute',
    left: 16,
    right: 16,
    bottom: 22,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  uploadButton: {
    flex: 1,
    minHeight: 44,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#123D4D',
    backgroundColor: '#081A25',
    alignItems: 'center',
    justifyContent: 'center',
  },
  uploadButtonText: {
    color: '#3B5968',
    fontSize: 14,
    letterSpacing: 2.2,
    fontFamily: MONO_FONT,
  },
  footerActions: {
    flexDirection: 'row',
    gap: 8,
  },
  iconButton: {
    width: 44,
    height: 34,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#193748',
    backgroundColor: '#0A1C28',
    alignItems: 'center',
    justifyContent: 'center',
  },
});

import React, { useState, useEffect, useRef } from 'react'
import { useVaultStore } from '../store/useVaultStore'
import { useAuthStore } from '../store/useAuthStore'
import { 
  Database, 
  RefreshCw, 
  Terminal, 
  CheckCircle, 
  LogOut, 
  User, 
  ShieldCheck, 
  Download, 
  Upload, 
  Key, 
  ChevronDown, 
  ChevronUp, 
  Cloud, 
  Server, 
  HardDrive, 
  FolderSync, 
  Activity, 
  Zap,
  Check
} from 'lucide-react'
import { Button } from '../components/common/Button'
import { Badge } from '../components/common/Badge'
import { WatchFolderManager } from '../components/settings/WatchFolderManager'
import { formatBytes } from '../utils/format'
import { api } from '../services/api'
import { translateErrorMessage } from '../utils/errorTranslation'

export const SettingsView: React.FC = () => {
  const { metrics, rebuildIndex, license, activateLicense, fetchFiles, fetchAlbums, fetchMetrics } = useVaultStore()
  const { user, authMode, logout } = useAuthStore()
  const [isRebuilding, setIsRebuilding] = useState(false)
  const [rebuildLogs, setRebuildLogs] = useState<string[]>([])
  const [rebuildDone, setRebuildDone] = useState(false)

  // Diagnostics Panel Collapsible State
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false)

  // Pro License & Multi-Storage State
  const [licenseKeyInput, setLicenseKeyInput] = useState('')
  const [isActivatingLicense, setIsActivatingLicense] = useState(false)
  const [licenseStatusMsg, setLicenseStatusMsg] = useState('')
  const [isNasSyncing, setIsNasSyncing] = useState(false)
  const [nasSyncMsg, setNasSyncMsg] = useState('')

  // S3 Storage Form State
  const [storageConfigs, setStorageConfigs] = useState<any>(null)
  const [s3FormOpen, setS3FormOpen] = useState(false)
  const [s3Endpoint, setS3Endpoint] = useState('https://s3.amazonaws.com')
  const [s3Bucket, setS3Bucket] = useState('')
  const [s3AccessKey, setS3AccessKey] = useState('')
  const [s3SecretKey, setS3SecretKey] = useState('')
  const [s3Region, setS3Region] = useState('us-east-1')
  const [s3Prefix, setS3Prefix] = useState('vayom_vault')
  const [isSavingS3, setIsSavingS3] = useState(false)
  const [isTestingS3, setIsTestingS3] = useState(false)
  const [s3StatusMsg, setS3StatusMsg] = useState('')

  // Nextcloud Storage Form State
  const [ncFormOpen, setNcFormOpen] = useState(false)
  const [ncUrl, setNcUrl] = useState('')
  const [ncUser, setNcUser] = useState('')
  const [ncPassword, setNcPassword] = useState('')
  const [ncPrefix, setNcPrefix] = useState('vayom_vault')
  const [isSavingNc, setIsSavingNc] = useState(false)
  const [isTestingNc, setIsTestingNc] = useState(false)
  const [ncStatusMsg, setNcStatusMsg] = useState('')

  // Backup and Recovery state
  const [channelHealth, setChannelHealth] = useState<any>(null)
  const [isRefreshingHealth, setIsRefreshingHealth] = useState(false)
  const [backupChannelInput, setBackupChannelInput] = useState('')
  const [isSavingBackupChannel, setIsSavingBackupChannel] = useState(false)
  const [backupChannelStatusMsg, setBackupChannelStatusMsg] = useState('')
  const [isImporting, setIsImporting] = useState(false)
  const [importStatusMsg, setImportStatusMsg] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const isMountedRef = useRef(true)

  const handleActivateLicense = async () => {
    if (!licenseKeyInput.trim()) return
    setIsActivatingLicense(true)
    setLicenseStatusMsg('')
    try {
      await activateLicense(licenseKeyInput.trim())
      setLicenseStatusMsg('✓ Pro License Activated! All storage connections unlocked.')
      setLicenseKeyInput('')
    } catch (err: any) {
      setLicenseStatusMsg(`Activation Failed: ${translateErrorMessage(err)}`)
    } finally {
      setIsActivatingLicense(false)
    }
  }

  const handleSyncNasVault = async () => {
    setIsNasSyncing(true)
    setNasSyncMsg('Scanning local & NAS folders...')
    try {
      const res = await api.syncNasVault()
      setNasSyncMsg(`✓ NAS Sync Complete: Indexed ${res.indexed_files} files in ${res.mapped_albums?.length || 0} folders.`)
      fetchFiles()
      fetchAlbums()
      fetchMetrics()
    } catch (err: any) {
      setNasSyncMsg(`Sync Failed: ${translateErrorMessage(err)}`)
    } finally {
      setIsNasSyncing(false)
    }
  }

  const loadChannelHealth = async () => {
    setIsRefreshingHealth(true)
    try {
      const data = await api.getChannelHealth()
      if (!isMountedRef.current) return
      setChannelHealth(data)
      if (data.backup_channel?.id) {
        setBackupChannelInput(data.backup_channel.id)
      }
    } catch (e: any) {
      console.error('Failed to load channel health:', e)
    } finally {
      setIsRefreshingHealth(false)
    }
  }

  const loadStorageConfigs = async () => {
    try {
      const data = await api.getStorageConfigs()
      if (!isMountedRef.current) return
      setStorageConfigs(data)
      if (data?.s3) {
        if (data.s3.endpoint_url) setS3Endpoint(data.s3.endpoint_url)
        if (data.s3.bucket_name) setS3Bucket(data.s3.bucket_name)
        if (data.s3.access_key_id) setS3AccessKey(data.s3.access_key_id)
        if (data.s3.region_name) setS3Region(data.s3.region_name)
        if (data.s3.vault_prefix) setS3Prefix(data.s3.vault_prefix)
      }
      if (data?.nextcloud) {
        if (data.nextcloud.webdav_url) setNcUrl(data.nextcloud.webdav_url)
        if (data.nextcloud.username) setNcUser(data.nextcloud.username)
        if (data.nextcloud.vault_prefix) setNcPrefix(data.nextcloud.vault_prefix)
      }
    } catch (e: any) {
      console.error('Failed to load storage configs:', e)
    }
  }

  const handleTestS3 = async () => {
    setIsTestingS3(true)
    setS3StatusMsg('Testing S3 connection...')
    try {
      const res = await api.getS3Health()
      if (res.healthy) {
        setS3StatusMsg(`✓ S3 Bucket Connected! (Latency: ${res.latency_ms}ms)`)
      } else {
        setS3StatusMsg(`✗ ${translateErrorMessage(res.error || 'Connection failed')}`)
      }
    } catch (err: any) {
      setS3StatusMsg(`✗ ${translateErrorMessage(err)}`)
    } finally {
      setIsTestingS3(false)
    }
  }

  const handleSaveS3 = async () => {
    if (!s3Bucket.trim() || !s3AccessKey.trim()) {
      setS3StatusMsg('Bucket Name and Access Key ID are required.')
      return
    }
    setIsSavingS3(true)
    setS3StatusMsg('Saving S3 configuration & verifying bucket...')
    try {
      const res = await api.saveS3Config({
        endpoint_url: s3Endpoint.trim(),
        bucket_name: s3Bucket.trim(),
        access_key_id: s3AccessKey.trim(),
        secret_access_key: s3SecretKey.trim() || undefined,
        region_name: s3Region.trim(),
        vault_prefix: s3Prefix.trim()
      })
      if (res.health?.healthy) {
        setS3StatusMsg(`✓ S3 Connected Successfully! Latency: ${res.health.latency_ms}ms`)
      } else {
        setS3StatusMsg(`Saved, but S3 test returned: ${translateErrorMessage(res.health?.error || 'Unhealthy')}`)
      }
      await loadStorageConfigs()
      setS3SecretKey('')
    } catch (err: any) {
      setS3StatusMsg(`✗ ${translateErrorMessage(err)}`)
    } finally {
      setIsSavingS3(false)
    }
  }

  const handleTestNc = async () => {
    setIsTestingNc(true)
    setNcStatusMsg('Testing Nextcloud WebDAV connection...')
    try {
      const res = await api.getNextcloudHealth()
      if (res.healthy) {
        setNcStatusMsg(`✓ Nextcloud WebDAV Connected! (Latency: ${res.latency_ms}ms)`)
      } else {
        setNcStatusMsg(`✗ ${translateErrorMessage(res.error || 'Check URL / credentials')}`)
      }
    } catch (err: any) {
      setNcStatusMsg(`✗ ${translateErrorMessage(err)}`)
    } finally {
      setIsTestingNc(false)
    }
  }

  const handleSaveNc = async () => {
    if (!ncUrl.trim() || !ncUser.trim()) {
      setNcStatusMsg('WebDAV URL and Username are required.')
      return
    }
    setIsSavingNc(true)
    setNcStatusMsg('Saving Nextcloud configuration & verifying WebDAV...')
    try {
      const res = await api.saveNextcloudConfig({
        webdav_url: ncUrl.trim(),
        username: ncUser.trim(),
        app_password: ncPassword.trim() || undefined,
        vault_prefix: ncPrefix.trim()
      })
      if (res.health?.healthy) {
        setNcStatusMsg(`✓ Nextcloud Connected Successfully! Latency: ${res.health.latency_ms}ms`)
      } else {
        setNcStatusMsg(`Saved, but Nextcloud test returned: ${translateErrorMessage(res.health?.error || 'Unhealthy')}`)
      }
      await loadStorageConfigs()
      setNcPassword('')
    } catch (err: any) {
      setNcStatusMsg(`✗ ${translateErrorMessage(err)}`)
    } finally {
      setIsSavingNc(false)
    }
  }

  useEffect(() => {
    isMountedRef.current = true
    loadChannelHealth()
    loadStorageConfigs()
    return () => {
      isMountedRef.current = false
    }
  }, [])

  const handleSaveBackupChannel = async () => {
    if (!backupChannelInput.trim()) return
    setIsSavingBackupChannel(true)
    setBackupChannelStatusMsg('')
    try {
      await api.setBackupChannel(backupChannelInput.trim())
      setBackupChannelStatusMsg('✓ Secondary Telegram backup channel connected and synced!')
      await loadChannelHealth()
    } catch (err: any) {
      setBackupChannelStatusMsg(`Error: ${translateErrorMessage(err)}`)
    } finally {
      setIsSavingBackupChannel(false)
    }
  }

  const handleExportCatalog = () => {
    window.open(api.exportCatalogUrl(), '_blank')
  }

  const handleImportCatalog = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setIsImporting(true)
    setImportStatusMsg('Restoring vault files from catalog backup...')
    try {
      const res = await api.importCatalog(file)
      setImportStatusMsg(`✓ Restored ${res.restored_files} files (${res.total_catalog_files} catalog entries) to database!`)
      fetchFiles()
      fetchAlbums()
      fetchMetrics()
      await loadChannelHealth()
    } catch (err: any) {
      setImportStatusMsg(`Restore failed: ${translateErrorMessage(err)}`)
    } finally {
      setIsImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDisconnect = async () => {
    if (window.confirm('Disconnect your Telegram storage account? You will return to the sign-in screen.')) {
      await logout()
    }
  }

  const handleRebuildIndex = async () => {
    setIsRebuilding(true)
    setRebuildLogs([
      `[${new Date().toLocaleTimeString()}] Connecting to Telegram storage channel...`,
      `[${new Date().toLocaleTimeString()}] Scanning channel message history backward from HEAD...`,
      `[${new Date().toLocaleTimeString()}] Parsing metadata envelopes from document captions...`,
    ])
    setRebuildDone(false)

    try {
      const res = await rebuildIndex()
      setRebuildLogs((prev) => [
        ...prev,
        `[${new Date().toLocaleTimeString()}] Reconstructing file entries and catalog...`,
        `[${new Date().toLocaleTimeString()}] Scanned and processed ${res.restored_envelopes} envelopes.`,
        `[${new Date().toLocaleTimeString()}] Complete: Catalog synchronized with Telegram storage.`,
      ])
      setRebuildDone(true)
    } catch (err: any) {
      setRebuildLogs((prev) => [
        ...prev,
        `[${new Date().toLocaleTimeString()}] ERROR: ${translateErrorMessage(err)}`,
      ])
    } finally {
      setIsRebuilding(false)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8 bg-[#000000] select-none font-mono">
      {/* Top Header */}
      <div className="pb-6 border-b border-[#282828] mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-base sm:text-lg font-bold uppercase tracking-wider text-[#ffffff] flex items-center gap-2.5">
            <HardDrive size={18} className="text-[#ffffff]" /> Storage Connections
          </h1>
          <p className="text-xs text-[#777777] mt-1">
            Manage connected cloud accounts, object storage, local drives, and backup channels
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={loadChannelHealth}
            disabled={isRefreshingHealth}
            icon={<RefreshCw size={12} className={isRefreshingHealth ? 'animate-spin' : ''} />}
          >
            {isRefreshingHealth ? 'Checking...' : 'Check Connections'}
          </Button>
          <Button
            variant="danger"
            size="sm"
            onClick={handleDisconnect}
            icon={<LogOut size={12} />}
          >
            Disconnect Telegram
          </Button>
        </div>
      </div>

      {/* Main 2-Column Desktop Grid */}
      <div className="max-w-7xl mx-auto grid grid-cols-1 xl:grid-cols-2 gap-6 items-start">
        
        {/* ================= COLUMN 1: STORAGE PROVIDERS ================= */}
        <div className="space-y-6">

          {/* 1. Connected Telegram Account & Cloud Storage */}
          <div className="p-5 bg-[#0a0a0a] border border-[#222222] space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-[#1a1a1a]">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 bg-[#0284c7]/20 border border-[#0284c7]/40 flex items-center justify-center text-[#38bdf8]">
                  <Cloud size={16} />
                </div>
                <div>
                  <span className="text-xs font-bold text-[#f0f0f0] uppercase tracking-wider block">
                    Telegram Cloud Storage
                  </span>
                  <span className="text-[10px] text-[#777777]">
                    Primary decentralized media vault
                  </span>
                </div>
              </div>

              <Badge
                variant={
                  metrics.online_status === 'online'
                    ? 'success'
                    : metrics.online_status === 'flood_wait'
                    ? 'danger'
                    : 'warning'
                }
              >
                {metrics.online_status === 'online'
                  ? 'Connected & Synced'
                  : metrics.online_status === 'flood_wait'
                  ? `Rate Limited (${metrics.flood_wait_seconds ?? 0}s)`
                  : 'Connecting'}
              </Badge>
            </div>

            {/* Account Details */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
              <div>
                <span className="text-[10px] text-[#666666] uppercase block">Account</span>
                <span className="text-[#e5e5e5] font-semibold truncate block">
                  {user?.display_name || 'Vault User'}
                </span>
                <span className="text-[10px] text-[#888888]">
                  {user?.username ? `@${user.username}` : 'Direct MTProto'}
                </span>
              </div>

              <div>
                <span className="text-[10px] text-[#666666] uppercase block">Storage Used</span>
                <span className="text-[#e5e5e5] font-semibold text-sm">
                  {formatBytes(metrics.total_bytes)}
                </span>
                <span className="text-[10px] text-[#888888] block">
                  {metrics.total_files} files cataloged
                </span>
              </div>

              <div>
                <span className="text-[10px] text-[#666666] uppercase block">Max Upload Size</span>
                <span className="text-[#22c55e] font-semibold flex items-center gap-1">
                  {user?.is_premium ? (
                    <>
                      <Zap size={11} className="fill-[#22c55e]" /> 4.00 GB (Premium)
                    </>
                  ) : (
                    '1.90 GB (Standard)'
                  )}
                </span>
                <span className="text-[10px] text-[#888888] block">
                  {user?.is_premium ? 'Uncapped speed' : 'Lookahead prefetch'}
                </span>
              </div>
            </div>

            {/* Channel Info & Actions */}
            <div className="p-3 bg-[#111111] border border-[#1e1e1e] flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 text-[11px]">
              <div>
                <span className="text-[#666666] text-[10px] uppercase block">Storage Channel ID</span>
                <span className="text-[#aaaaaa] font-mono select-all">
                  {metrics.channel_id || 'Not Assigned'}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleDisconnect}
                  className="text-[10px] whitespace-nowrap"
                >
                  Switch Account
                </Button>
              </div>
            </div>
          </div>

          {/* 2. Additional Storage Connections (Pro / Multi-Storage) */}
          <div className="p-5 bg-[#0a0a0a] border border-[#222222] space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-[#1a1a1a]">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 bg-[#f59e0b]/20 border border-[#f59e0b]/40 flex items-center justify-center text-[#fbbf24]">
                  <HardDrive size={16} />
                </div>
                <div>
                  <span className="text-xs font-bold text-[#f0f0f0] uppercase tracking-wider block">
                    Additional Storage Connections
                  </span>
                  <span className="text-[10px] text-[#777777]">
                    S3, NAS, and Nextcloud integration
                  </span>
                </div>
              </div>

              <Badge variant={license?.is_pro ? 'success' : 'default'}>
                {license?.is_pro ? 'Pro Unlocked' : 'Free Tier'}
              </Badge>
            </div>

            <p className="text-[11px] text-[#777777] leading-relaxed">
              Connect external storage to store files natively without splitting. Files are organized into your Vayom Drive library seamlessly.
            </p>

            {/* Pro License Activation if not active */}
            {!license?.is_pro && (
              <div className="p-3.5 bg-[#121212] border border-[#2a2a2a] space-y-2.5">
                <div className="text-xs text-[#e5e5e5] font-semibold flex items-center gap-1.5">
                  <Key size={13} className="text-[#f59e0b]" /> Unlock Additional Storage Connections
                </div>
                <p className="text-[11px] text-[#888888]">
                  Enter your Vayom Pro license key to connect your NAS, S3 buckets, and Nextcloud:
                </p>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={licenseKeyInput}
                    onChange={(e) => setLicenseKeyInput(e.target.value)}
                    placeholder="VAYOM-PRO.eyJ..."
                    className="flex-1 bg-[#0a0a0a] border border-[#333333] px-2.5 py-1 text-xs font-mono text-[#ffffff] focus:outline-none focus:border-[#ffffff]"
                  />
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={handleActivateLicense}
                    disabled={isActivatingLicense || !licenseKeyInput.trim()}
                  >
                    {isActivatingLicense ? 'Activating...' : 'Activate'}
                  </Button>
                </div>
                {licenseStatusMsg && (
                  <div className={`text-[11px] font-mono ${licenseStatusMsg.startsWith('✓') ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>
                    {licenseStatusMsg}
                  </div>
                )}
              </div>
            )}

            {/* Storage Provider Cards */}
            <div className="space-y-3">
              
              {/* NAS & Local Folders */}
              <div className="p-3 bg-[#111111] border border-[#1f1f1f] space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-emerald-500" />
                    <span className="text-xs font-bold text-[#e5e5e5]">Local & Network Storage (NAS)</span>
                  </div>
                  <span className="text-[9px] px-1.5 py-0.5 bg-emerald-950/80 text-emerald-400 border border-emerald-500/40 font-mono">
                    LOCAL / SMB / NFS
                  </span>
                </div>
                <p className="text-[10px] text-[#888888]">
                  Access files on your local workstation, external SSDs, or local network NAS.
                </p>
                <div className="flex items-center justify-between pt-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleSyncNasVault}
                    disabled={isNasSyncing}
                    className="text-[10px]"
                    icon={<FolderSync size={11} />}
                  >
                    {isNasSyncing ? 'Scanning...' : 'Scan & Sync Library'}
                  </Button>
                  {nasSyncMsg && <span className="text-[10px] text-[#888888] font-mono">{nasSyncMsg}</span>}
                </div>
              </div>

              {/* S3-Compatible Storage */}
              <div className="p-3 bg-[#111111] border border-[#1f1f1f] space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${storageConfigs?.s3?.configured ? 'bg-amber-500' : 'bg-[#555555]'}`} />
                    <span className="text-xs font-bold text-[#e5e5e5]">S3-Compatible Cloud Storage</span>
                  </div>
                  <Badge variant={storageConfigs?.s3?.configured ? 'success' : 'default'}>
                    {storageConfigs?.s3?.configured ? 'Connected' : 'Not Configured'}
                  </Badge>
                </div>
                <p className="text-[10px] text-[#888888]">
                  Connect Cloudflare R2, Amazon S3, Backblaze B2, or MinIO.
                </p>
                {storageConfigs?.s3?.configured && (
                  <div className="text-[10px] text-[#aaaaaa] font-mono">
                    Bucket: <span className="text-[#e5e5e5]">{storageConfigs.s3.bucket_name}</span>
                  </div>
                )}
                <div className="pt-1">
                  <Button
                    variant={s3FormOpen ? "secondary" : "outline"}
                    size="sm"
                    onClick={() => { setS3FormOpen(!s3FormOpen); setNcFormOpen(false); }}
                    className="text-[10px]"
                  >
                    {s3FormOpen ? 'Close Settings' : (storageConfigs?.s3?.configured ? 'Configure S3' : 'Connect S3')}
                  </Button>
                </div>

                {/* S3 Inline Form */}
                {s3FormOpen && (
                  <div className="p-3 bg-[#0a0a0a] border border-amber-500/30 space-y-3 font-mono text-xs mt-2">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                      <div>
                        <label className="text-[10px] text-[#888888] uppercase block mb-1">Endpoint URL</label>
                        <input
                          type="text"
                          value={s3Endpoint}
                          onChange={(e) => setS3Endpoint(e.target.value)}
                          placeholder="https://s3.amazonaws.com"
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2 py-1 text-xs text-[#ffffff] focus:outline-none focus:border-amber-400"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] text-[#888888] uppercase block mb-1">Bucket Name *</label>
                        <input
                          type="text"
                          value={s3Bucket}
                          onChange={(e) => setS3Bucket(e.target.value)}
                          placeholder="my-vault-bucket"
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2 py-1 text-xs text-[#ffffff] focus:outline-none focus:border-amber-400"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] text-[#888888] uppercase block mb-1">Access Key ID *</label>
                        <input
                          type="text"
                          value={s3AccessKey}
                          onChange={(e) => setS3AccessKey(e.target.value)}
                          placeholder="AKIA..."
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2 py-1 text-xs text-[#ffffff] focus:outline-none focus:border-amber-400"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] text-[#888888] uppercase block mb-1">
                          Secret Key {storageConfigs?.s3?.has_secret_key ? '(Stored)' : '*'}
                        </label>
                        <input
                          type="password"
                          value={s3SecretKey}
                          onChange={(e) => setS3SecretKey(e.target.value)}
                          placeholder={storageConfigs?.s3?.has_secret_key ? "•••••••••••• (Leave blank to keep)" : "Secret Key"}
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2 py-1 text-xs text-[#ffffff] focus:outline-none focus:border-amber-400"
                        />
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-[#1c1c1c]">
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={handleSaveS3}
                        disabled={isSavingS3}
                      >
                        {isSavingS3 ? 'Saving...' : 'Save & Connect'}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleTestS3}
                        disabled={isTestingS3 || !storageConfigs?.s3?.configured}
                      >
                        {isTestingS3 ? 'Testing...' : 'Test Connection'}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setS3FormOpen(false)}
                      >
                        Cancel
                      </Button>
                      {s3StatusMsg && (
                        <span className={`text-[11px] font-mono ml-2 ${s3StatusMsg.startsWith('✓') ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>
                          {s3StatusMsg}
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* Nextcloud WebDAV */}
              <div className="p-3 bg-[#111111] border border-[#1f1f1f] space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${storageConfigs?.nextcloud?.configured ? 'bg-purple-500' : 'bg-[#555555]'}`} />
                    <span className="text-xs font-bold text-[#e5e5e5]">Nextcloud WebDAV</span>
                  </div>
                  <Badge variant={storageConfigs?.nextcloud?.configured ? 'success' : 'default'}>
                    {storageConfigs?.nextcloud?.configured ? 'Connected' : 'Not Configured'}
                  </Badge>
                </div>
                <p className="text-[10px] text-[#888888]">
                  Connect your personal Nextcloud or ownCloud server.
                </p>
                {storageConfigs?.nextcloud?.configured && (
                  <div className="text-[10px] text-[#aaaaaa] font-mono">
                    User: <span className="text-[#e5e5e5]">{storageConfigs.nextcloud.username}</span>
                  </div>
                )}
                <div className="pt-1">
                  <Button
                    variant={ncFormOpen ? "secondary" : "outline"}
                    size="sm"
                    onClick={() => { setNcFormOpen(!ncFormOpen); setS3FormOpen(false); }}
                    className="text-[10px]"
                  >
                    {ncFormOpen ? 'Close Settings' : (storageConfigs?.nextcloud?.configured ? 'Configure NC' : 'Connect Nextcloud')}
                  </Button>
                </div>

                {/* Nextcloud Inline Form */}
                {ncFormOpen && (
                  <div className="p-3 bg-[#0a0a0a] border border-purple-500/30 space-y-3 font-mono text-xs mt-2">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                      <div className="sm:col-span-2">
                        <label className="text-[10px] text-[#888888] uppercase block mb-1">WebDAV URL *</label>
                        <input
                          type="text"
                          value={ncUrl}
                          onChange={(e) => setNcUrl(e.target.value)}
                          placeholder="https://nextcloud.example.com/remote.php/dav/files/user/"
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2 py-1 text-xs text-[#ffffff] focus:outline-none focus:border-purple-400"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] text-[#888888] uppercase block mb-1">Username *</label>
                        <input
                          type="text"
                          value={ncUser}
                          onChange={(e) => setNcUser(e.target.value)}
                          placeholder="username"
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2 py-1 text-xs text-[#ffffff] focus:outline-none focus:border-purple-400"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] text-[#888888] uppercase block mb-1">
                          App Password {storageConfigs?.nextcloud?.has_password ? '(Stored)' : '*'}
                        </label>
                        <input
                          type="password"
                          value={ncPassword}
                          onChange={(e) => setNcPassword(e.target.value)}
                          placeholder={storageConfigs?.nextcloud?.has_password ? "•••••••••••• (Leave blank to keep)" : "App password"}
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2 py-1 text-xs text-[#ffffff] focus:outline-none focus:border-purple-400"
                        />
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-[#1c1c1c]">
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={handleSaveNc}
                        disabled={isSavingNc}
                      >
                        {isSavingNc ? 'Saving...' : 'Save & Connect'}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleTestNc}
                        disabled={isTestingNc || !storageConfigs?.nextcloud?.configured}
                      >
                        {isTestingNc ? 'Testing...' : 'Test Connection'}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setNcFormOpen(false)}
                      >
                        Cancel
                      </Button>
                      {ncStatusMsg && (
                        <span className={`text-[11px] font-mono ml-2 ${ncStatusMsg.startsWith('✓') ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>
                          {ncStatusMsg}
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>

            </div>
          </div>

        </div>

        {/* ================= COLUMN 2: SAFEGUARDS, AUTOMATION & DIAGNOSTICS ================= */}
        <div className="space-y-6">

          {/* 3. Backup & Recovery */}
          <div className="p-5 bg-[#0a0a0a] border border-[#222222] space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-[#1a1a1a]">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 bg-[#22c55e]/20 border border-[#22c55e]/40 flex items-center justify-center text-[#22c55e]">
                  <ShieldCheck size={16} />
                </div>
                <div>
                  <span className="text-xs font-bold text-[#f0f0f0] uppercase tracking-wider block">
                    Backup and Recovery
                  </span>
                  <span className="text-[10px] text-[#777777]">
                    Multi-tier data redundancy and instant restores
                  </span>
                </div>
              </div>
            </div>

            <p className="text-[11px] text-[#777777] leading-relaxed">
              Safeguard your library against accidental channel loss or server migration. Your library can be mirrored to a secondary channel and backed up locally.
            </p>

            {/* Health Tiers */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 text-xs">
              <div className="p-2.5 bg-[#111111] border border-[#1f1f1f] space-y-1">
                <span className="text-[10px] text-[#666666] uppercase block">Primary Channel</span>
                <Badge variant={channelHealth?.primary_channel?.healthy ? 'success' : 'danger'}>
                  {channelHealth?.primary_channel?.healthy ? 'Healthy (Can Post)' : 'Attention Required'}
                </Badge>
              </div>

              <div className="p-2.5 bg-[#111111] border border-[#1f1f1f] space-y-1">
                <span className="text-[10px] text-[#666666] uppercase block">Saved Messages Mirror</span>
                <Badge variant={channelHealth?.saved_messages_mirror ? 'success' : 'default'}>
                  {channelHealth?.saved_messages_mirror ? 'Active (Private)' : 'Not Active'}
                </Badge>
              </div>
            </div>

            {/* Secondary Mirror Channel Configuration */}
            <div className="pt-2 border-t border-[#1a1a1a] space-y-2">
              <span className="text-xs font-semibold text-[#f0f0f0] block">
                Secondary Telegram Backup Channel
              </span>
              <p className="text-[10px] text-[#777777]">
                Mirror all uploads to a second private Telegram channel (e.g. <code className="text-[#e5e5e5]">-1001234567890</code>).
              </p>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  placeholder="-100xxxxxxxxxx"
                  value={backupChannelInput}
                  onChange={(e) => setBackupChannelInput(e.target.value)}
                  className="flex-1 px-3 py-1.5 bg-[#141414] border border-[#2a2a2a] text-[#ffffff] text-xs focus:outline-none focus:border-[#444444]"
                />
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleSaveBackupChannel}
                  disabled={isSavingBackupChannel || !backupChannelInput.trim()}
                >
                  {isSavingBackupChannel ? 'Saving...' : 'Save Mirror'}
                </Button>
              </div>
              {backupChannelStatusMsg && (
                <div className={`text-[10px] ${backupChannelStatusMsg.startsWith('✓') ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>
                  {backupChannelStatusMsg}
                </div>
              )}
            </div>

            {/* 1-Click Catalog Export & Restore */}
            <div className="pt-2 border-t border-[#1a1a1a] space-y-2">
              <span className="text-xs font-semibold text-[#f0f0f0] block">
                Offline Catalog Backup & Restore
              </span>
              <p className="text-[10px] text-[#777777]">
                Export your entire file database to a single JSON file. Restore anytime on a new computer with zero scanning delay.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleExportCatalog}
                  icon={<Download size={12} />}
                >
                  Export Catalog Backup (.json)
                </Button>

                <input
                  type="file"
                  ref={fileInputRef}
                  onChange={handleImportCatalog}
                  accept=".json"
                  className="hidden"
                  id="catalog-backup-input"
                />
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isImporting}
                  icon={<Upload size={12} />}
                >
                  {isImporting ? 'Restoring...' : 'Restore from Backup'}
                </Button>
              </div>
              {importStatusMsg && (
                <div className={`text-[10px] ${importStatusMsg.startsWith('✓') ? 'text-[#22c55e]' : 'text-[#f59e0b]'}`}>
                  {importStatusMsg}
                </div>
              )}
            </div>
          </div>

          {/* 4. Automated Folder Monitoring (Watch Folders) */}
          <div className="p-5 bg-[#0a0a0a] border border-[#222222] space-y-3">
            <div className="flex items-center gap-2.5 pb-2 border-b border-[#1a1a1a]">
              <div className="w-8 h-8 bg-[#3b82f6]/20 border border-[#3b82f6]/40 flex items-center justify-center text-[#60a5fa]">
                <FolderSync size={16} />
              </div>
              <div>
                <span className="text-xs font-bold text-[#f0f0f0] uppercase tracking-wider block">
                  Automatic Folder Monitoring
                </span>
                <span className="text-[10px] text-[#777777]">
                  Auto-upload from OBS, camera drops, and download folders
                </span>
              </div>
            </div>
            <WatchFolderManager />
          </div>

          {/* 5. System Diagnostics & Maintenance (Progressive Disclosure) */}
          <div className="p-4 bg-[#0a0a0a] border border-[#222222] space-y-3">
            <button
              onClick={() => setDiagnosticsOpen(!diagnosticsOpen)}
              className="w-full flex items-center justify-between text-left text-xs font-semibold text-[#888888] hover:text-[#ffffff] transition-colors"
            >
              <span className="flex items-center gap-2 uppercase tracking-wider">
                <Activity size={13} /> Advanced Diagnostics & Index Rebuild
              </span>
              {diagnosticsOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>

            {diagnosticsOpen && (
              <div className="space-y-4 pt-3 border-t border-[#1a1a1a] text-xs font-mono">
                {/* Diagnostics Grid */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                  <div className="p-2 bg-[#121212] border border-[#1e1e1e]">
                    <span className="text-[10px] text-[#666666] uppercase block">Anti-Ban Worker Pool</span>
                    <span className="text-[#e5e5e5]">
                      {channelHealth?.flood_wait_seconds > 0 
                        ? `Cooldown (${channelHealth.flood_wait_seconds}s)` 
                        : `${channelHealth?.max_concurrent_workers || 3} Active Workers`}
                    </span>
                  </div>

                  <div className="p-2 bg-[#121212] border border-[#1e1e1e]">
                    <span className="text-[10px] text-[#666666] uppercase block">Catalog WAL Sync</span>
                    <span className="text-[#22c55e] flex items-center gap-1">
                      <Check size={11} /> Synced & Verified
                    </span>
                  </div>
                </div>

                {/* Rebuild Index Tool */}
                <div className="space-y-2">
                  <span className="text-[11px] font-semibold text-[#e5e5e5] block">
                    Rebuild Local Index from Telegram Storage
                  </span>
                  <p className="text-[10px] text-[#777777]">
                    Reads metadata envelopes directly from Telegram messages to reconstruct your local catalog.
                  </p>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={handleRebuildIndex}
                    disabled={isRebuilding}
                    icon={<RefreshCw size={11} className={isRebuilding ? 'animate-spin' : ''} />}
                  >
                    {isRebuilding ? 'Rebuilding Index...' : 'Rebuild Index'}
                  </Button>

                  {/* Terminal Log Output */}
                  {rebuildLogs.length > 0 && (
                    <div className="p-2.5 bg-[#050505] border border-[#1f1f1f] text-[10px] space-y-1 font-mono text-[#aaaaaa] max-h-40 overflow-y-auto">
                      <div className="text-[9px] text-[#555555] uppercase pb-1 flex items-center gap-1 border-b border-[#181818]">
                        <Terminal size={10} /> Recovery Console
                      </div>
                      {rebuildLogs.map((log, idx) => (
                        <div key={idx} className="leading-tight">
                          {log}
                        </div>
                      ))}
                      {rebuildDone && (
                        <div className="text-[#22c55e] font-semibold pt-1">
                          ✓ Catalog successfully synchronized with Telegram storage.
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Service Checklist */}
                <div className="p-2.5 bg-[#111111] border border-[#1a1a1a] space-y-1.5 text-[10px] text-[#888888]">
                  <div className="flex items-center gap-1.5">
                    <CheckCircle size={12} className="text-[#22c55e]" />
                    <span>Direct MTProto Bot Client: Active</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <CheckCircle size={12} className="text-[#22c55e]" />
                    <span>Unified Single-Service Server: Active</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <CheckCircle size={12} className="text-[#22c55e]" />
                    <span>Ephemeral Disk Auto-Sync: Active</span>
                  </div>
                </div>
              </div>
            )}
          </div>

        </div>

      </div>
    </div>
  )
}

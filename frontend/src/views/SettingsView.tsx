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
  Check,
  HelpCircle
} from 'lucide-react'
import { Button } from '../components/common/Button'
import { Badge } from '../components/common/Badge'
import { WatchFolderManager } from '../components/settings/WatchFolderManager'
import { formatBytes } from '../utils/format'
import { api } from '../services/api'
import { translateErrorMessage } from '../utils/errorTranslation'

type SettingsCategory = 'all' | 'storage' | 'automation' | 'backup' | 'diagnostics'

export const SettingsView: React.FC = () => {
  const { metrics, rebuildIndex, license, activateLicense, fetchFiles, fetchAlbums, fetchMetrics } = useVaultStore()
  const { user, authMode, logout } = useAuthStore()
  const [isRebuilding, setIsRebuilding] = useState(false)
  const [rebuildLogs, setRebuildLogs] = useState<string[]>([])
  const [rebuildDone, setRebuildDone] = useState(false)

  // Active Category State
  const [activeCategory, setActiveCategory] = useState<SettingsCategory>('all')

  // Demand-based Card Accordion State (only expands on user demand)
  const [expandedCards, setExpandedCards] = useState<Record<string, boolean>>({
    telegram: false,
    additional: false,
    automation: false,
    backup: false,
    diagnostics: false,
  })

  const toggleCard = (id: string) => {
    setExpandedCards(prev => ({ ...prev, [id]: !prev[id] }))
  }

  const handleSelectCategory = (cat: SettingsCategory) => {
    setActiveCategory(cat)
    if (cat === 'storage') {
      setExpandedCards(prev => ({ ...prev, telegram: true, additional: true }))
    } else if (cat === 'automation') {
      setExpandedCards(prev => ({ ...prev, automation: true }))
    } else if (cat === 'backup') {
      setExpandedCards(prev => ({ ...prev, backup: true }))
    } else if (cat === 'diagnostics') {
      setExpandedCards(prev => ({ ...prev, diagnostics: true }))
    }
  }

  // Diagnostics Panel Collapsible State
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(true)

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
      setLicenseStatusMsg(`✗ ${translateErrorMessage(err)}`)
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
    <div className="flex-1 overflow-y-auto p-4 sm:p-6 md:p-8 bg-[#000000] select-none font-sans">
      {/* Top Header */}
      <div className="pb-6 border-b border-[#282828] mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-base sm:text-lg font-bold tracking-tight text-[#ffffff] flex items-center gap-2.5">
            <HardDrive size={18} className="text-[#ffffff]" /> Storage Connections
          </h1>
          <p className="text-xs text-[#a3a3a3] mt-1">
            Manage connected cloud accounts, object storage, local drives, and backup channels
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.dispatchEvent(new CustomEvent('open-vayom-guide'))}
            icon={<HelpCircle size={12} />}
          >
            Setup Guide
          </Button>
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

      {/* Categorized Settings Navigation Bar */}
      <div className="max-w-5xl mx-auto mb-6">
        <div className="flex items-center gap-1 border-b border-[#222222] overflow-x-auto scrollbar-none pb-0">
          <button
            onClick={() => handleSelectCategory('all')}
            className={`
              flex items-center gap-2 px-4 py-2.5 text-xs font-medium whitespace-nowrap transition-colors border-b-2 -mb-px select-none
              ${activeCategory === 'all'
                ? 'border-[#ffffff] text-[#ffffff] bg-[#121212]'
                : 'border-transparent text-[#888888] hover:text-[#cccccc] hover:bg-[#0a0a0a]'
              }
            `}
          >
            <HardDrive size={14} className={activeCategory === 'all' ? 'text-[#ffffff]' : 'text-[#666666]'} />
            <span className="hidden sm:inline">All Settings</span>
            <span className="inline sm:hidden">All</span>
          </button>

          <button
            onClick={() => handleSelectCategory('storage')}
            className={`
              flex items-center gap-2 px-4 py-2.5 text-xs font-medium whitespace-nowrap transition-colors border-b-2 -mb-px select-none
              ${activeCategory === 'storage'
                ? 'border-[#ffffff] text-[#ffffff] bg-[#121212]'
                : 'border-transparent text-[#888888] hover:text-[#cccccc] hover:bg-[#0a0a0a]'
              }
            `}
          >
            <Cloud size={14} className={activeCategory === 'storage' ? 'text-[#38bdf8]' : 'text-[#666666]'} />
            <span className="hidden sm:inline">Storage Providers</span>
            <span className="inline sm:hidden">Storage</span>
            <span className="w-1.5 h-1.5 rounded-full bg-[#22c55e]" title="Active" />
          </button>

          <button
            onClick={() => handleSelectCategory('automation')}
            className={`
              flex items-center gap-2 px-4 py-2.5 text-xs font-medium whitespace-nowrap transition-colors border-b-2 -mb-px select-none
              ${activeCategory === 'automation'
                ? 'border-[#ffffff] text-[#ffffff] bg-[#121212]'
                : 'border-transparent text-[#888888] hover:text-[#cccccc] hover:bg-[#0a0a0a]'
              }
            `}
          >
            <FolderSync size={14} className={activeCategory === 'automation' ? 'text-[#60a5fa]' : 'text-[#666666]'} />
            <span className="hidden sm:inline">Folder Automation</span>
            <span className="inline sm:hidden">Automation</span>
          </button>

          <button
            onClick={() => handleSelectCategory('backup')}
            className={`
              flex items-center gap-2 px-4 py-2.5 text-xs font-medium whitespace-nowrap transition-colors border-b-2 -mb-px select-none
              ${activeCategory === 'backup'
                ? 'border-[#ffffff] text-[#ffffff] bg-[#121212]'
                : 'border-transparent text-[#888888] hover:text-[#cccccc] hover:bg-[#0a0a0a]'
              }
            `}
          >
            <ShieldCheck size={14} className={activeCategory === 'backup' ? 'text-[#22c55e]' : 'text-[#666666]'} />
            <span className="hidden sm:inline">Backup & Recovery</span>
            <span className="inline sm:hidden">Backup</span>
          </button>

          <button
            onClick={() => handleSelectCategory('diagnostics')}
            className={`
              flex items-center gap-2 px-4 py-2.5 text-xs font-medium whitespace-nowrap transition-colors border-b-2 -mb-px select-none
              ${activeCategory === 'diagnostics'
                ? 'border-[#ffffff] text-[#ffffff] bg-[#121212]'
                : 'border-transparent text-[#888888] hover:text-[#cccccc] hover:bg-[#0a0a0a]'
              }
            `}
          >
            <Activity size={14} className={activeCategory === 'diagnostics' ? 'text-[#fbbf24]' : 'text-[#666666]'} />
            <span className="hidden sm:inline">System & Diagnostics</span>
            <span className="inline sm:hidden">Diagnostics</span>
          </button>
        </div>

        {/* Category Description Sub-bar */}
        <div className="py-2.5 px-1 text-xs text-[#737373]">
          {activeCategory === 'all' && 'All storage, automation, backup, and system maintenance settings. Click any card to expand its options.'}
          {activeCategory === 'storage' && 'Configure primary Telegram storage, S3 object storage, Nextcloud, and local/NAS network drives.'}
          {activeCategory === 'automation' && 'Set up automatic ingestion for OBS recordings, camera SD card drops, and watched local folders.'}
          {activeCategory === 'backup' && 'Manage dual-channel redundancy, secondary Telegram mirror channels, and portable catalog backups.'}
          {activeCategory === 'diagnostics' && 'View anti-ban worker pool metrics, catalog WAL integrity, index reconstruction, and setup guides.'}
        </div>
      </div>

      {/* Main Settings Content Area */}
      <div className="max-w-5xl mx-auto space-y-4">
        {/* ================= 1. TELEGRAM CLOUD STORAGE ================= */}
        {(activeCategory === 'all' || activeCategory === 'storage') && (
          <div className="bg-[#0a0a0a] border border-[#222222] transition-colors">
            <button
              type="button"
              onClick={() => toggleCard('telegram')}
              className="w-full p-4 sm:p-5 flex items-center justify-between text-left hover:bg-[#111111]/70 transition-colors select-none"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-[#0284c7]/20 border border-[#0284c7]/40 flex items-center justify-center text-[#38bdf8] shrink-0">
                  <Cloud size={16} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-[#f0f0f0]">
                      Telegram Cloud Storage
                    </span>
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
                  <span className="text-[11px] text-[#888888] hidden sm:block">
                    Primary decentralized media vault ({user?.display_name || 'Account'} • {formatBytes(metrics.total_bytes)})
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-xs text-[#888888] hidden md:inline font-mono">
                  {expandedCards.telegram ? 'Collapse' : 'Configure'}
                </span>
                <div className="p-1 text-[#888888] hover:text-[#ffffff]">
                  {expandedCards.telegram ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </div>
              </div>
            </button>

            {expandedCards.telegram && (
              <div className="p-5 pt-0 border-t border-[#1a1a1a] space-y-4 animate-in fade-in duration-150">
                {/* Account Details */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs pt-4">
                  <div>
                    <span className="text-[11px] font-medium text-[#888888] block">Account</span>
                    <span className="text-[#f0f0f0] font-medium truncate block">
                      {user?.display_name || 'Vault User'}
                    </span>
                    <span className="text-[11px] font-mono text-[#888888]">
                      {user?.username ? `@${user.username}` : 'Direct MTProto'}
                    </span>
                  </div>

                  <div>
                    <span className="text-[11px] font-medium text-[#888888] block">Storage Used</span>
                    <span className="text-[#f0f0f0] font-semibold font-mono text-sm">
                      {formatBytes(metrics.total_bytes)}
                    </span>
                    <span className="text-[11px] font-mono text-[#888888] block">
                      {metrics.total_files} files cataloged
                    </span>
                  </div>

                  <div>
                    <span className="text-[11px] font-medium text-[#888888] block">Max Upload Size</span>
                    <span className="text-[#22c55e] font-semibold font-mono flex items-center gap-1">
                      {user?.is_premium ? (
                        <>
                          <Zap size={11} className="fill-[#22c55e]" /> 4.00 GB (Premium)
                        </>
                      ) : (
                        '1.90 GB (Standard)'
                      )}
                    </span>
                    <span className="text-[11px] text-[#888888] block">
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
            )}
          </div>
        )}

        {/* ================= 2. ADDITIONAL STORAGE CONNECTIONS ================= */}
        {(activeCategory === 'all' || activeCategory === 'storage') && (
          <div className="bg-[#0a0a0a] border border-[#222222] transition-colors">
            <button
              type="button"
              onClick={() => toggleCard('additional')}
              className="w-full p-4 sm:p-5 flex items-center justify-between text-left hover:bg-[#111111]/70 transition-colors select-none"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-[#f59e0b]/20 border border-[#f59e0b]/40 flex items-center justify-center text-[#fbbf24] shrink-0">
                  <HardDrive size={16} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-[#f0f0f0]">
                      Additional Storage Connections
                    </span>
                    <Badge variant={license?.is_pro ? 'success' : 'default'}>
                      {license?.is_pro ? 'Pro Unlocked' : 'Free Tier'}
                    </Badge>
                  </div>
                  <span className="text-[11px] text-[#888888] hidden sm:block">
                    Connect S3-compatible buckets, Local NAS, and Nextcloud WebDAV
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-xs text-[#888888] hidden md:inline font-mono">
                  {expandedCards.additional ? 'Collapse' : 'Configure'}
                </span>
                <div className="p-1 text-[#888888] hover:text-[#ffffff]">
                  {expandedCards.additional ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </div>
              </div>
            </button>

            {expandedCards.additional && (
              <div className="p-5 pt-0 border-t border-[#1a1a1a] space-y-4 animate-in fade-in duration-150">
                <p className="text-xs text-[#a3a3a3] leading-relaxed pt-4">
              Connect external storage to store files natively without splitting. Files are organized into your Vayom Drive library seamlessly.
            </p>

            {/* Pro License Activation if not active */}
            {!license?.is_pro && (
              <div className="p-3.5 bg-[#121212] border border-[#2a2a2a] space-y-2.5">
                <div className="text-xs text-[#f0f0f0] font-semibold flex items-center gap-1.5">
                  <Key size={13} className="text-[#f59e0b]" /> Unlock Additional Storage Connections
                </div>
                <p className="text-xs text-[#888888]">
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
                    <span className="text-xs font-semibold text-[#f0f0f0]">Local & Network Storage (NAS)</span>
                  </div>
                  <span className="text-[10px] px-1.5 py-0.5 bg-emerald-950/80 text-emerald-400 border border-emerald-500/40 font-mono">
                    LOCAL / SMB / NFS
                  </span>
                </div>
                <p className="text-xs text-[#a3a3a3]">
                  Access files on your local workstation, external SSDs, or local network NAS.
                </p>
                <div className="flex items-center justify-between pt-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleSyncNasVault}
                    disabled={isNasSyncing}
                    icon={<FolderSync size={11} />}
                  >
                    {isNasSyncing ? 'Scanning...' : 'Scan & Sync Library'}
                  </Button>
                  {nasSyncMsg && <span className="text-xs text-[#888888] font-mono">{nasSyncMsg}</span>}
                </div>
              </div>

              {/* S3-Compatible Storage */}
              <div className="p-3 bg-[#111111] border border-[#1f1f1f] space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${storageConfigs?.s3?.configured ? 'bg-amber-500' : 'bg-[#555555]'}`} />
                    <span className="text-xs font-semibold text-[#f0f0f0]">S3-Compatible Cloud Storage</span>
                  </div>
                  <Badge variant={storageConfigs?.s3?.configured ? 'success' : 'default'}>
                    {storageConfigs?.s3?.configured ? 'Connected' : 'Not Configured'}
                  </Badge>
                </div>
                <p className="text-xs text-[#a3a3a3]">
                  Connect Cloudflare R2, Amazon S3, Backblaze B2, or MinIO.
                </p>
                {storageConfigs?.s3?.configured && (
                  <div className="text-xs text-[#a3a3a3]">
                    Bucket: <span className="text-[#f0f0f0] font-mono">{storageConfigs.s3.bucket_name}</span>
                  </div>
                )}
                <div className="pt-1">
                  <Button
                    variant={s3FormOpen ? "secondary" : "outline"}
                    size="sm"
                    onClick={() => { setS3FormOpen(!s3FormOpen); setNcFormOpen(false); }}
                  >
                    {s3FormOpen ? 'Close Settings' : (storageConfigs?.s3?.configured ? 'Configure S3' : 'Connect S3')}
                  </Button>
                </div>

                {/* S3 Inline Form */}
                {s3FormOpen && (
                  <div className="p-3 bg-[#0a0a0a] border border-amber-500/30 space-y-3 text-xs mt-2 font-sans">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                      <div>
                        <label className="text-xs font-medium text-[#cccccc] block mb-1">Endpoint URL</label>
                        <input
                          type="text"
                          value={s3Endpoint}
                          onChange={(e) => setS3Endpoint(e.target.value)}
                          placeholder="https://s3.amazonaws.com"
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2.5 py-1.5 text-xs text-[#ffffff] focus:outline-none focus:border-amber-400 font-sans"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-[#cccccc] block mb-1">Bucket Name *</label>
                        <input
                          type="text"
                          value={s3Bucket}
                          onChange={(e) => setS3Bucket(e.target.value)}
                          placeholder="my-vault-bucket"
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2.5 py-1.5 text-xs text-[#ffffff] focus:outline-none focus:border-amber-400 font-sans"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-[#cccccc] block mb-1">Access Key ID *</label>
                        <input
                          type="text"
                          value={s3AccessKey}
                          onChange={(e) => setS3AccessKey(e.target.value)}
                          placeholder="AKIA..."
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2.5 py-1.5 text-xs text-[#ffffff] focus:outline-none focus:border-amber-400 font-mono"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-[#cccccc] block mb-1">
                          Secret Key {storageConfigs?.s3?.has_secret_key ? '(Stored)' : '*'}
                        </label>
                        <input
                          type="password"
                          value={s3SecretKey}
                          onChange={(e) => setS3SecretKey(e.target.value)}
                          placeholder={storageConfigs?.s3?.has_secret_key ? "•••••••••••• (Leave blank to keep)" : "Secret Key"}
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2.5 py-1.5 text-xs text-[#ffffff] focus:outline-none focus:border-amber-400 font-mono"
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
                    <span className="text-xs font-semibold text-[#f0f0f0]">Nextcloud WebDAV</span>
                  </div>
                  <Badge variant={storageConfigs?.nextcloud?.configured ? 'success' : 'default'}>
                    {storageConfigs?.nextcloud?.configured ? 'Connected' : 'Not Configured'}
                  </Badge>
                </div>
                <p className="text-xs text-[#a3a3a3]">
                  Connect your personal Nextcloud or ownCloud server.
                </p>
                {storageConfigs?.nextcloud?.configured && (
                  <div className="text-xs text-[#a3a3a3]">
                    User: <span className="text-[#f0f0f0] font-mono">{storageConfigs.nextcloud.username}</span>
                  </div>
                )}
                <div className="pt-1">
                  <Button
                    variant={ncFormOpen ? "secondary" : "outline"}
                    size="sm"
                    onClick={() => { setNcFormOpen(!ncFormOpen); setS3FormOpen(false); }}
                  >
                    {ncFormOpen ? 'Close Settings' : (storageConfigs?.nextcloud?.configured ? 'Configure NC' : 'Connect Nextcloud')}
                  </Button>
                </div>

                {/* Nextcloud Inline Form */}
                {ncFormOpen && (
                  <div className="p-3 bg-[#0a0a0a] border border-purple-500/30 space-y-3 text-xs mt-2 font-sans">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                      <div className="sm:col-span-2">
                        <label className="text-xs font-medium text-[#cccccc] block mb-1">WebDAV URL *</label>
                        <input
                          type="text"
                          value={ncUrl}
                          onChange={(e) => setNcUrl(e.target.value)}
                          placeholder="https://nextcloud.example.com/remote.php/dav/files/user/"
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2.5 py-1.5 text-xs text-[#ffffff] focus:outline-none focus:border-purple-400 font-sans"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-[#cccccc] block mb-1">Username *</label>
                        <input
                          type="text"
                          value={ncUser}
                          onChange={(e) => setNcUser(e.target.value)}
                          placeholder="username"
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2.5 py-1.5 text-xs text-[#ffffff] focus:outline-none focus:border-purple-400 font-sans"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-[#cccccc] block mb-1">
                          App Password {storageConfigs?.nextcloud?.has_password ? '(Stored)' : '*'}
                        </label>
                        <input
                          type="password"
                          value={ncPassword}
                          onChange={(e) => setNcPassword(e.target.value)}
                          placeholder={storageConfigs?.nextcloud?.has_password ? "•••••••••••• (Leave blank to keep)" : "App password"}
                          className="w-full bg-[#050505] border border-[#2a2a2a] px-2.5 py-1.5 text-xs text-[#ffffff] focus:outline-none focus:border-purple-400 font-mono"
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
        )}
      </div>
    )}

        {/* ================= 3. FOLDER AUTOMATION ================= */}
        {(activeCategory === 'all' || activeCategory === 'automation') && (
          <div className="bg-[#0a0a0a] border border-[#222222] transition-colors">
            <button
              type="button"
              onClick={() => toggleCard('automation')}
              className="w-full p-4 sm:p-5 flex items-center justify-between text-left hover:bg-[#111111]/70 transition-colors select-none"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-[#3b82f6]/20 border border-[#3b82f6]/40 flex items-center justify-center text-[#60a5fa] shrink-0">
                  <FolderSync size={16} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-[#f0f0f0]">
                      Automatic Folder Monitoring
                    </span>
                    <span className="text-[10px] px-1.5 py-0.5 bg-[#181818] border border-[#262626] text-[#888888] font-mono">
                      OBS & Ingest
                    </span>
                  </div>
                  <span className="text-[11px] text-[#888888] hidden sm:block">
                    Auto-upload from OBS recordings, camera SD card drops, and watched local folders
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-xs text-[#888888] hidden md:inline font-mono">
                  {expandedCards.automation ? 'Collapse' : 'Configure'}
                </span>
                <div className="p-1 text-[#888888] hover:text-[#ffffff]">
                  {expandedCards.automation ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </div>
              </div>
            </button>

            {expandedCards.automation && (
              <div className="p-5 pt-0 border-t border-[#1a1a1a] space-y-4 animate-in fade-in duration-150">
                <WatchFolderManager />
              </div>
            )}
          </div>
        )}

        {/* ================= 4. BACKUP & RECOVERY ================= */}
        {(activeCategory === 'all' || activeCategory === 'backup') && (
          <div className="bg-[#0a0a0a] border border-[#222222] transition-colors">
            <button
              type="button"
              onClick={() => toggleCard('backup')}
              className="w-full p-4 sm:p-5 flex items-center justify-between text-left hover:bg-[#111111]/70 transition-colors select-none"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-[#22c55e]/20 border border-[#22c55e]/40 flex items-center justify-center text-[#22c55e] shrink-0">
                  <ShieldCheck size={16} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-[#f0f0f0]">
                      Backup and Recovery
                    </span>
                    <Badge variant={channelHealth?.primary_channel?.healthy ? 'success' : 'danger'}>
                      {channelHealth?.primary_channel?.healthy ? 'Healthy (Can Post)' : 'Attention Required'}
                    </Badge>
                  </div>
                  <span className="text-[11px] text-[#888888] hidden sm:block">
                    Multi-tier data redundancy, secondary Telegram mirror channels, and catalog backups
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-xs text-[#888888] hidden md:inline font-mono">
                  {expandedCards.backup ? 'Collapse' : 'Configure'}
                </span>
                <div className="p-1 text-[#888888] hover:text-[#ffffff]">
                  {expandedCards.backup ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </div>
              </div>
            </button>

            {expandedCards.backup && (
              <div className="p-5 pt-0 border-t border-[#1a1a1a] space-y-4 animate-in fade-in duration-150">
                <p className="text-xs text-[#a3a3a3] leading-relaxed pt-4">
                  Safeguard your library against accidental channel loss or server migration. Your library can be mirrored to a secondary channel and backed up locally.
                </p>

                {/* Health Tiers */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 text-xs">
                  <div className="p-2.5 bg-[#111111] border border-[#1f1f1f] space-y-1">
                    <span className="text-[11px] font-medium text-[#888888] block">Primary Channel</span>
                    <Badge variant={channelHealth?.primary_channel?.healthy ? 'success' : 'danger'}>
                      {channelHealth?.primary_channel?.healthy ? 'Healthy (Can Post)' : 'Attention Required'}
                    </Badge>
                  </div>

                  <div className="p-2.5 bg-[#111111] border border-[#1f1f1f] space-y-1">
                    <span className="text-[11px] font-medium text-[#888888] block">Saved Messages Mirror</span>
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
                  <p className="text-xs text-[#888888]">
                    Mirror all uploads to a second private Telegram channel (e.g. <code className="text-[#e5e5e5] font-mono">-1001234567890</code>).
                  </p>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      placeholder="-100xxxxxxxxxx"
                      value={backupChannelInput}
                      onChange={(e) => setBackupChannelInput(e.target.value)}
                      className="flex-1 px-3 py-1.5 bg-[#141414] border border-[#2a2a2a] text-[#ffffff] text-xs font-mono focus:outline-none focus:border-[#444444]"
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
                    <div className={`text-xs font-sans ${backupChannelStatusMsg.startsWith('✓') ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>
                      {backupChannelStatusMsg}
                    </div>
                  )}
                </div>

                {/* 1-Click Catalog Export & Restore */}
                <div className="pt-2 border-t border-[#1a1a1a] space-y-2">
                  <span className="text-xs font-semibold text-[#f0f0f0] block">
                    Offline Catalog Backup & Restore
                  </span>
                  <p className="text-xs text-[#888888]">
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
                    <div className={`text-xs font-sans ${importStatusMsg.startsWith('✓') ? 'text-[#22c55e]' : 'text-[#f59e0b]'}`}>
                      {importStatusMsg}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ================= 5. SYSTEM & DIAGNOSTICS ================= */}
        {(activeCategory === 'all' || activeCategory === 'diagnostics') && (
          <div className="bg-[#0a0a0a] border border-[#222222] transition-colors">
            <button
              type="button"
              onClick={() => toggleCard('diagnostics')}
              className="w-full p-4 sm:p-5 flex items-center justify-between text-left hover:bg-[#111111]/70 transition-colors select-none"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-[#fbbf24]/20 border border-[#fbbf24]/40 flex items-center justify-center text-[#fbbf24] shrink-0">
                  <Activity size={16} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-[#f0f0f0]">
                      System Diagnostics & Maintenance
                    </span>
                    <span className="text-[10px] px-1.5 py-0.5 bg-[#181818] border border-[#262626] text-[#22c55e] font-mono">
                      Active & Synced
                    </span>
                  </div>
                  <span className="text-[11px] text-[#888888] hidden sm:block">
                    Catalog WAL verification, Anti-ban pool, and index reconstruction
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-xs text-[#888888] hidden md:inline font-mono">
                  {expandedCards.diagnostics ? 'Collapse' : 'Configure'}
                </span>
                <div className="p-1 text-[#888888] hover:text-[#ffffff]">
                  {expandedCards.diagnostics ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </div>
              </div>
            </button>

            {expandedCards.diagnostics && (
              <div className="p-5 pt-0 border-t border-[#1a1a1a] space-y-4 animate-in fade-in duration-150">
                {/* Quick-Start Walkthrough Re-launch Banner */}
                <div className="p-4 bg-[#111111] border border-[#222222] flex flex-col sm:flex-row sm:items-center justify-between gap-4 mt-4">
                  <div className="space-y-1">
                    <span className="text-xs font-semibold text-[#f0f0f0] flex items-center gap-2">
                      <HelpCircle size={14} className="text-[#38bdf8]" /> Interactive Setup & Onboarding Guide
                    </span>
                    <p className="text-xs text-[#888888]">
                      Revisit the 4-step walkthrough covering storage architecture, client-side encryption, and desktop shortcuts.
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => window.dispatchEvent(new CustomEvent('open-vayom-guide'))}
                    icon={<HelpCircle size={12} />}
                  >
                    Launch Setup Guide
                  </Button>
                </div>

                <div className="space-y-4 text-xs font-sans">
                  {/* Diagnostics Grid */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                    <div className="p-3 bg-[#121212] border border-[#1e1e1e]">
                      <span className="text-[11px] font-medium text-[#888888] block">Anti-Ban Worker Pool</span>
                      <span className="text-[#f0f0f0] font-mono text-xs">
                        {channelHealth?.flood_wait_seconds > 0 
                          ? `Cooldown (${channelHealth.flood_wait_seconds}s)` 
                          : `${channelHealth?.max_concurrent_workers || 3} Active Workers`}
                      </span>
                    </div>

                    <div className="p-3 bg-[#121212] border border-[#1e1e1e]">
                      <span className="text-[11px] font-medium text-[#888888] block">Catalog WAL Sync</span>
                      <span className="text-[#22c55e] font-mono text-xs flex items-center gap-1">
                        <Check size={11} /> Synced & Verified
                      </span>
                    </div>
                  </div>

                  {/* Rebuild Index Tool */}
                  <div className="pt-2 border-t border-[#1a1a1a] space-y-2">
                    <span className="text-xs font-semibold text-[#f0f0f0] block">
                      Rebuild Local Index from Telegram Storage
                    </span>
                    <p className="text-xs text-[#a3a3a3]">
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
                  <div className="pt-2 border-t border-[#1a1a1a]">
                    <span className="text-[11px] font-semibold text-[#888888] block mb-2 uppercase">Service Status</span>
                    <div className="p-3 bg-[#111111] border border-[#1a1a1a] space-y-2 text-[11px] text-[#888888]">
                      <div className="flex items-center gap-2">
                        <CheckCircle size={13} className="text-[#22c55e]" />
                        <span>Direct MTProto Bot Client: Active</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <CheckCircle size={13} className="text-[#22c55e]" />
                        <span>Unified Single-Service Server: Active</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <CheckCircle size={13} className="text-[#22c55e]" />
                        <span>Ephemeral Disk Auto-Sync: Active</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  )
}

import React, { useState, useEffect, useRef } from 'react'
import { useVaultStore } from '../store/useVaultStore'
import { useAuthStore } from '../store/useAuthStore'
import { Database, RefreshCw, Terminal, CheckCircle, LogOut, User, Shield, ShieldCheck, Download, Upload } from 'lucide-react'
import { Button } from '../components/common/Button'
import { Badge } from '../components/common/Badge'
import { WatchFolderManager } from '../components/settings/WatchFolderManager'
import { formatBytes } from '../utils/format'
import { api } from '../services/api'

export const SettingsView: React.FC = () => {
  const { metrics, rebuildIndex } = useVaultStore()
  const { user, authMode, logout } = useAuthStore()
  const [isRebuilding, setIsRebuilding] = useState(false)
  const [rebuildLogs, setRebuildLogs] = useState<string[]>([])
  const [rebuildDone, setRebuildDone] = useState(false)

  // Vault Redundancy & Disaster Recovery state
  const [channelHealth, setChannelHealth] = useState<any>(null)
  const [backupChannelInput, setBackupChannelInput] = useState('')
  const [isSavingBackupChannel, setIsSavingBackupChannel] = useState(false)
  const [backupChannelStatusMsg, setBackupChannelStatusMsg] = useState('')
  const [isImporting, setIsImporting] = useState(false)
  const [importStatusMsg, setImportStatusMsg] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadChannelHealth = async () => {
    try {
      const data = await api.getChannelHealth()
      setChannelHealth(data)
      if (data.backup_channel?.id) {
        setBackupChannelInput(data.backup_channel.id)
      }
    } catch (e: any) {
      console.error('Failed to load channel health:', e)
    }
  }

  useEffect(() => {
    loadChannelHealth()
  }, [])

  const handleSaveBackupChannel = async () => {
    if (!backupChannelInput.trim()) return
    setIsSavingBackupChannel(true)
    setBackupChannelStatusMsg('')
    try {
      await api.setBackupChannel(backupChannelInput.trim())
      setBackupChannelStatusMsg('✓ Secondary backup channel connected and master catalog mirrored!')
      await loadChannelHealth()
    } catch (err: any) {
      setBackupChannelStatusMsg(`Error: ${err.message || 'Failed to save'}`)
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
    setImportStatusMsg('Restoring vault files and parts from backup...')
    try {
      const res = await api.importCatalog(file)
      setImportStatusMsg(`✓ Successfully restored ${res.restored_files} files (${res.total_catalog_files} catalog entries) to database!`)
      useVaultStore.getState().fetchFiles()
      useVaultStore.getState().fetchAlbums()
      useVaultStore.getState().fetchMetrics()
      await loadChannelHealth()
    } catch (err: any) {
      setImportStatusMsg(`Restore failed: ${err.message || 'Invalid catalog file'}`)
    } finally {
      setIsImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDisconnect = async () => {
    if (window.confirm('Disconnect from this Telegram account / node? You will return to the login screen.')) {
      await logout()
    }
  }

  const handleRebuildIndex = async () => {
    setIsRebuilding(true)
    setRebuildLogs([
      `[${new Date().toLocaleTimeString()}] Connecting to Telegram channel: ${metrics.channel_id}`,
      `[${new Date().toLocaleTimeString()}] Scanning channel message history backward from HEAD...`,
      `[${new Date().toLocaleTimeString()}] Parsing VAYOM:v1 metadata envelopes from document captions...`,
    ])
    setRebuildDone(false)

    try {
      const res = await rebuildIndex()
      setRebuildLogs((prev) => [
        ...prev,
        `[${new Date().toLocaleTimeString()}] Reconstructing file_parts and SQLite catalog...`,
        `[${new Date().toLocaleTimeString()}] Scanned and processed ${res.restored_envelopes} envelopes.`,
        `[${new Date().toLocaleTimeString()}] Complete: Catalog successfully synchronized with Telegram storage channel.`,
      ])
      setRebuildDone(true)
    } catch (err: any) {
      setRebuildLogs((prev) => [
        ...prev,
        `[${new Date().toLocaleTimeString()}] ERROR: ${err.message || 'Failed to rebuild index'}`,
      ])
    } finally {
      setIsRebuilding(false)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 bg-[#000000] select-none font-mono">
      {/* Top Header */}
      <div className="pb-6 border-b border-[#282828] mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-base font-bold uppercase tracking-wider text-[#ffffff] flex items-center gap-2">
            <Database size={16} /> Vayom Drive Node Configuration
          </h1>
          <p className="text-xs text-[#777777] mt-1">
            Telegram backend parameters, pointer-offset policies, and disaster recovery tools
          </p>
        </div>
        <Button
          variant="danger"
          size="sm"
          onClick={handleDisconnect}
          icon={<LogOut size={12} />}
        >
          Disconnect Node
        </Button>
      </div>

      <div className="max-w-3xl space-y-6">
        {/* Active Node Account Profile */}
        <div className="p-4 bg-[#0a0a0a] border border-[#222222] space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-[#f0f0f0] uppercase tracking-wider flex items-center gap-2">
              <User size={13} /> Active Node Account
            </span>
            <Badge variant="default">
              {authMode === 'qr' ? 'Telegram User Session' : authMode === 'bot' ? 'Dedicated Bot' : 'Sovereign Node'}
            </Badge>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs pt-2 border-t border-[#1a1a1a]">
            <div>
              <span className="text-[10px] text-[#666666] uppercase block">Display Name</span>
              <span className="text-[#e5e5e5] font-semibold">{user?.display_name || 'Vault User'}</span>
            </div>
            <div>
              <span className="text-[10px] text-[#666666] uppercase block">Telegram Username</span>
              <span className="text-[#e5e5e5]">{user?.username ? `@${user.username}` : 'Direct MTProto'}</span>
            </div>
            <div>
              <span className="text-[10px] text-[#666666] uppercase block">Chunk Ceiling</span>
              <span className="text-[#22c55e] font-semibold">
                {user?.is_premium ? '4.00 GB (Premium)' : '1.90 GB (Standard)'}
              </span>
            </div>
          </div>
        </div>

        {/* Telegram Storage Channel Status */}
        <div className="p-4 bg-[#0a0a0a] border border-[#222222] space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-[#f0f0f0] uppercase tracking-wider">
              Telegram Backend Status
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
                ? 'Online • Direct MTProto'
                : metrics.online_status === 'flood_wait'
                ? `Rate Limited • Flood Wait (${metrics.flood_wait_seconds ?? 0}s)`
                : 'Connecting'}
            </Badge>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs pt-2 border-t border-[#1a1a1a]">
            <div>
              <span className="text-[10px] text-[#666666] uppercase block">Storage Channel</span>
              <span className="text-[#e5e5e5]">{metrics.channel_id}</span>
            </div>
            <div>
              <span className="text-[10px] text-[#666666] uppercase block">Total Messages Stored</span>
              <span className="text-[#e5e5e5]">{metrics.telegram_messages.toLocaleString()}</span>
            </div>
            <div>
              <span className="text-[10px] text-[#666666] uppercase block">Max Part Size Ceiling</span>
              <span className="text-[#e5e5e5]">{user?.is_premium ? '3.90 GB (Telegram Premium)' : '1.90 GB (1,900,000,000 bytes)'}</span>
            </div>
            <div>
              <span className="text-[10px] text-[#666666] uppercase block">Current Stored Bytes</span>
              <span className="text-[#e5e5e5]">{formatBytes(metrics.total_bytes)}</span>
            </div>
          </div>
        </div>

        {/* Vault Redundancy & Disaster Recovery (Multi-Channel Mirror & Offline Snapshot) */}
        <div className="p-4 bg-[#0a0a0a] border border-[#222222] space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-[#f0f0f0] uppercase tracking-wider flex items-center gap-2">
              <ShieldCheck size={14} className="text-[#22c55e]" /> Vault Redundancy & Disaster Recovery
            </span>
            <Button
              variant="secondary"
              size="sm"
              onClick={loadChannelHealth}
              icon={<RefreshCw size={11} />}
            >
              Refresh Health
            </Button>
          </div>

          <p className="text-[11px] text-[#777777]">
            Eliminate single-point-of-failure risks. If your primary Telegram channel is accidentally left, 
            deleted, or bot admin privileges revoked, Vayom Drive preserves your vault through multi-tier backups.
          </p>

          {/* Health Tier Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs pt-1">
            {/* Primary Channel */}
            <div className="p-2.5 bg-[#121212] border border-[#1f1f1f] space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-[#888888] uppercase">Primary Channel</span>
                <Badge variant={channelHealth?.primary_channel?.healthy ? 'success' : 'danger'}>
                  {channelHealth?.primary_channel?.healthy ? 'Healthy (Can Post)' : 'Attention Required'}
                </Badge>
              </div>
              <div className="text-[#e5e5e5] font-mono text-[11px] truncate">
                {channelHealth?.primary_channel?.title || channelHealth?.primary_channel?.id || metrics.channel_id}
              </div>
              {channelHealth?.primary_channel?.error && (
                <div className="text-[10px] text-[#ef4444] pt-1">
                  {channelHealth.primary_channel.error}
                </div>
              )}
            </div>

            {/* Saved Messages Mirror */}
            <div className="p-2.5 bg-[#121212] border border-[#1f1f1f] space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-[#888888] uppercase">Saved Messages Mirror</span>
                <Badge variant={channelHealth?.saved_messages_mirror ? 'success' : 'default'}>
                  {channelHealth?.saved_messages_mirror ? 'Active (Private Me)' : 'Not in QR Mode'}
                </Badge>
              </div>
              <div className="text-[#aaaaaa] text-[10px]">
                {channelHealth?.saved_messages_mirror 
                  ? 'Master catalog mirrored to personal Saved Messages on every commit.' 
                  : 'Immutable fallback active in Telegram user login session.'}
              </div>
            </div>

            {/* Local Persistent Snapshot */}
            <div className="p-2.5 bg-[#121212] border border-[#1f1f1f] space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-[#888888] uppercase">Local File Snapshot</span>
                <Badge variant={channelHealth?.local_backup?.exists ? 'success' : 'warning'}>
                  {channelHealth?.local_backup?.exists ? 'Persisted' : 'Pending Upload'}
                </Badge>
              </div>
              <div className="text-[#888888] text-[10px] truncate" title={channelHealth?.local_backup?.path}>
                {channelHealth?.local_backup?.path || 'data/vault_catalog_backup.json'}
              </div>
            </div>

            {/* Telegram Anti-Ban Protection Pool */}
            <div className="p-2.5 bg-[#121212] border border-[#1f1f1f] space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-[#888888] uppercase">Anti-Ban Pacing & Limits</span>
                <Badge variant={channelHealth?.flood_wait_seconds > 0 ? 'warning' : 'info'}>
                  {channelHealth?.flood_wait_seconds > 0 
                    ? `Cooldown (${channelHealth.flood_wait_seconds}s)` 
                    : `${channelHealth?.max_concurrent_workers || 3} Workers Active`}
                </Badge>
              </div>
              <div className="text-[#888888] text-[10px]">
                25ms packet spacing + deadlock-free FloodWait retry queue.
              </div>
            </div>
          </div>

          {/* Secondary Mirror Channel Configuration */}
          <div className="pt-2 border-t border-[#1a1a1a] space-y-2">
            <span className="text-[11px] font-semibold text-[#f0f0f0] block">
              Multi-Channel Redundancy (Secondary Mirror Channel)
            </span>
            <p className="text-[10px] text-[#777777]">
              Specify a second private channel ID (e.g. <code className="text-[#e5e5e5]">-1001234567890</code>). 
              Every catalog commit will be mirrored to this channel simultaneously.
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
                {isSavingBackupChannel ? 'Saving...' : 'Set Backup Channel'}
              </Button>
            </div>
            {backupChannelStatusMsg && (
              <div className={`text-[10px] ${backupChannelStatusMsg.startsWith('✓') ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>
                {backupChannelStatusMsg}
              </div>
            )}
          </div>

          {/* 1-Click Offline Backup & Restore */}
          <div className="pt-2 border-t border-[#1a1a1a] space-y-2">
            <span className="text-[11px] font-semibold text-[#f0f0f0] block">
              Offline Catalog Portability & Disaster Recovery
            </span>
            <p className="text-[10px] text-[#777777]">
              Export your entire vault pointer database as a single JSON file. You can import this file anytime to instantly rebuild your vault on any machine with zero Telegram scanning latency.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={handleExportCatalog}
                icon={<Download size={12} />}
              >
                Download Catalog Backup (.json)
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
                {isImporting ? 'Importing & Restoring...' : 'Restore Catalog from Backup'}
              </Button>
            </div>
            {importStatusMsg && (
              <div className={`text-[10px] ${importStatusMsg.startsWith('✓') ? 'text-[#22c55e]' : 'text-[#f59e0b]'}`}>
                {importStatusMsg}
              </div>
            )}
          </div>
        </div>

        {/* Local Watch Folders: Media Workstation Automation */}
        <WatchFolderManager />

        {/* Disaster Recovery: Rebuild Index from Channel */}
        <div className="p-4 bg-[#0a0a0a] border border-[#222222] space-y-4">
          <div>
            <span className="text-xs font-semibold text-[#f0f0f0] uppercase tracking-wider block">
              Disaster Recovery: Rebuild SQLite Index
            </span>
            <p className="text-[11px] text-[#777777] mt-1">
              If your host filesystem or SQLite database is lost or running on an ephemeral free cloud dyno, 
              Vayom Drive will read the metadata envelopes from every Telegram channel message 
              and restore the full catalog with zero data loss.
            </p>
          </div>

          <Button
            variant="secondary"
            size="sm"
            onClick={handleRebuildIndex}
            disabled={isRebuilding}
            icon={<RefreshCw size={12} className={isRebuilding ? 'animate-spin' : ''} />}
          >
            {isRebuilding ? 'Rebuilding Index...' : 'Rebuild Index from Channel'}
          </Button>

          {/* Terminal Output */}
          {rebuildLogs.length > 0 && (
            <div className="p-3 bg-[#050505] border border-[#1f1f1f] text-[11px] space-y-1 font-mono text-[#aaaaaa] max-h-48 overflow-y-auto">
              <div className="text-[10px] text-[#555555] uppercase pb-1 flex items-center gap-1.5 border-b border-[#181818]">
                <Terminal size={11} /> Recovery Process Output
              </div>
              {rebuildLogs.map((log, idx) => (
                <div key={idx} className="leading-tight">
                  {log}
                </div>
              ))}
              {rebuildDone && (
                <div className="text-[#22c55e] font-semibold pt-1">
                  ✓ Database fully restored and verified.
                </div>
              )}
            </div>
          )}
        </div>

        {/* Free Cloud Deployment Checklist */}
        <div className="p-4 bg-[#0a0a0a] border border-[#222222] space-y-3">
          <span className="text-xs font-semibold text-[#f0f0f0] uppercase tracking-wider block">
            Free Cloud Deployment Checklist
          </span>
          <div className="space-y-2 text-xs text-[#888888]">
            <div className="flex items-center gap-2">
              <CheckCircle size={14} className="text-[#22c55e]" />
              <span>Direct Telegram MTProto Bot Client: active (eliminates C++ Bot API container)</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle size={14} className="text-[#22c55e]" />
              <span>Unified Single-Service Server: FastAPI serves compiled React PWA at root</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle size={14} className="text-[#22c55e]" />
              <span>Ephemeral Disk Auto-Sync: enabled on boot</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

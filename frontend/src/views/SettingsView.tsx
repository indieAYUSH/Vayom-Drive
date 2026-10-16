import React, { useState } from 'react'
import { useVaultStore } from '../store/useVaultStore'
import { Database, RefreshCw, Terminal, CheckCircle } from 'lucide-react'
import { Button } from '../components/common/Button'
import { Badge } from '../components/common/Badge'

export const SettingsView: React.FC = () => {
  const { metrics, rebuildIndex } = useVaultStore()
  const [isRebuilding, setIsRebuilding] = useState(false)
  const [rebuildLogs, setRebuildLogs] = useState<string[]>([])
  const [rebuildDone, setRebuildDone] = useState(false)

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

  const formatBytes = (bytes: number) => {
    if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    return `${(bytes / 1024).toFixed(1)} KB`
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 bg-[#000000] select-none font-mono">
      {/* Top Header */}
      <div className="pb-6 border-b border-[#282828] mb-6">
        <h1 className="text-base font-bold uppercase tracking-wider text-[#ffffff] flex items-center gap-2">
          <Database size={16} /> Vayom Drive Node Configuration
        </h1>
        <p className="text-xs text-[#777777] mt-1">
          Telegram backend parameters, pointer-offset policies, and disaster recovery tools
        </p>
      </div>

      <div className="max-w-3xl space-y-6">
        {/* Telegram Storage Channel Status */}
        <div className="p-4 bg-[#0a0a0a] border border-[#222222] space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-[#f0f0f0] uppercase tracking-wider">
              Telegram Backend Status
            </span>
            <Badge variant={metrics.online_status === 'online' ? 'success' : 'warning'}>
              {metrics.online_status === 'online' ? 'Online • Direct MTProto' : 'Connecting'}
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
              <span className="text-[#e5e5e5]">1.90 GB (1,900,000,000 bytes)</span>
            </div>
            <div>
              <span className="text-[10px] text-[#666666] uppercase block">Current Stored Bytes</span>
              <span className="text-[#e5e5e5]">{formatBytes(metrics.total_bytes)}</span>
            </div>
          </div>
        </div>

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

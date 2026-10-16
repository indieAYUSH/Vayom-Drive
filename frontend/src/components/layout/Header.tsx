import React from 'react'
import { Search, Upload, HardDrive, Settings, ShieldCheck, Share2 } from 'lucide-react'
import { useVaultStore } from '../../store/useVaultStore'
import { useUploadStore } from '../../store/useUploadStore'
import { Button } from '../common/Button'

export const Header: React.FC = () => {
  const { metrics, setCommandBarOpen, setActiveTab, activeTab } = useVaultStore()
  const { addFilesToUpload } = useUploadStore()
  const fileInputRef = React.useRef<HTMLInputElement>(null)

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      addFilesToUpload(e.target.files)
      e.target.value = ''
    }
  }

  const formatStorage = (bytes: number) => {
    const gb = (bytes / (1024 * 1024 * 1024)).toFixed(1)
    return `${gb} GB`
  }

  return (
    <header className="h-14 bg-[#080808] border-b border-[#282828] px-4 flex items-center justify-between select-none z-30 flex-shrink-0">
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileChange}
        multiple
        className="hidden"
      />

      {/* Brand Mark */}
      <div className="flex items-center gap-4">
        <div 
          onClick={() => setActiveTab('all')}
          className="flex items-center gap-2.5 cursor-pointer group"
        >
          <div className="w-5 h-5 bg-[#ffffff] flex items-center justify-center">
            <div className="w-2.5 h-2.5 bg-[#000000]" />
          </div>
          <span className="font-mono text-sm font-bold tracking-widest text-[#ffffff] uppercase group-hover:text-[#e5e5e5]">
            VAYOM DRIVE
          </span>
        </div>

        {/* Telegram Bot Connection Indicator */}
        <div className="hidden sm:flex items-center gap-1.5 px-2 py-0.5 border border-[#1f1f1f] bg-[#0d0d0d] text-[10px] font-mono text-[#888888]">
          <span className="w-1.5 h-1.5 bg-[#22c55e]" />
          <span>TG-CHANNEL: {metrics.channel_id.substring(0, 8)}...</span>
        </div>
      </div>

      {/* Search / Command Trigger */}
      <div className="flex-1 max-w-lg mx-4">
        <div
          onClick={() => setCommandBarOpen(true)}
          className="h-8 bg-[#111111] border border-[#282828] hover:border-[#444444] px-3 flex items-center justify-between text-xs text-[#666666] cursor-pointer transition-colors"
        >
          <div className="flex items-center gap-2 truncate">
            <Search size={13} className="text-[#666666] flex-shrink-0" />
            <span className="font-mono text-[11px] truncate">Search semantic CLIP, camera, or filename...</span>
          </div>
          <kbd className="hidden sm:inline-block font-mono text-[10px] bg-[#1c1c1c] text-[#888888] px-1.5 py-0.5 border border-[#282828]">
            ⌘K
          </kbd>
        </div>
      </div>

      {/* Action Buttons & Storage Badge */}
      <div className="flex items-center gap-2.5">
        {/* Storage Metric */}
        <div className="hidden md:flex items-center gap-2 px-2.5 py-1 bg-[#111111] border border-[#222222] font-mono text-xs">
          <HardDrive size={13} className="text-[#888888]" />
          <span className="text-[#cccccc]">{formatStorage(metrics.total_bytes)}</span>
          <span className="text-[#555555]">/ UNLIMITED</span>
        </div>

        {/* Upload Button */}
        <Button
          variant="primary"
          size="sm"
          icon={<Upload size={13} />}
          onClick={() => fileInputRef.current?.click()}
        >
          Upload
        </Button>

        {/* Share Links Portal */}
        <Button
          variant={activeTab === 'shares' ? 'secondary' : 'outline'}
          size="sm"
          onClick={() => setActiveTab('shares')}
          className="hidden sm:inline-flex"
          icon={<Share2 size={13} />}
        >
          Shares
        </Button>

        {/* Settings Button */}
        <Button
          variant={activeTab === 'settings' ? 'secondary' : 'outline'}
          size="sm"
          onClick={() => setActiveTab('settings')}
          icon={<Settings size={13} />}
        >
          <span className="hidden sm:inline">Settings</span>
        </Button>
      </div>
    </header>
  )
}

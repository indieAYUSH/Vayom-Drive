import React, { useRef } from 'react'
import { Search, Upload, HardDrive, Settings, FolderUp, Image } from 'lucide-react'
import { useVaultStore } from '../../store/useVaultStore'
import { useUploadStore } from '../../store/useUploadStore'
import { useAuthStore } from '../../store/useAuthStore'
import { Button } from '../common/Button'

export const Header: React.FC = () => {
  const { metrics, setCommandBarOpen, setActiveTab, activeTab, primaryMode, setPrimaryMode } = useVaultStore()
  const { addFilesToUpload } = useUploadStore()
  const { user } = useAuthStore()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

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
    <header className="h-14 bg-[#080808] border-b border-[#282828] px-3 sm:px-4 flex items-center justify-between select-none z-30 flex-shrink-0">
      {/* File & Folder Inputs */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileChange}
        multiple
        className="hidden"
      />
      <input
        type="file"
        ref={folderInputRef}
        onChange={handleFileChange}
        // @ts-ignore
        webkitdirectory=""
        directory=""
        multiple
        className="hidden"
      />

      {/* Brand Mark & Primary Mode Switcher */}
      <div className="flex items-center gap-2 sm:gap-4 flex-shrink-0">
        <div 
          onClick={() => setActiveTab('all')}
          className="flex items-center gap-2 cursor-pointer group"
        >
          <div className="w-5 h-5 bg-[#ffffff] flex items-center justify-center flex-shrink-0">
            <div className="w-2.5 h-2.5 bg-[#000000]" />
          </div>
          <span className="font-mono text-xs sm:text-sm font-bold tracking-widest text-[#ffffff] uppercase group-hover:text-[#e5e5e5] hidden min-[450px]:inline">
            VAYOM
          </span>
        </div>

        {/* Dual Mode Switcher (Google Photos vs Google Drive) */}
        <div className="hidden md:flex border border-[#282828] bg-[#111111] font-mono text-[11px]">
          <button
            onClick={() => {
              setPrimaryMode('photos')
              setActiveTab('all')
            }}
            className={`px-2.5 py-1 transition-colors flex items-center gap-1.5 ${
              primaryMode === 'photos'
                ? 'bg-[#ffffff] text-[#000000] font-semibold'
                : 'text-[#888888] hover:text-[#f0f0f0]'
            }`}
            title="Google Photos Mode: Media Timeline & Reels"
          >
            <Image size={12} />
            <span>Photos</span>
          </button>
          <button
            onClick={() => {
              setPrimaryMode('drive')
              setActiveTab('folders')
            }}
            className={`px-2.5 py-1 transition-colors flex items-center gap-1.5 ${
              primaryMode === 'drive'
                ? 'bg-[#ffffff] text-[#000000] font-semibold'
                : 'text-[#888888] hover:text-[#f0f0f0]'
            }`}
            title="Google Drive Mode: Folders & Document Trees"
          >
            <HardDrive size={12} />
            <span>Drive</span>
          </button>
        </div>
      </div>

      {/* Search / Command Trigger */}
      <div className="flex-1 max-w-md mx-2 sm:mx-4 min-w-0">
        <div
          onClick={() => setCommandBarOpen(true)}
          className="h-8 bg-[#111111] border border-[#282828] hover:border-[#444444] px-2.5 sm:px-3 flex items-center justify-between text-xs text-[#666666] cursor-pointer transition-colors"
        >
          <div className="flex items-center gap-2 truncate min-w-0">
            <Search size={13} className="text-[#666666] flex-shrink-0" />
            <span className="font-mono text-[11px] truncate">Search CLIP, camera, filename...</span>
          </div>
          <kbd className="hidden sm:inline-block font-mono text-[10px] bg-[#1c1c1c] text-[#888888] px-1.5 py-0.5 border border-[#282828] flex-shrink-0">
            ⌘K
          </kbd>
        </div>
      </div>

      {/* Action Buttons & Storage Badge */}
      <div className="flex items-center gap-1.5 sm:gap-2 flex-shrink-0">
        {/* Storage Metric */}
        <div className="hidden xl:flex items-center gap-2 px-2.5 py-1 bg-[#111111] border border-[#222222] font-mono text-xs">
          <HardDrive size={13} className="text-[#888888]" />
          <span className="text-[#cccccc]">{formatStorage(metrics.total_bytes)}</span>
          <span className="text-[#555555]">/ UNLIMITED</span>
        </div>

        {/* Upload File Button */}
        <Button
          variant="primary"
          size="sm"
          icon={<Upload size={13} />}
          onClick={() => fileInputRef.current?.click()}
        >
          <span>Upload</span>
        </Button>

        {/* Upload Folder Button */}
        <Button
          variant="outline"
          size="sm"
          icon={<FolderUp size={13} />}
          onClick={() => folderInputRef.current?.click()}
          className="hidden sm:inline-flex"
        >
          <span>Folder</span>
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

        {/* User Account Chip */}
        {user && (
          <div 
            onClick={() => setActiveTab('settings')}
            title="Active Telegram Node"
            className="hidden lg:flex items-center gap-1.5 px-2 py-1 bg-[#141414] border border-[#282828] text-[11px] font-mono text-[#e5e5e5] cursor-pointer hover:border-[#444444]"
          >
            <span className="w-1.5 h-1.5 bg-[#22c55e]" />
            <span className="truncate max-w-[90px]">{user.display_name || user.first_name || 'Node'}</span>
          </div>
        )}
      </div>
    </header>
  )
}

import React from 'react'
import { ChevronRight, Folder, Home, Plus } from 'lucide-react'
import { useVaultStore } from '../../store/useVaultStore'

interface BreadcrumbNavProps {
  currentFolderName?: string | null
  onNewFolder?: () => void
}

export const BreadcrumbNav: React.FC<BreadcrumbNavProps> = ({
  currentFolderName,
  onNewFolder,
}) => {
  const { setActiveTab, setSelectedAlbumId } = useVaultStore()

  const handleNavigateRoot = () => {
    setSelectedAlbumId(null)
    setActiveTab('folders')
  }

  return (
    <div className="h-10 bg-[#080808] border-b border-[#202020] px-4 flex items-center justify-between text-xs select-none">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 truncate">
        <button
          onClick={handleNavigateRoot}
          className="flex items-center gap-1.5 text-[#888888] hover:text-[#ffffff] transition-colors py-1 px-1.5 hover:bg-[#141414] font-medium"
          title="All Folders & Directories"
        >
          <Home size={13} />
          <span>Drive</span>
        </button>

        <ChevronRight size={12} className="text-[#555555] flex-shrink-0" />

        <button
          onClick={handleNavigateRoot}
          className={`text-[#888888] hover:text-[#ffffff] transition-colors py-1 px-1.5 hover:bg-[#141414] font-medium ${
            !currentFolderName ? 'text-[#f0f0f0] font-semibold' : ''
          }`}
        >
          Folders
        </button>

        {currentFolderName && (
          <>
            <ChevronRight size={12} className="text-[#555555] flex-shrink-0" />
            <div className="flex items-center gap-1.5 py-1 px-1.5 bg-[#141414] border border-[#282828] text-[#ffffff] font-medium truncate max-w-xs">
              <Folder size={12} className="text-[#888888]" />
              <span className="truncate">{currentFolderName}</span>
            </div>
          </>
        )}
      </nav>

      {onNewFolder && (
        <button
          onClick={onNewFolder}
          className="flex items-center gap-1 text-xs font-medium px-2 py-1 bg-[#111111] hover:bg-[#1a1a1a] border border-[#282828] hover:border-[#444444] text-[#e0e0e0] hover:text-[#ffffff] transition-colors"
        >
          <Plus size={12} />
          <span>New Folder</span>
        </button>
      )}
    </div>
  )
}

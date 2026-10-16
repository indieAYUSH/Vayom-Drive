import React, { useState } from 'react'
import { useVaultStore } from '../store/useVaultStore'
import { VirtualizedGrid } from '../components/gallery/VirtualizedGrid'
import { TableView } from '../components/gallery/TableView'
import { LayoutGrid, List, X, ArrowLeft } from 'lucide-react'
import { Badge } from '../components/common/Badge'

export const GalleryView: React.FC = () => {
  const { 
    files, 
    activeTab, 
    selectedAlbumId, 
    setSelectedAlbumId,
    selectedTag, 
    setSelectedTag,
    selectedIds, 
    toggleSelect, 
    selectAll, 
    clearSelection,
    openLightbox,
    moveToTrash,
    albums
  } = useVaultStore()

  const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid')

  const activeAlbum = albums.find((a) => a.id === selectedAlbumId)

  // Filter items
  const displayItems = React.useMemo(() => {
    return files.filter((item) => {
      if (item.is_deleted) return false
      if (selectedAlbumId && !item.albums.includes(selectedAlbumId)) return false
      if (selectedTag && !item.tags.includes(selectedTag)) return false
      if (activeTab === 'images' && item.file_category !== 'image') return false
      if (activeTab === 'videos' && item.file_category !== 'video') return false
      if (activeTab === 'documents' && item.file_category !== 'document') return false
      if (activeTab === 'archives' && item.file_category !== 'archive' && !item.is_split) return false
      return true
    })
  }, [files, activeTab, selectedAlbumId, selectedTag])

  return (
    <div className="flex-1 flex flex-col h-full bg-[#000000] overflow-hidden">
      {/* Gallery Header Bar */}
      <div className="h-12 border-b border-[#282828] bg-[#080808] px-4 flex items-center justify-between flex-shrink-0 font-mono text-xs select-none">
        {/* Left: Section Title or Filter Indicator */}
        <div className="flex items-center gap-2 truncate">
          {selectedAlbumId && (
            <button
              onClick={() => setSelectedAlbumId(null)}
              className="p-1 hover:text-[#ffffff] text-[#888888] flex items-center gap-1 border border-transparent hover:border-[#333333] mr-1"
            >
              <ArrowLeft size={13} />
              <span className="text-[11px]">All</span>
            </button>
          )}

          <span className="font-semibold text-[#f0f0f0] uppercase tracking-wider">
            {activeAlbum 
              ? activeAlbum.name 
              : activeTab === 'all' 
                ? 'All Media Timeline' 
                : `${activeTab} Index`}
          </span>

          <span className="text-[11px] text-[#555555]">
            ({displayItems.length} {displayItems.length === 1 ? 'file' : 'files'})
          </span>

          {selectedTag && (
            <div className="flex items-center gap-1 ml-2">
              <Badge variant="white">#{selectedTag}</Badge>
              <button 
                onClick={() => setSelectedTag(null)}
                className="text-[#666666] hover:text-[#ffffff]"
              >
                <X size={12} />
              </button>
            </div>
          )}
        </div>

        {/* Right: Layout Switcher & Selection */}
        <div className="flex items-center gap-2">
          {selectedIds.length > 0 ? (
            <button
              onClick={clearSelection}
              className="text-[11px] text-[#999999] hover:text-[#ffffff] px-2 py-1 border border-[#282828]"
            >
              Clear ({selectedIds.length})
            </button>
          ) : (
            <button
              onClick={selectAll}
              className="text-[11px] text-[#999999] hover:text-[#ffffff] px-2 py-1 border border-[#282828] hover:border-[#444444] hidden sm:inline-block"
            >
              Select All
            </button>
          )}

          <div className="flex border border-[#282828] bg-[#111111]">
            <button
              onClick={() => setViewMode('grid')}
              className={`p-1.5 transition-colors ${
                viewMode === 'grid' 
                  ? 'bg-[#ffffff] text-[#000000]' 
                  : 'text-[#888888] hover:text-[#f0f0f0]'
              }`}
              title="Grid View"
            >
              <LayoutGrid size={13} />
            </button>
            <button
              onClick={() => setViewMode('table')}
              className={`p-1.5 transition-colors ${
                viewMode === 'table' 
                  ? 'bg-[#ffffff] text-[#000000]' 
                  : 'text-[#888888] hover:text-[#f0f0f0]'
              }`}
              title="Dense Table View"
            >
              <List size={13} />
            </button>
          </div>
        </div>
      </div>

      {/* Main Virtualized Grid or Table */}
      {viewMode === 'grid' ? (
        <VirtualizedGrid
          items={displayItems}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onOpenLightbox={openLightbox}
        />
      ) : (
        <TableView
          items={displayItems}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onOpenLightbox={openLightbox}
          onMoveToTrash={moveToTrash}
        />
      )}
    </div>
  )
}

import React, { useState } from 'react'
import { useVaultStore } from '../store/useVaultStore'
import { VirtualizedGrid } from '../components/gallery/VirtualizedGrid'
import { TableView } from '../components/gallery/TableView'
import { LayoutGrid, List, X, ArrowLeft, Calendar } from 'lucide-react'
import { Badge } from '../components/common/Badge'
import { BreadcrumbNav } from '../components/drive/BreadcrumbNav'

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
    albums,
    starredIds,
    timelineGroupByDate,
    setTimelineGroupByDate,
    driveViewMode,
    setDriveViewMode,
  } = useVaultStore()

  const activeAlbum = albums.find((a) => a.id === selectedAlbumId)

  const isScreenshot = (item: any): boolean => {
    if (item.file_category === 'screenshot' || item.is_screenshot) return true
    const name = (item.filename || '').toLowerCase()
    return (
      name.includes('screenshot') ||
      name.includes('screen_shot') ||
      name.includes('screen shot') ||
      name.includes('screencap') ||
      name.includes('capture') ||
      name.includes('scrn') ||
      (item.tags && item.tags.some((t: string) => t.toLowerCase() === 'screenshot' || t.toLowerCase() === 'screen'))
    )
  }

  const isAudio = (item: any): boolean => {
    return (
      item.file_category === 'audio' ||
      (item.mime_type && item.mime_type.startsWith('audio/')) ||
      /\.(mp3|wav|flac|ogg|m4a|aac)$/i.test(item.filename || '')
    )
  }

  // Filter items
  const displayItems = React.useMemo(() => {
    return files.filter((item) => {
      if (item.is_deleted) return false
      if (selectedAlbumId) {
        const inAlbum = item.albums?.includes(selectedAlbumId) || (item.folders && item.folders.includes(selectedAlbumId))
        if (!inAlbum) return false
      }
      if (selectedTag && !item.tags.includes(selectedTag)) return false
      if (activeTab === 'starred') return starredIds.includes(item.id)
      if (activeTab === 'audio') return isAudio(item)
      if (activeTab === 'screenshots') {
        return item.file_category === 'image' && isScreenshot(item)
      }
      if (activeTab === 'images') {
        return item.file_category === 'image' && !isScreenshot(item)
      }
      if (activeTab === 'videos') return item.file_category === 'video'
      if (activeTab === 'documents') return item.file_category === 'document'
      if (activeTab === 'archives') return item.file_category === 'archive' || item.is_split
      return true
    })
  }, [files, activeTab, selectedAlbumId, selectedTag, starredIds])

  return (
    <div className="flex-1 flex flex-col h-full bg-[#000000] overflow-hidden">
      {/* Drive Breadcrumb Navigation when viewing folder */}
      {selectedAlbumId && (
        <BreadcrumbNav currentFolderName={activeAlbum?.name} />
      )}

      {/* Gallery Header Bar */}
      <div className="h-12 border-b border-[#282828] bg-[#080808] px-4 flex items-center justify-between flex-shrink-0 font-mono text-xs select-none">
        {/* Left: Section Title or Filter Indicator */}
        <div className="flex items-center gap-2 truncate">
          {selectedAlbumId && (
            <button
              onClick={() => setSelectedAlbumId(null)}
              className="p-1 hover:text-[#ffffff] text-[#888888] flex items-center gap-1 border border-transparent hover:border-[#333333] mr-1"
              title="Back to Folders"
            >
              <ArrowLeft size={13} />
              <span className="text-[11px]">Folders</span>
            </button>
          )}

          <span className="font-semibold text-[#f0f0f0] uppercase tracking-wider">
            {activeAlbum 
              ? `Folder: ${activeAlbum.name}` 
              : activeTab === 'all' 
                ? 'All Media Timeline' 
                : activeTab === 'screenshots'
                  ? 'Screenshots'
                  : activeTab === 'starred'
                    ? 'Starred & Favorites'
                    : activeTab === 'audio'
                      ? 'Audio & Voice Tracks'
                      : activeTab === 'folders'
                        ? 'Folder Contents'
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
              onClick={() => selectAll(displayItems.map((item) => item.id))}
              className="text-[11px] text-[#999999] hover:text-[#ffffff] px-2 py-1 border border-[#282828] hover:border-[#444444] hidden sm:inline-block"
            >
              Select All
            </button>
          )}

          <div className="flex border border-[#282828] bg-[#111111]">
            <button
              onClick={() => setTimelineGroupByDate(!timelineGroupByDate)}
              className={`p-1.5 transition-colors border-r border-[#282828] ${
                timelineGroupByDate
                  ? 'bg-[#ffffff] text-[#000000]'
                  : 'text-[#888888] hover:text-[#f0f0f0]'
              }`}
              title={timelineGroupByDate ? 'Date Grouping: Active' : 'Date Grouping: Disabled'}
            >
              <Calendar size={13} />
            </button>
            <button
              onClick={() => setDriveViewMode('grid')}
              className={`p-1.5 transition-colors ${
                driveViewMode === 'grid' 
                  ? 'bg-[#ffffff] text-[#000000]' 
                  : 'text-[#888888] hover:text-[#f0f0f0]'
              }`}
              title="Grid View"
            >
              <LayoutGrid size={13} />
            </button>
            <button
              onClick={() => setDriveViewMode('table')}
              className={`p-1.5 transition-colors ${
                driveViewMode === 'table' 
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
      {driveViewMode === 'grid' ? (
        <VirtualizedGrid
          items={displayItems}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onOpenLightbox={openLightbox}
          groupByDate={timelineGroupByDate}
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

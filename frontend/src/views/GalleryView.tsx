import React, { useState } from 'react'
import type { FileItem } from '../types/vault'
import { useVaultStore } from '../store/useVaultStore'
import { VirtualizedGrid } from '../components/gallery/VirtualizedGrid'
import { TableView } from '../components/gallery/TableView'
import { LayoutGrid, List, X, ArrowLeft, Calendar, Download } from 'lucide-react'
import { Badge } from '../components/common/Badge'
import { BreadcrumbNav } from '../components/drive/BreadcrumbNav'
import { WorkstationFilterBar } from '../components/gallery/WorkstationFilterBar'
import { apiUrl } from '../utils/apiUrl'

export const GalleryView: React.FC = () => {
  const { 
    files, 
    activeTab, 
    selectedAlbumId, 
    setSelectedAlbumId,
    selectedTag, 
    setSelectedTag,
    selectedCamera,
    selectedCodec,
    selectedResolution,
    selectedLens,
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

  // H-13: Filter items ensuring category filters do not bypass workstation metadata filters
  const displayItems = React.useMemo(() => {
    return files.filter((item) => {
      if (item.is_deleted) return false
      if (selectedAlbumId) {
        const inAlbum = item.albums?.includes(selectedAlbumId) || (item.folders && item.folders.includes(selectedAlbumId))
        if (!inAlbum) return false
      }
      if (selectedTag && !item.tags.includes(selectedTag)) return false
      if (activeTab === 'starred' && !starredIds.includes(item.id)) return false
      if (activeTab === 'audio' && !isAudio(item)) return false
      if (activeTab === 'screenshots' && !(item.file_category === 'image' && isScreenshot(item))) return false
      if (activeTab === 'images' && !(item.file_category === 'image' && !isScreenshot(item))) return false
      if (activeTab === 'videos' && item.file_category !== 'video') return false
      if (activeTab === 'documents' && item.file_category !== 'document') return false
      if (activeTab === 'archives' && !(item.file_category === 'archive' || item.is_split)) return false

      // Workstation Metadata Filters (applies consistently across images, videos, all, etc.)
      if (selectedCamera) {
        const cam = item.exif?.camera_make || item.exif?.make || item.exif?.camera_model || item.exif?.model
        if (!cam || !cam.toLowerCase().includes(selectedCamera.toLowerCase())) return false
      }
      if (selectedCodec) {
        const codec = item.exif?.codec?.toUpperCase()
        if (codec !== selectedCodec.toUpperCase()) return false
      }
      if (selectedResolution) {
        const res = item.exif?.resolution_tier
        if (res !== selectedResolution) return false
      }
      if (selectedLens) {
        const lens = item.exif?.lens
        if (!lens || !lens.toLowerCase().includes(selectedLens.toLowerCase())) return false
      }

      return true
    })
  }, [files, activeTab, selectedAlbumId, selectedTag, starredIds, selectedCamera, selectedCodec, selectedResolution, selectedLens])

  // H-14: Scoped lightbox opening using the visible, sorted list
  const handleOpenLightbox = React.useCallback(
    (item: FileItem, contextList?: FileItem[]) => {
      openLightbox(item, contextList || displayItems)
    },
    [openLightbox, displayItems]
  )

  return (
    <div className="flex-1 flex flex-col h-full bg-[#000000] overflow-hidden">
      {/* Drive Breadcrumb Navigation when viewing folder */}
      {selectedAlbumId && (
        <BreadcrumbNav currentFolderName={activeAlbum?.name} />
      )}

      {/* Gallery Header Bar */}
      <div className="h-12 border-b border-[#282828] bg-[#080808] px-4 flex items-center justify-between flex-shrink-0 font-sans text-xs select-none">
        {/* Left: Section Title or Filter Indicator */}
        <div className="flex items-center gap-2 truncate">
          {selectedAlbumId && (
            <button
              onClick={() => setSelectedAlbumId(null)}
              className="p-1 hover:text-[#ffffff] text-[#888888] flex items-center gap-1 border border-transparent hover:border-[#333333] mr-1"
              title="Back to Folders"
            >
              <ArrowLeft size={13} />
              <span className="text-xs">Folders</span>
            </button>
          )}

          <span className="font-semibold text-[#f0f0f0] tracking-normal">
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

          <span className="text-[11px] font-mono text-[#888888]">
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
          {selectedAlbumId && (
            <a
              href={apiUrl(`/api/v1/albums/${selectedAlbumId}/download-zip`)}
              download={`${activeAlbum?.name || 'folder'}.zip`}
              className="text-xs font-sans text-[#cccccc] hover:text-[#ffffff] px-2.5 py-1 border border-[#282828] hover:border-[#444444] bg-[#111111] flex items-center gap-1.5 transition-colors"
              title="Download folder contents as a .ZIP archive"
            >
              <Download size={12} />
              <span className="hidden sm:inline">Download .ZIP</span>
            </a>
          )}

          {selectedIds.length > 0 ? (
            <button
              onClick={clearSelection}
              className="text-xs font-sans text-[#a3a3a3] hover:text-[#ffffff] px-2.5 py-1 border border-[#282828]"
            >
              Clear ({selectedIds.length})
            </button>
          ) : (
            <button
              onClick={() => selectAll(displayItems.map((item) => item.id))}
              className="text-xs font-sans text-[#a3a3a3] hover:text-[#ffffff] px-2.5 py-1 border border-[#282828] hover:border-[#444444] hidden sm:inline-block"
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

      {/* Workstation Media Filters Toolbar */}
      <WorkstationFilterBar />

      {/* Main Virtualized Grid or Table */}
      {driveViewMode === 'grid' ? (
        <VirtualizedGrid
          items={displayItems}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onOpenLightbox={handleOpenLightbox}
          groupByDate={timelineGroupByDate}
        />
      ) : (
        <TableView
          items={displayItems}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onOpenLightbox={handleOpenLightbox}
          onMoveToTrash={(ids) => {
            // H-17: surface failures instead of an unhandled rejection
            moveToTrash(ids).catch((err: any) => alert(err?.message || 'Failed to move items to trash'))
          }}
        />
      )}
    </div>
  )
}

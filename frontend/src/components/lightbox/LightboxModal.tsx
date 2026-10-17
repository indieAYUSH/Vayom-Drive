import React, { useState, useEffect, useRef } from 'react'
import { FileItem } from '../../types/vault'
import { VideoPlayer } from './VideoPlayer'
import { MetadataDrawer } from './MetadataDrawer'
import { DocPreview } from '../drive/DocPreview'
import { useVaultStore } from '../../store/useVaultStore'
import { 
  X, 
  ChevronLeft, 
  ChevronRight, 
  Download, 
  Trash2, 
  Info, 
  ZoomIn, 
  ZoomOut,
  Maximize2,
  Star
} from 'lucide-react'
import { Button } from '../common/Button'

interface LightboxModalProps {
  item: FileItem | null
  onClose: () => void
  onNext: () => void
  onPrev: () => void
  onOpenShare?: (file: FileItem) => void
  onMoveToTrash: (id: string) => void
}

export const LightboxModal: React.FC<LightboxModalProps> = ({
  item,
  onClose,
  onNext,
  onPrev,
  onOpenShare,
  onMoveToTrash
}) => {
  const [showMetadata, setShowMetadata] = useState(false)
  const [zoomLevel, setZoomLevel] = useState(1)
  const { starredIds, toggleStar } = useVaultStore()

  // Touch tracking for mobile swipe gestures
  const touchStartX = useRef<number>(0)
  const touchStartY = useRef<number>(0)
  const lastTapTime = useRef<number>(0)

  // Fullscreen toggle helper
  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {})
    } else {
      document.exitFullscreen().catch(() => {})
    }
  }

  const handleDownload = () => {
    if (!item) return
    const url = item.preview_url || item.thumbnail_url || `/api/v1/files/${item.id}/download`
    if (url) {
      const a = document.createElement('a')
      a.href = url
      a.download = item.filename
      a.click()
    }
  }

  // Keyboard navigation & shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!item) return
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight') onNext()
      else if (e.key === 'ArrowLeft') onPrev()
      else if (e.key === 'i' || e.key === 'I') setShowMetadata((prev) => !prev)
      else if (e.key === 'f' || e.key === 'F') toggleFullscreen()
      else if (e.key === 'd' || e.key === 'D') handleDownload()
      else if (e.key === 'Delete' || e.key === 'Backspace') {
        onMoveToTrash(item.id)
        onClose()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [item, onClose, onNext, onPrev, onMoveToTrash])

  // Reset zoom on item change
  useEffect(() => {
    setZoomLevel(1)
  }, [item?.id])

  if (!item) return null

  const isStarred = starredIds.includes(item.id)

  // Touch handlers for mobile swipe navigation
  const handleTouchStart = (e: React.TouchEvent) => {
    if (e.touches.length === 1) {
      touchStartX.current = e.touches[0].clientX
      touchStartY.current = e.touches[0].clientY
    }
  }

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (e.changedTouches.length === 1) {
      const deltaX = e.changedTouches[0].clientX - touchStartX.current
      const deltaY = e.changedTouches[0].clientY - touchStartY.current

      // Horizontal swipe threshold: > 60px horizontal, with limited vertical tilt
      if (Math.abs(deltaX) > 60 && Math.abs(deltaX) > Math.abs(deltaY) * 1.5) {
        if (deltaX < 0) onNext()
        else onPrev()
      }

      // Vertical swipe down threshold to dismiss: > 120px down
      if (deltaY > 120 && Math.abs(deltaY) > Math.abs(deltaX) * 2) {
        onClose()
      }
    }
  }

  // Mobile double-tap to zoom
  const handleDoubleTap = () => {
    const now = Date.now()
    if (now - lastTapTime.current < 300) {
      setZoomLevel((prev) => (prev === 1 ? 2.5 : 1))
    }
    lastTapTime.current = now
  }

  return (
    <div 
      className="fixed inset-0 z-50 flex flex-col bg-[#000000] text-[#f0f0f0] select-none"
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      {/* Top Header Bar */}
      <div className="h-12 bg-[#080808] border-b border-[#222222] px-2 sm:px-4 flex items-center justify-between z-20 flex-shrink-0">
        <div className="flex items-center gap-2 sm:gap-3 truncate max-w-[150px] sm:max-w-md">
          <button
            onClick={() => toggleStar(item.id)}
            className={`p-1 border border-[#282828] bg-[#111111] transition-colors ${
              isStarred ? 'text-[#ffffff]' : 'text-[#666666] hover:text-[#ffffff]'
            }`}
            title={isStarred ? 'Unstar' : 'Star file'}
          >
            <Star size={13} fill={isStarred ? 'white' : 'none'} />
          </button>
          <span className="font-mono text-xs font-semibold text-[#f0f0f0] truncate" title={item.filename}>
            {item.filename}
          </span>
          <span className="text-[10px] font-mono text-[#666666] hidden sm:inline">
            {(item.size_bytes / (1024 * 1024)).toFixed(1)} MB
          </span>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-1 sm:gap-1.5">
          {item.file_category === 'image' && (
            <div className="hidden sm:flex items-center gap-1 mr-2 border-r border-[#222222] pr-2">
              <button
                onClick={() => setZoomLevel((z) => Math.max(0.5, z - 0.25))}
                className="p-1.5 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
                title="Zoom Out"
              >
                <ZoomOut size={13} />
              </button>
              <button
                onClick={() => setZoomLevel(1)}
                className="px-1 text-[10px] font-mono text-[#888888] hover:text-[#ffffff]"
              >
                {Math.round(zoomLevel * 100)}%
              </button>
              <button
                onClick={() => setZoomLevel((z) => Math.min(3, z + 0.25))}
                className="p-1.5 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
                title="Zoom In"
              >
                <ZoomIn size={13} />
              </button>
            </div>
          )}

          <Button
            variant="outline"
            size="sm"
            onClick={toggleFullscreen}
            icon={<Maximize2 size={13} />}
            className="hidden md:inline-flex"
          >
            <span className="hidden lg:inline">Fullscreen</span>
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={handleDownload}
            icon={<Download size={13} />}
          >
            <span className="hidden sm:inline">Download</span>
          </Button>

          <Button
            variant={showMetadata ? 'secondary' : 'outline'}
            size="sm"
            onClick={() => setShowMetadata(!showMetadata)}
            icon={<Info size={13} />}
          >
            <span className="hidden sm:inline">Info</span>
          </Button>

          <Button
            variant="danger"
            size="sm"
            onClick={() => {
              onMoveToTrash(item.id)
              onClose()
            }}
            icon={<Trash2 size={13} />}
          >
            <span className="hidden sm:inline">Trash</span>
          </Button>

          <button
            onClick={onClose}
            className="p-1.5 ml-1 sm:ml-2 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
            title="Close (Esc)"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      {/* Main View Area with Metadata Drawer Side by Side */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left Arrow Button */}
        <button
          onClick={onPrev}
          className="absolute left-2 sm:left-4 top-1/2 -translate-y-1/2 z-20 w-8 h-8 sm:w-10 sm:h-10 bg-[#080808] border border-[#282828] hover:border-[#ffffff] flex items-center justify-center text-[#cccccc] hover:text-[#ffffff] transition-colors"
          title="Previous (Left Arrow or Swipe Right)"
        >
          <ChevronLeft size={18} />
        </button>

        {/* Center Media Viewport */}
        <div 
          onClick={handleDoubleTap}
          className="flex-1 flex items-center justify-center p-2 sm:p-4 overflow-hidden relative"
        >
          {item.file_category === 'video' ? (
            <VideoPlayer
              src={item.preview_url || `/api/v1/files/${item.id}/download`}
              poster={item.thumbnail_url}
              filename={item.filename}
            />
          ) : item.file_category === 'image' ? (
            <div className="w-full h-full flex items-center justify-center overflow-auto">
              <img
                src={item.preview_url || item.thumbnail_url || `/api/v1/files/${item.id}/download`}
                alt={item.filename}
                draggable={false}
                onDragStart={(e) => e.preventDefault()}
                style={{ transform: `scale(${zoomLevel})` }}
                className="max-h-[85vh] max-w-[85vw] object-contain transition-transform duration-150 border border-[#1a1a1a] select-none"
              />
            </div>
          ) : (
            <DocPreview item={item} onDownload={handleDownload} />
          )}
        </div>

        {/* Right Arrow Button */}
        <button
          onClick={onNext}
          className="absolute right-2 sm:right-4 top-1/2 -translate-y-1/2 z-20 w-8 h-8 sm:w-10 sm:h-10 bg-[#080808] border border-[#282828] hover:border-[#ffffff] flex items-center justify-center text-[#cccccc] hover:text-[#ffffff] transition-colors"
          title="Next (Right Arrow or Swipe Left)"
        >
          <ChevronRight size={18} />
        </button>

        {/* Collapsible Metadata Drawer */}
        {showMetadata && (
          <MetadataDrawer
            item={item}
            onClose={() => setShowMetadata(false)}
          />
        )}
      </div>
    </div>
  )
}

import React, { useState, useEffect } from 'react'
import { FileItem } from '../../types/vault'
import { VideoPlayer } from './VideoPlayer'
import { MetadataDrawer } from './MetadataDrawer'
import { 
  X, 
  ChevronLeft, 
  ChevronRight, 
  Download, 
  Share2, 
  Trash2, 
  Info, 
  ZoomIn, 
  ZoomOut,
  Maximize2
} from 'lucide-react'
import { Button } from '../common/Button'

interface LightboxModalProps {
  item: FileItem | null
  onClose: () => void
  onNext: () => void
  onPrev: () => void
  onOpenShare: (file: FileItem) => void
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

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!item) return
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowRight') onNext()
      if (e.key === 'ArrowLeft') onPrev()
      if (e.key === 'i' || e.key === 'I') setShowMetadata((prev) => !prev)
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [item, onClose, onNext, onPrev])

  if (!item) return null

  const handleDownload = () => {
    // Direct stream or sample download
    const url = item.preview_url || item.thumbnail_url
    if (url) {
      const a = document.createElement('a')
      a.href = url
      a.download = item.filename
      a.click()
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[#000000] text-[#f0f0f0] select-none">
      {/* Top Header Bar */}
      <div className="h-12 bg-[#080808] border-b border-[#222222] px-2 sm:px-4 flex items-center justify-between z-20 flex-shrink-0">
        <div className="flex items-center gap-2 sm:gap-3 truncate max-w-[160px] sm:max-w-md">
          <span className="font-mono text-xs font-semibold text-[#f0f0f0] truncate">
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
            onClick={handleDownload}
            icon={<Download size={13} />}
          >
            <span className="hidden sm:inline">Download</span>
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenShare(item)}
            icon={<Share2 size={13} />}
          >
            <span className="hidden sm:inline">Share</span>
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
          title="Previous (Left Arrow)"
        >
          <ChevronLeft size={18} />
        </button>

        {/* Center Media Viewport */}
        <div className="flex-1 flex items-center justify-center p-2 sm:p-4 overflow-hidden relative">
          {item.file_category === 'video' ? (
            <VideoPlayer
              src={item.preview_url || 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4'}
              poster={item.thumbnail_url}
              filename={item.filename}
            />
          ) : item.file_category === 'image' ? (
            <div className="w-full h-full flex items-center justify-center overflow-auto">
              <img
                src={item.preview_url || item.thumbnail_url}
                alt={item.filename}
                draggable={false}
                onDragStart={(e) => e.preventDefault()}
                style={{ transform: `scale(${zoomLevel})` }}
                className="max-h-[85vh] max-w-[85vw] object-contain transition-transform duration-150 border border-[#1a1a1a] select-none"
              />
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center p-8 border border-[#222222] bg-[#0c0c0c] text-center font-mono max-w-md">
              <div className="w-16 h-16 border border-[#333333] flex items-center justify-center text-xl mb-4 text-[#888888]">
                {item.file_category === 'archive' ? 'ZIP' : 'DOC'}
              </div>
              <div className="text-sm font-semibold text-[#f0f0f0] break-all mb-1">
                {item.filename}
              </div>
              <div className="text-xs text-[#777777] mb-4">
                {(item.size_bytes / (1024 * 1024)).toFixed(2)} MB • {item.mime_type}
              </div>
              <Button variant="primary" onClick={handleDownload} icon={<Download size={14} />}>
                Download Document
              </Button>
            </div>
          )}
        </div>

        {/* Right Arrow Button */}
        <button
          onClick={onNext}
          className="absolute right-2 sm:right-4 top-1/2 -translate-y-1/2 z-20 w-8 h-8 sm:w-10 sm:h-10 bg-[#080808] border border-[#282828] hover:border-[#ffffff] flex items-center justify-center text-[#cccccc] hover:text-[#ffffff] transition-colors"
          title="Next (Right Arrow)"
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

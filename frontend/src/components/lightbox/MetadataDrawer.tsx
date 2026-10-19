import React from 'react'
import { FileItem } from '../../types/vault'
import { Copy, Check, MapPin, Camera, HardDrive, ShieldCheck, Tag, X } from 'lucide-react'
import { Badge } from '../common/Badge'
import { formatBytes } from '../../utils/format'

interface MetadataDrawerProps {
  item: FileItem
  onClose?: () => void
}

export const MetadataDrawer: React.FC<MetadataDrawerProps> = ({ item, onClose }) => {
  const [copiedSha, setCopiedSha] = React.useState(false)
  const copiedTimeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)

  React.useEffect(() => {
    return () => {
      if (copiedTimeoutRef.current) {
        clearTimeout(copiedTimeoutRef.current)
        copiedTimeoutRef.current = null
      }
    }
  }, [])

  const copySha = () => {
    try {
      navigator.clipboard.writeText(item.sha256).catch((err: unknown) => {
        console.warn('Clipboard write failed:', err)
      })
    } catch (err) {
      console.warn('Clipboard unavailable:', err)
    }
    setCopiedSha(true)
    if (copiedTimeoutRef.current) clearTimeout(copiedTimeoutRef.current)
    copiedTimeoutRef.current = setTimeout(() => setCopiedSha(false), 1500)
  }

  const formatSize = (bytes: number) => {
    return `${formatBytes(bytes)} (${(bytes || 0).toLocaleString()} bytes)`
  }

  return (
    <aside className="fixed inset-y-0 right-0 z-30 w-full sm:static sm:w-80 sm:w-96 bg-[#0c0c0c] border-l border-[#282828] h-full overflow-y-auto p-4 flex flex-col gap-5 text-xs select-none flex-shrink-0 shadow-none">
      {/* Drawer Header Bar */}
      <div className="flex items-center justify-between pb-3 border-b border-[#222222]">
        <div className="text-xs font-semibold tracking-normal text-[#ffffff] flex items-center gap-2">
          <HardDrive size={14} />
          <span>File Inspector</span>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="p-1 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333] transition-colors"
            title="Close Info (Esc)"
          >
            <X size={15} />
          </button>
        )}
      </div>

      {/* File Overview */}
      <div className="space-y-2">
        <div className="text-xs font-semibold text-[#888888] flex items-center gap-1.5">
          <HardDrive size={12} /> File Specification
        </div>
        <div className="bg-[#121212] border border-[#222222] p-3 space-y-2">
          <div>
            <div className="text-[11px] font-medium text-[#888888]">Name</div>
            <div className="text-xs font-medium text-[#f0f0f0] break-all mt-0.5">{item.filename}</div>
          </div>
          <div className="flex justify-between text-xs pt-1.5 border-t border-[#1a1a1a]">
            <span className="text-[#888888]">Size:</span>
            <span className="text-[#cccccc] font-mono text-[11px]">{formatSize(item.size_bytes)}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-[#888888]">MIME:</span>
            <span className="text-[#cccccc] font-mono text-[11px] uppercase">{item.mime_type}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-[#888888]">Created:</span>
            <span className="text-[#cccccc] font-mono text-[11px]">{new Date(item.created_at).toLocaleString()}</span>
          </div>
        </div>
      </div>

      {/* Cryptographic SHA-256 Integrity */}
      <div className="space-y-2">
        <div className="text-xs font-semibold text-[#888888] flex items-center gap-1.5">
          <ShieldCheck size={12} /> SHA-256 Checksum
        </div>
        <div className="bg-[#121212] border border-[#222222] p-2.5">
          <div className="text-[11px] text-[#aaaaaa] break-all select-all font-mono">
            {item.sha256}
          </div>
          <button
            onClick={copySha}
            className="mt-2 w-full py-1 border border-[#282828] hover:border-[#555555] bg-[#161616] flex items-center justify-center gap-1.5 text-xs font-medium text-[#cccccc] transition-colors"
          >
            {copiedSha ? (
              <>
                <Check size={12} className="text-[#22c55e]" /> Copied Checksum
              </>
            ) : (
              <>
                <Copy size={12} /> Copy SHA-256
              </>
            )}
          </button>
        </div>
      </div>

      {/* Telegram Extent & Pointer Parts Manifest */}
      <div className="space-y-2">
        <div className="text-xs font-semibold text-[#888888] flex items-center justify-between">
          <span>Telegram Extents ({item.total_parts} Parts)</span>
          {item.is_split && <Badge variant="warning" className="font-mono text-[10px]">Split Array</Badge>}
        </div>
        <div className="border border-[#222222] divide-y divide-[#1c1c1c] bg-[#111111]">
          {item.parts.map((part) => (
            <div key={part.part_index} className="p-2.5 text-xs space-y-1">
              <div className="flex justify-between font-medium text-[#e5e5e5]">
                <span>Part {part.part_index + 1} / {item.total_parts}</span>
                <span className="text-[#3b82f6] font-mono text-[11px]">Msg ID: {part.telegram_message_id}</span>
              </div>
              <div className="flex justify-between text-[11px] text-[#888888] font-mono">
                <span>Offset: {part.offset.toLocaleString()}</span>
                <span>Slice: {formatBytes(part.size)}</span>
              </div>
              <div className="text-[10px] text-[#666666] truncate font-mono">
                Hash: {part.sha256}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* EXIF Camera Metadata */}
      {item.exif && (
        <div className="space-y-2">
          <div className="text-xs font-semibold text-[#888888] flex items-center gap-1.5">
            <Camera size={12} /> EXIF & Optical Data
          </div>
          <div className="bg-[#121212] border border-[#222222] p-3 text-xs space-y-1.5">
            {item.exif.camera_make && (
              <div className="flex justify-between">
                <span className="text-[#888888]">Camera:</span>
                <span className="text-[#e5e5e5] font-medium">{item.exif.camera_make} {item.exif.camera_model}</span>
              </div>
            )}
            {item.exif.lens && (
              <div className="flex justify-between">
                <span className="text-[#888888]">Lens:</span>
                <span className="text-[#cccccc]">{item.exif.lens}</span>
              </div>
            )}
            {item.exif.dimensions && (
              <div className="flex justify-between">
                <span className="text-[#888888]">Resolution:</span>
                <span className="text-[#cccccc] font-mono text-[11px]">{item.exif.dimensions.width} × {item.exif.dimensions.height} px</span>
              </div>
            )}
            <div className="grid grid-cols-3 gap-1 pt-1.5 border-t border-[#1c1c1c] text-center text-xs">
              <div className="p-1.5 bg-[#181818] border border-[#222222]">
                <div className="text-[11px] text-[#888888]">Focal</div>
                <div className="text-[#cccccc] font-mono text-xs mt-0.5">{item.exif.focal_length || '—'}</div>
              </div>
              <div className="p-1.5 bg-[#181818] border border-[#222222]">
                <div className="text-[11px] text-[#888888]">Aperture</div>
                <div className="text-[#cccccc] font-mono text-xs mt-0.5">{item.exif.aperture || '—'}</div>
              </div>
              <div className="p-1.5 bg-[#181818] border border-[#222222]">
                <div className="text-[11px] text-[#888888]">ISO</div>
                <div className="text-[#cccccc] font-mono text-xs mt-0.5">{item.exif.iso || '—'}</div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* GPS Geo Data */}
      {item.gps && (
        <div className="space-y-2">
          <div className="text-xs font-semibold text-[#888888] flex items-center gap-1.5">
            <MapPin size={12} /> Geo Coordinates
          </div>
          <div className="bg-[#121212] border border-[#222222] p-3 space-y-1.5 text-xs">
            <div className="flex justify-between">
              <span className="text-[#888888]">Latitude:</span>
              <span className="text-[#e5e5e5] font-mono text-[11px]">{item.gps.latitude.toFixed(5)}°</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#888888]">Longitude:</span>
              <span className="text-[#e5e5e5] font-mono text-[11px]">{item.gps.longitude.toFixed(5)}°</span>
            </div>
            <a
              href={`https://www.openstreetmap.org/?mlat=${item.gps.latitude}&mlon=${item.gps.longitude}#map=14/${item.gps.latitude}/${item.gps.longitude}`}
              target="_blank"
              rel="noreferrer"
              className="mt-2 block w-full py-1 text-center bg-[#181818] border border-[#282828] text-xs text-[#38bdf8] hover:border-[#38bdf8] transition-colors font-medium"
            >
              Open in OpenStreetMap ↗
            </a>
          </div>
        </div>
      )}

      {/* Tags */}
      {item.tags.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-semibold text-[#888888] flex items-center gap-1.5">
            <Tag size={12} /> Tags & CLIP Features
          </div>
          <div className="flex flex-wrap gap-1">
            {item.tags.map((t) => (
              <Badge key={t} variant="default">#{t}</Badge>
            ))}
          </div>
        </div>
      )}
    </aside>
  )
}

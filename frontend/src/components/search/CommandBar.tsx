import React, { useState, useEffect, useRef } from 'react'
import { Search, Image, Video, Archive, FileText, ArrowRight, X, Loader2 } from 'lucide-react'
import { useVaultStore } from '../../store/useVaultStore'
import { FileItem } from '../../types/vault'
import { Badge } from '../common/Badge'
import { formatBytes } from '../../utils/format'
import { api } from '../../services/api'

export const CommandBar: React.FC = () => {
  const { 
    isCommandBarOpen, 
    setCommandBarOpen, 
    files, 
    catalogFiles,
    openLightbox,
    setActiveTab,
    setSelectedTag
  } = useVaultStore()

  const [query, setQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [serverResults, setServerResults] = useState<FileItem[]>([])
  const [isSearchingServer, setIsSearchingServer] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // H-12: Debounced server search across entire vault catalog
  useEffect(() => {
    const trimmed = query.trim()
    if (!trimmed) {
      setServerResults([])
      setIsSearchingServer(false)
      return
    }

    const controller = new AbortController()
    setIsSearchingServer(true)
    const timer = setTimeout(() => {
      api.listFiles({ q: trimmed, limit: 30 }, { signal: controller.signal })
        .then((res) => {
          setServerResults(res)
          setIsSearchingServer(false)
        })
        .catch((err) => {
          if (err.name !== 'AbortError') {
            setIsSearchingServer(false)
          }
        })
    }, 180)

    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [query])

  // Global Keyboard shortcut listener for Cmd+K / Ctrl+K
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setCommandBarOpen(!isCommandBarOpen)
      }
      if (e.key === 'Escape' && isCommandBarOpen) {
        setCommandBarOpen(false)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isCommandBarOpen, setCommandBarOpen])

  useEffect(() => {
    if (isCommandBarOpen) {
      setTimeout(() => inputRef.current?.focus(), 50)
      setSelectedIndex(0)
    } else {
      setQuery('')
      setServerResults([])
      setIsSearchingServer(false)
    }
  }, [isCommandBarOpen])

  // H-12: Filter across catalogFiles and combine with server search results
  const filteredItems = React.useMemo(() => {
    const pool = catalogFiles.length > 0 ? catalogFiles : files
    const trimmed = query.trim()
    if (!trimmed) return pool.filter((f) => !f.is_deleted).slice(0, 8)
    const q = trimmed.toLowerCase()

    const localMatches = pool.filter((f) => {
      if (f.is_deleted) return false
      if (f.filename.toLowerCase().includes(q)) return true
      if (f.tags.some((t) => t.toLowerCase().includes(q))) return true
      if (f.exif?.camera_make?.toLowerCase().includes(q)) return true
      if (f.exif?.camera_model?.toLowerCase().includes(q)) return true
      if (f.file_category.toLowerCase() === q) return true
      if (q === 'large' || q === '>1gb') return f.size_bytes > 1000000000
      return false
    })

    const localIds = new Set(localMatches.map((m) => m.id))
    const additional = serverResults.filter((s) => !s.is_deleted && !localIds.has(s.id))

    return [...localMatches, ...additional].slice(0, 15)
  }, [catalogFiles, files, query, serverResults])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIndex((prev) => (prev + 1) % Math.max(1, filteredItems.length))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIndex((prev) => (prev - 1 + filteredItems.length) % Math.max(1, filteredItems.length))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const selected = filteredItems[selectedIndex]
      if (selected) {
        openLightbox(selected)
        setCommandBarOpen(false)
      }
    }
  }

  if (!isCommandBarOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-6 sm:pt-20 px-2 sm:px-4">
      {/* Opaque backdrop with zero blur */}
      <div 
        className="fixed inset-0 bg-[#000000] bg-opacity-80"
        onClick={() => setCommandBarOpen(false)}
      />

      {/* Sharp modal box */}
      <div className="relative w-full max-w-2xl bg-[#111111] border border-[#333333] select-none text-xs z-10 flex flex-col">
        {/* Search Input Bar */}
        <div className="flex items-center px-4 py-3 border-b border-[#282828] bg-[#0c0c0c] gap-3">
          <Search size={16} className="text-[#888888]" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setSelectedIndex(0)
            }}
            onKeyDown={handleKeyDown}
            placeholder="Search by filename, camera, lens, tags (e.g. 'drone', '4k', 'Sony')..."
            className="w-full bg-transparent text-[#f0f0f0] placeholder:text-[#666666] focus:outline-none text-xs"
          />
          {isSearchingServer && <Loader2 size={13} className="text-[#888888] animate-spin flex-shrink-0" />}
          <button
            onClick={() => setCommandBarOpen(false)}
            className="text-[#666666] hover:text-[#f0f0f0] p-1"
          >
            <X size={14} />
          </button>
        </div>

        {/* Quick Filter Tag Suggestions */}
        <div className="px-4 py-2 bg-[#080808] border-b border-[#1f1f1f] flex items-center gap-2 flex-wrap text-xs text-[#888888]">
          <span className="font-medium text-[#777777]">Quick:</span>
          {['dog', 'aurora', 'drone', '4k', 'backup', 'large'].map((filter) => (
            <button
              key={filter}
              onClick={() => {
                setQuery(filter)
                inputRef.current?.focus()
              }}
              className="px-2 py-0.5 bg-[#141414] border border-[#282828] hover:border-[#ffffff] hover:text-[#ffffff] transition-colors font-medium text-xs text-[#cccccc]"
            >
              #{filter}
            </button>
          ))}
        </div>

        {/* Search Result Rows */}
        <div className="max-h-80 overflow-y-auto divide-y divide-[#1a1a1a]">
          {filteredItems.length === 0 ? (
            <div className="p-8 text-center text-[#666666] text-xs">
              No files matching "{query}"
            </div>
          ) : (
            filteredItems.map((item, idx) => (
              <div
                key={item.id}
                onClick={() => {
                  openLightbox(item)
                  setCommandBarOpen(false)
                }}
                onMouseEnter={() => setSelectedIndex(idx)}
                className={`p-3 flex items-center justify-between cursor-pointer transition-colors ${
                  selectedIndex === idx ? 'bg-[#1a1a1a] text-[#ffffff]' : 'text-[#cccccc] hover:bg-[#141414]'
                }`}
              >
                <div className="flex items-center gap-2 sm:gap-3 truncate min-w-0 flex-1 mr-2">
                  {item.thumbnail_url ? (
                    <img
                      src={item.thumbnail_url}
                      alt={item.filename}
                      className="w-8 h-8 object-cover border border-[#282828] flex-shrink-0"
                    />
                  ) : item.file_category === 'video' ? (
                    <div className="w-8 h-8 bg-[#181818] border border-[#282828] flex items-center justify-center flex-shrink-0">
                      <Video size={14} className="text-[#888888]" />
                    </div>
                  ) : (
                    <div className="w-8 h-8 bg-[#181818] border border-[#282828] flex items-center justify-center flex-shrink-0">
                      <FileText size={14} className="text-[#888888]" />
                    </div>
                  )}

                  <div className="truncate min-w-0 flex-1">
                    <div className="font-medium truncate text-xs text-[#f0f0f0]">{item.filename}</div>
                    <div className="text-xs text-[#888888] flex items-center gap-2 mt-0.5">
                      <span className="font-mono text-[11px] text-[#888888]">{formatBytes(item.size_bytes)}</span>
                      <span>•</span>
                      <span className="capitalize">{item.file_category}</span>
                      {item.tags.length > 0 && (
                        <>
                          <span>•</span>
                          <span className="truncate text-[#777777]">#{item.tags.slice(0, 2).join(' #')}</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-1.5 sm:gap-2 flex-shrink-0">
                  {item.is_split && <Badge variant="warning" className="font-mono text-[10px]">{item.total_parts} Parts</Badge>}
                  <ArrowRight size={14} className="text-[#666666]" />
                </div>
              </div>
            ))
          )}
        </div>

        {/* Footer shortcuts */}
        <div className="px-4 py-2 bg-[#0a0a0a] border-t border-[#222222] flex items-center justify-between text-xs text-[#888888]">
          <div className="flex items-center gap-2">
            <span>Navigate:</span>
            <span className="font-mono text-[11px] px-1 bg-[#161616] border border-[#282828] text-[#cccccc]">↑</span>
            <span className="font-mono text-[11px] px-1 bg-[#161616] border border-[#282828] text-[#cccccc]">↓</span>
            <span>• Select:</span>
            <span className="font-mono text-[11px] px-1 bg-[#161616] border border-[#282828] text-[#cccccc]">Enter</span>
          </div>
          <div>ESC to close</div>
        </div>
      </div>
    </div>
  )
}

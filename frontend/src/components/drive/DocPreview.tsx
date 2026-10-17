import React, { useState, useEffect } from 'react'
import { FileItem } from '../../types/vault'
import { Button } from '../common/Button'
import { Download, Copy, Check, FileText, Music, Play, Pause, Volume2, VolumeX } from 'lucide-react'
import { formatBytes } from '../../utils/format'

interface DocPreviewProps {
  item: FileItem
  onDownload: () => void
}

export const DocPreview: React.FC<DocPreviewProps> = ({ item, onDownload }) => {
  const [textContent, setTextContent] = useState<string | null>(null)
  const [isLoadingText, setIsLoadingText] = useState(false)
  const [copied, setCopied] = useState(false)

  // Audio player state if audio
  const [isPlayingAudio, setIsPlayingAudio] = useState(false)
  const [audioDuration, setAudioDuration] = useState(0)
  const [audioTime, setAudioTime] = useState(0)
  const audioRef = React.useRef<HTMLAudioElement>(null)

  const ext = (item.filename.split('.').pop() || '').toLowerCase()
  const isPdf = item.mime_type === 'application/pdf' || ext === 'pdf'
  const isAudio =
    item.file_category === 'audio' ||
    item.mime_type.startsWith('audio/') ||
    ['mp3', 'wav', 'flac', 'ogg', 'm4a', 'aac'].includes(ext)
  const isCodeOrText =
    ['txt', 'md', 'json', 'py', 'js', 'ts', 'tsx', 'jsx', 'css', 'html', 'sh', 'yml', 'yaml', 'csv', 'log', 'env'].includes(ext) ||
    item.mime_type.startsWith('text/')

  // Fetch text content for code/text previews
  useEffect(() => {
    if (!isCodeOrText || isPdf || isAudio) {
      setTextContent(null)
      return
    }

    const controller = new AbortController()
    setIsLoadingText(true)

    fetch(item.preview_url || `/api/v1/files/${item.id}/download`, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error('Failed to load file preview')
        return res.text()
      })
      .then((text) => {
        // Truncate to first 500 KB for preview safety
        setTextContent(text.slice(0, 500000))
        setIsLoadingText(false)
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          setTextContent(null)
          setIsLoadingText(false)
        }
      })

    return () => controller.abort()
  }, [item, isCodeOrText, isPdf, isAudio])

  const copyText = () => {
    if (!textContent) return
    navigator.clipboard.writeText(textContent)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // Audio handlers
  const togglePlayAudio = () => {
    if (!audioRef.current) return
    if (isPlayingAudio) {
      audioRef.current.pause()
      setIsPlayingAudio(false)
    } else {
      audioRef.current.play()
      setIsPlayingAudio(true)
    }
  }

  const formatSecs = (sec: number) => {
    const m = Math.floor(sec / 60)
    const s = Math.floor(sec % 60)
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  // PDF Preview
  if (isPdf) {
    return (
      <div className="w-full h-full flex flex-col bg-[#0a0a0a] border border-[#222222]">
        <div className="h-9 bg-[#111111] border-b border-[#222222] px-3 flex items-center justify-between font-mono text-xs text-[#888888]">
          <span>PDF DOCUMENT PREVIEW</span>
          <Button variant="outline" size="sm" onClick={onDownload} icon={<Download size={12} />}>
            Download
          </Button>
        </div>
        <div className="flex-1 w-full h-full relative">
          <object
            data={item.preview_url || `/api/v1/files/${item.id}/download`}
            type="application/pdf"
            className="w-full h-full"
          >
            <div className="flex flex-col items-center justify-center p-8 h-full text-center font-mono">
              <p className="text-sm text-[#888888] mb-4">Embedded PDF viewer not supported by your browser.</p>
              <Button variant="primary" onClick={onDownload} icon={<Download size={13} />}>
                Download PDF to View
              </Button>
            </div>
          </object>
        </div>
      </div>
    )
  }

  // Audio Preview
  if (isAudio) {
    return (
      <div className="flex flex-col items-center justify-center p-8 bg-[#0c0c0c] border border-[#222222] font-mono max-w-lg w-full">
        <audio
          ref={audioRef}
          src={item.preview_url || `/api/v1/files/${item.id}/download`}
          onTimeUpdate={() => audioRef.current && setAudioTime(audioRef.current.currentTime)}
          onLoadedMetadata={() => audioRef.current && setAudioDuration(audioRef.current.duration)}
          onEnded={() => setIsPlayingAudio(false)}
        />
        <div className="w-20 h-20 border border-[#333333] bg-[#000000] flex items-center justify-center mb-5 text-[#ffffff]">
          <Music size={32} />
        </div>
        <div className="text-sm font-semibold text-[#f0f0f0] truncate max-w-full mb-1">
          {item.filename}
        </div>
        <div className="text-xs text-[#777777] mb-6">
          {formatBytes(item.size_bytes)} • {ext.toUpperCase()} Audio Track
        </div>

        {/* Audio Controls */}
        <div className="w-full bg-[#111111] border border-[#282828] p-4 flex flex-col gap-3">
          <div className="flex items-center justify-between text-xs text-[#888888]">
            <span>{formatSecs(audioTime)}</span>
            <span>{formatSecs(audioDuration || 0)}</span>
          </div>

          <div
            onClick={(e) => {
              if (!audioRef.current || !audioDuration) return
              const rect = e.currentTarget.getBoundingClientRect()
              const pct = (e.clientX - rect.left) / rect.width
              audioRef.current.currentTime = pct * audioDuration
            }}
            className="w-full h-2 bg-[#222222] cursor-pointer relative"
          >
            <div
              className="h-full bg-[#ffffff]"
              style={{ width: `${audioDuration ? (audioTime / audioDuration) * 100 : 0}%` }}
            />
          </div>

          <div className="flex items-center justify-between pt-1">
            <button
              onClick={togglePlayAudio}
              className="p-2 border border-[#333333] bg-[#000000] text-[#ffffff] hover:border-[#ffffff] transition-colors"
            >
              {isPlayingAudio ? <Pause size={16} /> : <Play size={16} />}
            </button>
            <Button variant="outline" size="sm" onClick={onDownload} icon={<Download size={13} />}>
              Download
            </Button>
          </div>
        </div>
      </div>
    )
  }

  // Code & Text Preview
  if (isCodeOrText) {
    return (
      <div className="w-full h-full max-w-5xl flex flex-col bg-[#0a0a0a] border border-[#222222] font-mono text-xs">
        <div className="h-9 bg-[#111111] border-b border-[#222222] px-3 flex items-center justify-between text-[#888888] select-none">
          <div className="flex items-center gap-2">
            <span className="uppercase text-[11px] font-semibold text-[#f0f0f0]">{ext || 'TEXT'} FILE PREVIEW</span>
            <span className="text-[10px] text-[#555555]">({formatBytes(item.size_bytes)})</span>
          </div>
          <div className="flex items-center gap-2">
            {textContent && (
              <button
                onClick={copyText}
                className="px-2 py-1 border border-[#282828] hover:border-[#444444] text-[#cccccc] hover:text-[#ffffff] flex items-center gap-1 text-[11px]"
              >
                {copied ? <Check size={12} className="text-[#22c55e]" /> : <Copy size={12} />}
                <span>{copied ? 'Copied' : 'Copy'}</span>
              </button>
            )}
            <button
              onClick={onDownload}
              className="px-2 py-1 border border-[#282828] hover:border-[#444444] text-[#cccccc] hover:text-[#ffffff] flex items-center gap-1 text-[11px]"
            >
              <Download size={12} />
              <span>Download</span>
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-auto p-4 select-text bg-[#000000]">
          {isLoadingText ? (
            <div className="text-center py-12 text-[#666666]">Loading file contents...</div>
          ) : textContent !== null ? (
            <pre className="text-[#e0e0e0] font-mono leading-relaxed whitespace-pre-wrap break-all">
              {textContent}
            </pre>
          ) : (
            <div className="text-center py-12 text-[#666666]">
              Unable to load preview directly.
              <div className="mt-3">
                <Button variant="primary" onClick={onDownload} icon={<Download size={13} />}>
                  Download File
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  // Fallback Binary / Archive Card
  return (
    <div className="flex flex-col items-center justify-center p-8 border border-[#222222] bg-[#0c0c0c] text-center font-mono max-w-md">
      <div className="w-16 h-16 border border-[#333333] flex items-center justify-center text-xl mb-4 text-[#888888]">
        {item.file_category === 'archive' ? 'ZIP' : 'DOC'}
      </div>
      <div className="text-sm font-semibold text-[#f0f0f0] break-all mb-1">
        {item.filename}
      </div>
      <div className="text-xs text-[#777777] mb-4">
        {formatBytes(item.size_bytes)} • {item.mime_type}
      </div>
      <Button variant="primary" onClick={onDownload} icon={<Download size={14} />}>
        Download Original File
      </Button>
    </div>
  )
}

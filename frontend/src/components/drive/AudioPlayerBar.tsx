import React, { useRef, useState, useEffect } from 'react'
import { useVaultStore } from '../../store/useVaultStore'
import { Play, Pause, Volume2, VolumeX, X, Download, Music } from 'lucide-react'

export const AudioPlayerBar: React.FC = () => {
  const { activeAudioItem, setActiveAudioItem } = useVaultStore()
  const audioRef = useRef<HTMLAudioElement>(null)

  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [isMuted, setIsMuted] = useState(false)

  useEffect(() => {
    if (!activeAudioItem) {
      setIsPlaying(false)
      setCurrentTime(0)
      return
    }

    const audio = audioRef.current
    if (!audio) return

    audio.src = activeAudioItem.preview_url || `/api/v1/files/${activeAudioItem.id}/download`
    audio.load()
    audio.play().then(() => setIsPlaying(true)).catch(() => setIsPlaying(false))

    const handleTimeUpdate = () => setCurrentTime(audio.currentTime)
    const handleLoadedMetadata = () => setDuration(audio.duration)
    const handleEnded = () => setIsPlaying(false)

    audio.addEventListener('timeupdate', handleTimeUpdate)
    audio.addEventListener('loadedmetadata', handleLoadedMetadata)
    audio.addEventListener('ended', handleEnded)

    return () => {
      audio.removeEventListener('timeupdate', handleTimeUpdate)
      audio.removeEventListener('loadedmetadata', handleLoadedMetadata)
      audio.removeEventListener('ended', handleEnded)
    }
  }, [activeAudioItem])

  if (!activeAudioItem) return null

  const togglePlay = () => {
    if (!audioRef.current) return
    if (isPlaying) {
      audioRef.current.pause()
      setIsPlaying(false)
    } else {
      audioRef.current.play()
      setIsPlaying(true)
    }
  }

  const toggleMute = () => {
    if (!audioRef.current) return
    audioRef.current.muted = !isMuted
    setIsMuted(!isMuted)
  }

  const handleSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!audioRef.current || !duration) return
    const rect = e.currentTarget.getBoundingClientRect()
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    const newTime = pct * duration
    audioRef.current.currentTime = newTime
    setCurrentTime(newTime)
  }

  const formatSecs = (time: number) => {
    const m = Math.floor(time / 60)
    const s = Math.floor(time % 60)
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  const handleDownload = () => {
    const a = document.createElement('a')
    a.href = activeAudioItem.preview_url || `/api/v1/files/${activeAudioItem.id}/download`
    a.download = activeAudioItem.filename
    a.click()
  }

  return (
    <aside aria-label="Audio player" className="fixed bottom-0 left-0 right-0 z-40 bg-[#080808] border-t border-[#282828] text-[#f0f0f0] font-mono text-xs select-none">
      <audio ref={audioRef} />

      {/* Scrubber line across top edge */}
      <div
        onClick={handleSeek}
        className="w-full h-1.5 bg-[#1c1c1c] cursor-pointer relative group"
        title="Seek audio track"
      >
        <div
          className="h-full bg-[#ffffff] transition-all"
          style={{ width: `${duration ? (currentTime / duration) * 100 : 0}%` }}
        />
      </div>

      <div className="h-14 px-3 sm:px-6 flex items-center justify-between gap-3">
        {/* Track info */}
        <div className="flex items-center gap-3 min-w-0 max-w-[200px] sm:max-w-md">
          <div className="w-8 h-8 bg-[#111111] border border-[#282828] flex items-center justify-center text-[#aaaaaa] flex-shrink-0">
            <Music size={14} />
          </div>
          <div className="truncate">
            <div className="font-semibold text-xs text-[#ffffff] truncate" title={activeAudioItem.filename}>
              {activeAudioItem.filename}
            </div>
            <div className="text-[10px] text-[#777777]">
              {formatSecs(currentTime)} / {formatSecs(duration || 0)} • {(activeAudioItem.size_bytes / (1024 * 1024)).toFixed(1)} MB
            </div>
          </div>
        </div>

        {/* Center Controls */}
        <div className="flex items-center gap-2">
          <button
            onClick={togglePlay}
            className="w-9 h-9 border border-[#333333] hover:border-[#ffffff] bg-[#000000] text-[#ffffff] flex items-center justify-center transition-colors"
            title={isPlaying ? 'Pause' : 'Play'}
          >
            {isPlaying ? <Pause size={15} /> : <Play size={15} fill="white" />}
          </button>
        </div>

        {/* Right Action Tools */}
        <div className="flex items-center gap-1 sm:gap-2">
          <button
            onClick={toggleMute}
            className="p-1.5 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#282828]"
            title={isMuted ? 'Unmute' : 'Mute'}
          >
            {isMuted ? <VolumeX size={15} /> : <Volume2 size={15} />}
          </button>

          <button
            onClick={handleDownload}
            className="p-1.5 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#282828] hidden sm:block"
            title="Download track"
          >
            <Download size={15} />
          </button>

          <button
            onClick={() => setActiveAudioItem(null)}
            className="p-1.5 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#282828]"
            title="Close audio player"
          >
            <X size={16} />
          </button>
        </div>
      </div>
    </aside>
  )
}

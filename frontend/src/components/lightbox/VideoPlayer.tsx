import React, { useRef, useState, useEffect } from 'react'
import { Play, Pause, Volume2, VolumeX, Maximize, RotateCcw } from 'lucide-react'

interface VideoPlayerProps {
  src: string
  poster?: string
  filename: string
}

export const VideoPlayer: React.FC<VideoPlayerProps> = ({ src, poster }) => {
  const videoRef = useRef<HTMLVideoElement>(null)
  const progressRef = useRef<HTMLDivElement>(null)

  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [bufferedEnd, setBufferedEnd] = useState(0)
  const [volume, setVolume] = useState(1)
  const [isMuted, setIsMuted] = useState(false)
  const [playbackRate, setPlaybackRate] = useState(1)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const handleTimeUpdate = () => {
      setCurrentTime(video.currentTime)
      if (video.buffered.length > 0) {
        setBufferedEnd(video.buffered.end(video.buffered.length - 1))
      }
    }

    const handleLoadedMetadata = () => {
      setDuration(video.duration)
    }

    const handleEnded = () => setIsPlaying(false)

    video.addEventListener('timeupdate', handleTimeUpdate)
    video.addEventListener('loadedmetadata', handleLoadedMetadata)
    video.addEventListener('ended', handleEnded)

    return () => {
      video.removeEventListener('timeupdate', handleTimeUpdate)
      video.removeEventListener('loadedmetadata', handleLoadedMetadata)
      video.removeEventListener('ended', handleEnded)
    }
  }, [])

  const togglePlay = () => {
    if (!videoRef.current) return
    if (isPlaying) {
      videoRef.current.pause()
      setIsPlaying(false)
    } else {
      videoRef.current.play()
      setIsPlaying(true)
    }
  }

  const handleSeekPos = (clientX: number) => {
    if (!videoRef.current || !progressRef.current || !duration) return
    const rect = progressRef.current.getBoundingClientRect()
    const pos = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    const newTime = pos * duration
    videoRef.current.currentTime = newTime
    setCurrentTime(newTime)
  }

  const handleSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    handleSeekPos(e.clientX)
  }

  const handleTouchSeek = (e: React.TouchEvent<HTMLDivElement>) => {
    if (e.touches.length > 0) {
      handleSeekPos(e.touches[0].clientX)
    }
  }

  const toggleMute = () => {
    if (!videoRef.current) return
    videoRef.current.muted = !isMuted
    setIsMuted(!isMuted)
  }

  const changeRate = () => {
    if (!videoRef.current) return
    const rates = [1, 1.5, 2, 0.5]
    const nextIdx = (rates.indexOf(playbackRate) + 1) % rates.length
    const nextRate = rates[nextIdx]
    videoRef.current.playbackRate = nextRate
    setPlaybackRate(nextRate)
  }

  const formatTime = (time: number) => {
    const mins = Math.floor(time / 60)
    const secs = Math.floor(time % 60)
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }

  return (
    <div className="relative w-full h-full flex flex-col items-center justify-center bg-[#000000] group select-none p-2 sm:p-4">
      <video
        ref={videoRef}
        src={src}
        poster={poster}
        onClick={togglePlay}
        className="max-h-[60vh] sm:max-h-[80vh] max-w-full object-contain cursor-pointer"
        playsInline
      />

      {/* Control Overlay Bar */}
      <div className="w-full max-w-4xl bg-[#0c0c0c] border border-[#222222] p-2 mt-2 sm:mt-3 font-mono text-xs flex flex-col gap-2">
        {/* Progress Bar (Range Scrubber) */}
        <div
          ref={progressRef}
          onClick={handleSeek}
          onTouchStart={handleTouchSeek}
          onTouchMove={handleTouchSeek}
          className="relative h-3 sm:h-2 bg-[#1c1c1c] cursor-pointer touch-none"
        >
          {/* Buffered range (Range requests) */}
          <div
            className="absolute top-0 left-0 h-full bg-[#333333]"
            style={{ width: `${duration ? (bufferedEnd / duration) * 100 : 0}%` }}
          />
          {/* Played range */}
          <div
            className="absolute top-0 left-0 h-full bg-[#ffffff]"
            style={{ width: `${duration ? (currentTime / duration) * 100 : 0}%` }}
          />
        </div>

        {/* Action Controls */}
        <div className="flex items-center justify-between text-[#e5e5e5]">
          <div className="flex items-center gap-3">
            <button
              onClick={togglePlay}
              className="p-1 hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
            >
              {isPlaying ? <Pause size={14} /> : <Play size={14} />}
            </button>

            <button
              onClick={toggleMute}
              className="p-1 hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
            >
              {isMuted ? <VolumeX size={14} /> : <Volume2 size={14} />}
            </button>

            <span className="text-[11px] text-[#888888]">
              {formatTime(currentTime)} / {formatTime(duration)}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={changeRate}
              className="px-1.5 py-0.5 border border-[#282828] text-[10px] hover:border-[#555555]"
            >
              {playbackRate}x
            </button>

            <button
              onClick={() => videoRef.current?.requestFullscreen()}
              className="p-1 hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
            >
              <Maximize size={14} />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

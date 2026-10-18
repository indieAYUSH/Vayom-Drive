import React, { useRef, useState, useEffect } from 'react'
import { Play, Pause, Volume2, VolumeX, Maximize, AlertTriangle, RefreshCw, ExternalLink, Download } from 'lucide-react'

interface VideoPlayerProps {
  src: string
  poster?: string
  filename: string
}

export const VideoPlayer: React.FC<VideoPlayerProps> = ({ src, poster, filename }) => {
  const videoRef = useRef<HTMLVideoElement>(null)
  const progressRef = useRef<HTMLDivElement>(null)

  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [bufferedEnd, setBufferedEnd] = useState(0)
  const [volume, setVolume] = useState(1)
  const [isMuted, setIsMuted] = useState(false)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [isLoading, setIsLoading] = useState(true)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const isScrubbingRef = useRef(false)
  const lastSeekTimeRef = useRef(0)
  const seekThrottleTimeout = useRef<any>(null)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const handleTimeUpdate = () => {
      if (!isScrubbingRef.current) {
        setCurrentTime(video.currentTime)
      }
      // Truthful buffer detection ahead of playhead
      if (video.buffered.length > 0) {
        let currentBuffered = 0
        for (let i = 0; i < video.buffered.length; i++) {
          if (video.buffered.start(i) <= video.currentTime && video.currentTime <= video.buffered.end(i) + 0.5) {
            currentBuffered = video.buffered.end(i)
            break
          }
        }
        setBufferedEnd(currentBuffered || video.buffered.end(video.buffered.length - 1))
      }
    }

    const handleLoadedMetadata = () => {
      setDuration(video.duration)
      setIsLoading(false)
      setErrorMsg(null)
    }

    const handleEnded = () => setIsPlaying(false)

    const handleWaiting = () => setIsLoading(true)
    const handlePlaying = () => {
      setIsLoading(false)
      setIsPlaying(true)
      setErrorMsg(null)
    }
    const handlePause = () => setIsPlaying(false)

    const handleVideoError = () => {
      const err = video.error
      setIsLoading(false)
      setIsPlaying(false)
      let msg = "Could not play video in browser."
      if (err) {
        if (err.code === 1) msg = "Playback aborted by client."
        else if (err.code === 2) msg = "Network error occurred while fetching video stream."
        else if (err.code === 3) msg = "Video decode error encountered."
        else if (err.code === 4) msg = "This video format or codec is not supported natively by your browser."
      }
      setErrorMsg(msg)
    }

    video.addEventListener('timeupdate', handleTimeUpdate)
    video.addEventListener('loadedmetadata', handleLoadedMetadata)
    video.addEventListener('ended', handleEnded)
    video.addEventListener('waiting', handleWaiting)
    video.addEventListener('playing', handlePlaying)
    video.addEventListener('pause', handlePause)
    video.addEventListener('error', handleVideoError)

    return () => {
      video.removeEventListener('timeupdate', handleTimeUpdate)
      video.removeEventListener('loadedmetadata', handleLoadedMetadata)
      video.removeEventListener('ended', handleEnded)
      video.removeEventListener('waiting', handleWaiting)
      video.removeEventListener('playing', handlePlaying)
      video.removeEventListener('pause', handlePause)
      video.removeEventListener('error', handleVideoError)
      if (seekThrottleTimeout.current) clearTimeout(seekThrottleTimeout.current)
    }
  }, [src])

  const togglePlay = () => {
    if (!videoRef.current) return
    if (isPlaying) {
      videoRef.current.pause()
      setIsPlaying(false)
    } else {
      videoRef.current.play().then(() => {
        setIsPlaying(true)
        setErrorMsg(null)
      }).catch((err) => {
        console.warn("Video play error:", err)
        setIsPlaying(false)
      })
    }
  }

  const applySeek = (newTime: number, immediate: boolean = false) => {
    if (!videoRef.current || !duration) return
    setCurrentTime(newTime)
    const now = Date.now()
    if (immediate || now - lastSeekTimeRef.current > 150) {
      lastSeekTimeRef.current = now
      if (seekThrottleTimeout.current) clearTimeout(seekThrottleTimeout.current)
      videoRef.current.currentTime = newTime
    } else {
      if (seekThrottleTimeout.current) clearTimeout(seekThrottleTimeout.current)
      seekThrottleTimeout.current = setTimeout(() => {
        if (videoRef.current) {
          videoRef.current.currentTime = newTime
        }
      }, 100)
    }
  }

  const handleSeekPos = (clientX: number, isFinal: boolean = false) => {
    if (!videoRef.current || !progressRef.current || !duration) return
    const rect = progressRef.current.getBoundingClientRect()
    const pos = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    const newTime = pos * duration
    applySeek(newTime, isFinal)
  }

  const handleSeekClick = (e: React.MouseEvent<HTMLDivElement>) => {
    handleSeekPos(e.clientX, true)
  }

  const handleTouchStart = (e: React.TouchEvent<HTMLDivElement>) => {
    isScrubbingRef.current = true
    if (e.touches.length > 0) {
      handleSeekPos(e.touches[0].clientX, false)
    }
  }

  const handleTouchMove = (e: React.TouchEvent<HTMLDivElement>) => {
    if (e.touches.length > 0) {
      handleSeekPos(e.touches[0].clientX, false)
    }
  }

  const handleTouchEnd = (e: React.TouchEvent<HTMLDivElement>) => {
    isScrubbingRef.current = false
    if (e.changedTouches.length > 0) {
      handleSeekPos(e.changedTouches[0].clientX, true)
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
      {/* Video Viewport Container */}
      <div className="relative max-h-[60vh] sm:max-h-[80vh] max-w-full flex items-center justify-center">
        <video
          ref={videoRef}
          src={src}
          poster={poster}
          onClick={togglePlay}
          preload="metadata"
          playsInline
          className="max-h-[60vh] sm:max-h-[80vh] max-w-full object-contain cursor-pointer"
        />

        {/* Buffering Spinner */}
        {isLoading && !errorMsg && (
          <div className="absolute inset-0 flex items-center justify-center bg-[#000000]/40 pointer-events-none">
            <div className="w-8 h-8 border-2 border-[#ffffff] border-t-transparent animate-spin" />
          </div>
        )}

        {/* Playback Error Fallback Overlay */}
        {errorMsg && (
          <div className="absolute inset-0 z-20 bg-[#080808]/95 border border-[#333333] flex flex-col items-center justify-center p-6 text-center space-y-3">
            <div className="w-10 h-10 rounded-full bg-[#1f0a0a] border border-[#ef4444] flex items-center justify-center text-[#ef4444]">
              <AlertTriangle size={20} />
            </div>
            <div className="max-w-md">
              <div className="text-xs font-bold text-[#ef4444] tracking-wider uppercase">Playback Issue</div>
              <p className="text-[11px] text-[#aaaaaa] mt-1 leading-relaxed">{errorMsg}</p>
            </div>
            <div className="flex flex-wrap items-center justify-center gap-2 pt-2">
              <button
                onClick={() => {
                  setErrorMsg(null)
                  setIsLoading(true)
                  if (videoRef.current) {
                    videoRef.current.load()
                    videoRef.current.play().catch(() => {})
                  }
                }}
                className="px-3 py-1.5 bg-[#141414] border border-[#333333] hover:border-[#ffffff] text-xs text-[#ffffff] flex items-center gap-1.5 transition-colors"
              >
                <RefreshCw size={12} /> Retry Stream
              </button>
              <a
                href={src}
                target="_blank"
                rel="noreferrer"
                className="px-3 py-1.5 bg-[#ffffff] text-[#000000] text-xs font-semibold flex items-center gap-1.5 hover:bg-[#e5e5e5] transition-colors"
              >
                <ExternalLink size={12} /> Open in Native Player
              </a>
              <a
                href={src}
                download={filename}
                className="px-3 py-1.5 bg-[#141414] border border-[#333333] hover:border-[#ffffff] text-xs text-[#ffffff] flex items-center gap-1.5 transition-colors"
              >
                <Download size={12} /> Download Video
              </a>
            </div>
          </div>
        )}
      </div>

      {/* Control Overlay Bar */}
      <div className="w-full max-w-4xl bg-[#0c0c0c] border border-[#222222] p-2 mt-2 sm:mt-3 font-mono text-xs flex flex-col gap-2">
        {/* Progress Bar (Range Scrubber) */}
        <div
          ref={progressRef}
          onClick={handleSeekClick}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
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
              title={isPlaying ? "Pause" : "Play"}
            >
              {isPlaying ? <Pause size={14} /> : <Play size={14} />}
            </button>

            <button
              onClick={toggleMute}
              className="p-1 hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
              title={isMuted ? "Unmute" : "Mute"}
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
              title="Playback Rate"
            >
              {playbackRate}x
            </button>

            {/* Native Player / External Tab Link */}
            <a
              href={src}
              target="_blank"
              rel="noreferrer"
              className="p-1 hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
              title="Open stream in native player or new tab"
            >
              <ExternalLink size={14} />
            </a>

            <button
              onClick={() => videoRef.current?.requestFullscreen()}
              className="p-1 hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
              title="Fullscreen"
            >
              <Maximize size={14} />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

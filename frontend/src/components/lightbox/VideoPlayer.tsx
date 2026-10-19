import React, { useRef, useState, useEffect, useCallback } from 'react'
import { Play, Pause, Volume2, VolumeX, Maximize, AlertTriangle, RefreshCw, ExternalLink, Download, SlidersHorizontal, Check, Loader2 } from 'lucide-react'
import type { FileItem } from '../../types/vault'
import { api } from '../../services/api'
import { resolveMediaUrl } from '../../utils/apiUrl'

interface VideoPlayerProps {
  item?: FileItem
  src: string
  poster?: string
  filename: string
}

export const VideoPlayer: React.FC<VideoPlayerProps> = ({ item, src, poster, filename }) => {
  const videoRef = useRef<HTMLVideoElement>(null)
  const progressRef = useRef<HTMLDivElement>(null)

  // Live proxy state tracking for background encoding/upload completion
  const [dynamicProxyUrl, setDynamicProxyUrl] = useState<string | null>(item?.proxy_url || null)
  const [dynamicProxyStatus, setDynamicProxyStatus] = useState<string | undefined>(item?.proxy_status)

  // Stream mode: 'original' vs 'proxy'
  const hasProxy = (dynamicProxyStatus === 'ready' || item?.proxy_status === 'ready') && !!(dynamicProxyUrl || item?.proxy_url)
  const originalUrl = resolveMediaUrl(item?.preview_url || src)
  const proxyUrl = resolveMediaUrl(dynamicProxyUrl || item?.proxy_url || '')
  const isOptimizing = !hasProxy && (
    dynamicProxyStatus === 'uploading' ||
    dynamicProxyStatus === 'encoding' ||
    dynamicProxyStatus === 'queued' ||
    item?.proxy_status === 'uploading' ||
    item?.proxy_status === 'encoding' ||
    item?.proxy_status === 'queued'
  )

  const [activeStream, setActiveStream] = useState<'original' | 'proxy'>(hasProxy ? 'proxy' : 'original')
  const [currentSrc, setCurrentSrc] = useState<string>(hasProxy ? proxyUrl : src)
  const [forceOriginal, setForceOriginal] = useState(false)

  // Non-blocking toast state
  const [toastMessage, setToastMessage] = useState<string | null>(null)
  const toastTimeoutRef = useRef<any>(null)

  const showToast = useCallback((msg: string) => {
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current)
    setToastMessage(msg)
    toastTimeoutRef.current = setTimeout(() => {
      setToastMessage(null)
    }, 3500)
  }, [])

  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [bufferedEnd, setBufferedEnd] = useState(0)
  const [bufferedRanges, setBufferedRanges] = useState<{ start: number; end: number }[]>([])
  const [volume, setVolume] = useState(1)
  const [isMuted, setIsMuted] = useState(false)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [isLoading, setIsLoading] = useState(true)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const isScrubbingRef = useRef(false)
  const lastSeekTimeRef = useRef(0)
  const seekThrottleTimeout = useRef<any>(null)

  // Seamless switch tracking
  const seekOnLoadRef = useRef<number | null>(null)
  const wasPlayingBeforeSwitchRef = useRef(false)
  const healthyBufferSecondsRef = useRef(0)
  const stallCountRef = useRef(0)
  const lastSwitchTimeRef = useRef(0)
  const dragHandlersRef = useRef<{ move: (e: MouseEvent) => void; up: (e: MouseEvent) => void } | null>(null)

  // Initial stream selection: default to stream-optimized proxy (zero buffering) when available
  useEffect(() => {
    if (!hasProxy) {
      setActiveStream('original')
      setCurrentSrc(originalUrl)
      return
    }

    if (!forceOriginal) {
      // H-51: preserve playback position/state when auto-switching to the proxy
      if (currentSrc !== proxyUrl) {
        const video = videoRef.current
        seekOnLoadRef.current = video ? video.currentTime : 0
        wasPlayingBeforeSwitchRef.current = isPlaying
      }
      setActiveStream('proxy')
      setCurrentSrc(proxyUrl)
    } else {
      setActiveStream('original')
      setCurrentSrc(originalUrl)
    }
  }, [hasProxy, originalUrl, proxyUrl, forceOriginal])

  // Dynamic poll for background proxy completion when video is opened while encoding/uploading
  useEffect(() => {
    if (!item?.id || hasProxy || !isOptimizing) return

    const pollInterval = setInterval(async () => {
      try {
        const files = await api.listFiles({ q: item.filename, limit: 10 })
        const found = files.find(f => f.id === item.id)
        if (found?.proxy_status === 'ready' && found.proxy_url) {
          clearInterval(pollInterval)
          setDynamicProxyStatus('ready')
          setDynamicProxyUrl(found.proxy_url)
          if (!forceOriginal) {
            const video = videoRef.current
            const prevTime = video ? video.currentTime : 0
            seekOnLoadRef.current = prevTime
            wasPlayingBeforeSwitchRef.current = isPlaying
            setActiveStream('proxy')
            setCurrentSrc(resolveMediaUrl(found.proxy_url))
            showToast('Instant FastStart preview is ready! Switched stream.')
          }
        }
      } catch {}
    }, 4000)

    return () => clearInterval(pollInterval)
  }, [item?.id, item?.filename, hasProxy, isOptimizing, forceOriginal, isPlaying, showToast])

  // Stream switcher with seamless timestamp preservation and anti-flapping guard
  const switchStream = useCallback((target: 'original' | 'proxy', reason: string) => {
    if (target === activeStream) return
    if (target === 'proxy' && (!hasProxy || forceOriginal)) return

    const now = Date.now()
    // Anti-flapping: enforce max 1 automatic switch per 60s unless explicitly forced or codec error fallback
    if (!reason.includes('forced') && !reason.includes('fallback') && now - lastSwitchTimeRef.current < 60000) {
      return
    }
    lastSwitchTimeRef.current = now

    const video = videoRef.current
    const prevTime = video ? video.currentTime : currentTime
    seekOnLoadRef.current = prevTime
    wasPlayingBeforeSwitchRef.current = isPlaying

    setActiveStream(target)
    const newSrc = target === 'proxy' ? proxyUrl : originalUrl
    setCurrentSrc(newSrc)

    if (target === 'proxy') {
      showToast(`Switched to compressed preview: ${reason}`)
    } else {
      showToast(`Switched to original quality: ${reason}`)
    }
  }, [activeStream, hasProxy, forceOriginal, originalUrl, proxyUrl, currentTime, isPlaying, showToast])

  // H-38: latest values for the media effect's event handlers — read at call time so the
  // effect below only re-runs when the actual media source changes (not on every timeupdate).
  const mediaLiveRef = useRef({ activeStream, hasProxy, forceOriginal, isOptimizing, switchStream })
  useEffect(() => {
    mediaLiveRef.current = { activeStream, hasProxy, forceOriginal, isOptimizing, switchStream }
  })

  const toggleForceOriginal = () => {
    if (!forceOriginal) {
      setForceOriginal(true)
      if (activeStream !== 'original') {
        switchStream('original', 'forced by user')
      } else {
        showToast('Original quality forced for this session')
      }
    } else {
      setForceOriginal(false)
      showToast('Automatic adaptive streaming enabled')
    }
  }

  useEffect(() => {
    setIsPlaying(false)
    setIsLoading(true)
    setErrorMsg(null)

    const video = videoRef.current
    if (!video) return

    const updateBufferInfo = () => {
      if (!video) return
      if (video.buffered.length > 0) {
        const ranges: { start: number; end: number }[] = []
        let currentBuffered = 0
        for (let i = 0; i < video.buffered.length; i++) {
          const bStart = video.buffered.start(i)
          const bEnd = video.buffered.end(i)
          ranges.push({ start: bStart, end: bEnd })
          if (bStart <= video.currentTime && video.currentTime <= bEnd + 0.5) {
            currentBuffered = bEnd
          }
        }
        setBufferedRanges(ranges)
        const finalBufferedEnd = currentBuffered || video.buffered.end(video.buffered.length - 1)
        setBufferedEnd(finalBufferedEnd)

        // Adaptive upgrade check: upgrade on pause, seek, or sustained healthy buffer (>20s for >30s)
        const live = mediaLiveRef.current
        if (live.activeStream === 'proxy' && !live.forceOriginal && live.hasProxy) {
          const bufferAhead = finalBufferedEnd - video.currentTime
          const isEligibleMoment = video.paused || isScrubbingRef.current || bufferAhead > 25.0
          if (bufferAhead > 15.0 && isEligibleMoment) {
            healthyBufferSecondsRef.current += 1
            if (healthyBufferSecondsRef.current >= 15) {
              healthyBufferSecondsRef.current = 0
              live.switchStream('original', 'connection recovered')
            }
          } else {
            healthyBufferSecondsRef.current = 0
          }
        }
      }
    }

    const handleTimeUpdate = () => {
      if (!isScrubbingRef.current) {
        setCurrentTime(video.currentTime)
      }
      updateBufferInfo()
    }

    const handleLoadedMetadata = () => {
      setDuration(video.duration)
      setIsLoading(false)
      setErrorMsg(null)

      if (seekOnLoadRef.current !== null && seekOnLoadRef.current > 0) {
        video.currentTime = seekOnLoadRef.current
        seekOnLoadRef.current = null
      }
      if (wasPlayingBeforeSwitchRef.current) {
        video.play().then(() => setIsPlaying(true)).catch(() => {})
        wasPlayingBeforeSwitchRef.current = false
      }
    }

    const handleEnded = () => setIsPlaying(false)

    const handleWaiting = () => {
      setIsLoading(true)
      // Adaptive downgrade check: if playing original and stalled with low buffer, switch to proxy
      const live = mediaLiveRef.current
      if (live.activeStream === 'original' && live.hasProxy && !live.forceOriginal) {
        stallCountRef.current += 1
        if (stallCountRef.current >= 1) {
          stallCountRef.current = 0
          live.switchStream('proxy', 'slow connection')
        }
      } else if (live.activeStream === 'original' && live.isOptimizing) {
        showToast('Buffering uncompressed original from Telegram (preview optimizing...)')
      }
    }

    const handlePlaying = () => {
      setIsLoading(false)
      setIsPlaying(true)
      setErrorMsg(null)
      stallCountRef.current = 0
    }

    const handlePause = () => setIsPlaying(false)

    const handleVideoError = () => {
      const err = video.error
      setIsLoading(false)
      setIsPlaying(false)

      // Fallback: If original failed to play (e.g. 4K HEVC unsupported in browser), auto-fallback to stream_v1 proxy!
      const live = mediaLiveRef.current
      if (live.activeStream === 'original' && live.hasProxy && !live.forceOriginal) {
        live.switchStream('proxy', 'browser codec fallback')
        return
      }

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
    video.addEventListener('progress', updateBufferInfo)
    video.addEventListener('loadedmetadata', handleLoadedMetadata)
    video.addEventListener('ended', handleEnded)
    video.addEventListener('waiting', handleWaiting)
    video.addEventListener('playing', handlePlaying)
    video.addEventListener('pause', handlePause)
    video.addEventListener('error', handleVideoError)

    return () => {
      video.removeEventListener('timeupdate', handleTimeUpdate)
      video.removeEventListener('progress', updateBufferInfo)
      video.removeEventListener('loadedmetadata', handleLoadedMetadata)
      video.removeEventListener('ended', handleEnded)
      video.removeEventListener('waiting', handleWaiting)
      video.removeEventListener('playing', handlePlaying)
      video.removeEventListener('pause', handlePause)
      video.removeEventListener('error', handleVideoError)
      if (seekThrottleTimeout.current) clearTimeout(seekThrottleTimeout.current)
      if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current)
    }
  }, [currentSrc])

  // H-38/H-53: unmount-only teardown — release the media element (stops background
  // downloads) and drop any window drag listeners left by an in-progress scrub.
  useEffect(() => {
    const video = videoRef.current
    return () => {
      const drag = dragHandlersRef.current
      if (drag) {
        window.removeEventListener('mousemove', drag.move)
        window.removeEventListener('mouseup', drag.up)
        dragHandlersRef.current = null
      }
      isScrubbingRef.current = false
      if (!video) return
      try {
        video.pause()
        video.removeAttribute('src')
        video.load()
      } catch {}
    }
  }, [])

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

  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    isScrubbingRef.current = true
    handleSeekPos(e.clientX, false)

    const handleMouseMove = (moveEvent: MouseEvent) => {
      handleSeekPos(moveEvent.clientX, false)
    }

    const handleMouseUp = (upEvent: MouseEvent) => {
      isScrubbingRef.current = false
      handleSeekPos(upEvent.clientX, true)
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
      dragHandlersRef.current = null
    }

    dragHandlersRef.current = { move: handleMouseMove, up: handleMouseUp }
    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
  }

  const handleTouchStart = (e: React.TouchEvent<HTMLDivElement>) => {
    e.stopPropagation()
    isScrubbingRef.current = true
    if (e.touches.length > 0) {
      handleSeekPos(e.touches[0].clientX, false)
    }
  }

  const handleTouchMove = (e: React.TouchEvent<HTMLDivElement>) => {
    e.stopPropagation()
    if (e.touches.length > 0) {
      handleSeekPos(e.touches[0].clientX, false)
    }
  }

  const handleTouchEnd = (e: React.TouchEvent<HTMLDivElement>) => {
    e.stopPropagation()
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
          src={currentSrc}
          poster={poster}
          onClick={togglePlay}
          preload="auto"
          playsInline
          className="max-h-[60vh] sm:max-h-[80vh] max-w-full object-contain cursor-pointer"
        />

        {/* Small Top-Left Badge: "Original" or "Compressed preview" */}
        <div className="absolute top-3 left-3 z-20 flex items-center gap-1.5 pointer-events-none">
          {activeStream === 'original' ? (
            <span className="px-2 py-0.5 bg-[#000000]/80 backdrop-blur-md border border-[#22c55e]/50 text-[#4ade80] text-[10px] font-mono tracking-wider uppercase shadow-md flex items-center gap-1">
              <span className={`w-1.5 h-1.5 rounded-full bg-[#22c55e] ${isOptimizing ? 'animate-ping' : ''}`} />
              {isOptimizing ? 'Original • Optimizing Preview...' : 'Original'}
            </span>
          ) : (
            <span className="px-2 py-0.5 bg-[#000000]/80 backdrop-blur-md border border-[#f59e0b]/50 text-[#fbbf24] text-[10px] font-mono tracking-wider uppercase shadow-md flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-[#f59e0b]" />
              Compressed preview (720p)
            </span>
          )}
          {forceOriginal && (
            <span className="px-1.5 py-0.5 bg-[#172554]/90 border border-[#3b82f6]/60 text-[#93c5fd] text-[9px] font-mono uppercase tracking-wider">
              FORCED
            </span>
          )}
        </div>

        {/* Brief Non-Blocking Floating Toast on Any Switch */}
        {toastMessage && (
          <div className="absolute top-3 z-30 px-3 py-1.5 bg-[#0a0a0a]/95 backdrop-blur-md border border-[#333333] text-xs font-mono text-[#ffffff] shadow-2xl flex items-center gap-2 pointer-events-none animate-in fade-in zoom-in-95 duration-200">
            <span className="w-2 h-2 rounded-full bg-[#3b82f6] animate-pulse" />
            <span>{toastMessage}</span>
          </div>
        )}

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
              {hasProxy && activeStream === 'original' && (
                <button
                  onClick={() => switchStream('proxy', 'manual fallback')}
                  className="px-3 py-1.5 bg-[#141414] border border-[#f59e0b] hover:border-[#ffffff] text-xs text-[#fbbf24] flex items-center gap-1.5 transition-colors"
                >
                  Try Compressed Preview
                </button>
              )}
              <a
                href={originalUrl}
                target="_blank"
                rel="noreferrer"
                className="px-3 py-1.5 bg-[#ffffff] text-[#000000] text-xs font-semibold flex items-center gap-1.5 hover:bg-[#e5e5e5] transition-colors"
              >
                <ExternalLink size={12} /> Open in Native Player
              </a>
              <a
                href={resolveMediaUrl(`/api/v1/files/${item?.id || ''}/download`)}
                download={filename}
                className="px-3 py-1.5 bg-[#141414] border border-[#333333] hover:border-[#ffffff] text-xs text-[#ffffff] flex items-center gap-1.5 transition-colors"
              >
                <Download size={12} /> Download Original
              </a>
            </div>
          </div>
        )}
      </div>

      {/* Control Overlay Bar */}
      <div className="w-full max-w-4xl bg-[#0c0c0c] border border-[#222222] p-2 mt-2 sm:mt-3 text-xs flex flex-col gap-2">
        {/* Progress Bar (Range Scrubber) */}
        <div
          ref={progressRef}
          onMouseDown={handleMouseDown}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
          className="relative h-3 sm:h-2 bg-[#1c1c1c] cursor-pointer touch-none"
        >
          {/* Buffered range segments */}
          {bufferedRanges.length > 0 ? (
            bufferedRanges.map((r, idx) => (
              <div
                key={idx}
                className="absolute top-0 h-full bg-[#3a3a3a]"
                style={{
                  left: `${duration ? (r.start / duration) * 100 : 0}%`,
                  width: `${duration ? Math.max(0, ((r.end - r.start) / duration) * 100) : 0}%`
                }}
              />
            ))
          ) : (
            <div
              className="absolute top-0 left-0 h-full bg-[#3a3a3a]"
              style={{ width: `${duration ? (bufferedEnd / duration) * 100 : 0}%` }}
            />
          )}
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

            <span className="text-xs font-mono text-[#888888]">
              {formatTime(currentTime)} / {formatTime(duration)}
            </span>
          </div>

          <div className="flex items-center gap-2">
            {/* Discreet Player-Menu Item: "Force original" Escape Hatch (Session Only) */}
            {hasProxy && (
              <button
                onClick={toggleForceOriginal}
                className={`px-2 py-0.5 border text-xs font-mono flex items-center gap-1 transition-colors ${
                  forceOriginal
                    ? 'bg-[#1e3a5f] border-[#3b82f6] text-[#ffffff]'
                    : 'border-[#282828] text-[#888888] hover:text-[#cccccc] hover:border-[#444444]'
                }`}
                title={forceOriginal ? "Disable forced original (enable adaptive preview)" : "Force original stream (session only)"}
              >
                <SlidersHorizontal size={11} />
                <span>Force original</span>
                {forceOriginal && <Check size={11} className="text-[#38bdf8]" />}
              </button>
            )}

            <button
              onClick={changeRate}
              className="px-1.5 py-0.5 border border-[#282828] text-xs font-mono hover:border-[#555555] text-[#cccccc]"
              title="Playback Rate"
            >
              {playbackRate}x
            </button>

            {/* Native Player / External Tab Link (always original) */}
            <a
              href={originalUrl}
              target="_blank"
              rel="noreferrer"
              className="p-1 hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
              title="Open original stream in native player or new tab"
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

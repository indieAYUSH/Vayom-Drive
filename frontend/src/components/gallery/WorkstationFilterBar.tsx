import React from 'react'
import { useVaultStore } from '../../store/useVaultStore'
import { Camera, Film, Monitor, Aperture, X, RotateCcw } from 'lucide-react'

export const WorkstationFilterBar: React.FC = () => {
  const {
    files,
    activeTab,
    selectedCamera,
    selectedCodec,
    selectedResolution,
    selectedLens,
    setSelectedCamera,
    setSelectedCodec,
    setSelectedResolution,
    setSelectedLens,
    resetWorkstationFilters,
  } = useVaultStore()

  // Only show workstation filter bar in tabs that deal with media (all, images, videos)
  const isMediaTab = ['all', 'images', 'videos', 'folders', 'starred'].includes(activeTab)
  if (!isMediaTab) return null

  // Extract available filter options dynamically from current non-deleted files
  const { cameras, codecs, resolutions, lenses } = React.useMemo(() => {
    const camSet = new Set<string>()
    const codecSet = new Set<string>()
    const resSet = new Set<string>()
    const lensSet = new Set<string>()

    for (const file of files) {
      if (file.is_deleted) continue
      const exif = file.exif
      if (!exif) continue

      const cam = exif.camera_make || exif.make || exif.camera_model || exif.model
      if (cam && typeof cam === 'string' && cam.trim()) {
        camSet.add(cam.trim())
      }

      if (exif.codec && typeof exif.codec === 'string' && exif.codec.trim() && exif.codec !== 'UNKNOWN') {
        codecSet.add(exif.codec.trim().toUpperCase())
      }

      if (exif.resolution_tier && typeof exif.resolution_tier === 'string' && exif.resolution_tier.trim()) {
        resSet.add(exif.resolution_tier.trim())
      }

      if (exif.lens && typeof exif.lens === 'string' && exif.lens.trim()) {
        lensSet.add(exif.lens.trim())
      }
    }

    return {
      cameras: Array.from(camSet).sort(),
      codecs: Array.from(codecSet).sort(),
      resolutions: Array.from(resSet).sort(),
      lenses: Array.from(lensSet).sort(),
    }
  }, [files])

  const hasAnyFiltersAvailable =
    cameras.length > 0 || codecs.length > 0 || resolutions.length > 0 || lenses.length > 0

  const hasActiveFilter =
    Boolean(selectedCamera) || Boolean(selectedCodec) || Boolean(selectedResolution) || Boolean(selectedLens)

  if (!hasAnyFiltersAvailable && !hasActiveFilter) {
    return null
  }

  return (
    <div className="border-b border-[#1f1f1f] bg-[#0c0c0c] px-4 py-1.5 flex items-center gap-2 overflow-x-auto text-[11px] font-mono select-none scrollbar-thin">
      <div className="flex items-center gap-1.5 text-[#666666] uppercase tracking-wider text-[10px] pr-2 border-r border-[#222222] flex-shrink-0">
        <Film size={12} className="text-[#888888]" />
        <span>Workstation</span>
      </div>

      {/* Camera Filter Chips */}
      {cameras.length > 0 && (
        <div className="flex items-center gap-1 flex-shrink-0">
          <Camera size={11} className="text-[#555555] mr-0.5" />
          {cameras.slice(0, 4).map((cam) => {
            const isActive = selectedCamera === cam
            return (
              <button
                key={cam}
                onClick={() => setSelectedCamera(isActive ? null : cam)}
                className={`px-2 py-0.5 rounded-none border transition-colors flex items-center gap-1 ${
                  isActive
                    ? 'bg-[#ffffff] text-[#000000] border-[#ffffff] font-semibold'
                    : 'bg-[#141414] text-[#aaaaaa] border-[#252525] hover:border-[#444444] hover:text-[#f0f0f0]'
                }`}
              >
                <span>{cam}</span>
                {isActive && <X size={10} />}
              </button>
            )
          })}
        </div>
      )}

      {/* Video Codec Filter Chips */}
      {codecs.length > 0 && (
        <div className="flex items-center gap-1 flex-shrink-0 pl-1 border-l border-[#222222]">
          <span className="text-[10px] text-[#555555]">CODEC:</span>
          {codecs.map((c) => {
            const isActive = selectedCodec === c
            return (
              <button
                key={c}
                onClick={() => setSelectedCodec(isActive ? null : c)}
                className={`px-2 py-0.5 rounded-none border transition-colors flex items-center gap-1 ${
                  isActive
                    ? 'bg-[#00e5ff] text-[#000000] border-[#00e5ff] font-semibold'
                    : 'bg-[#141414] text-[#aaaaaa] border-[#252525] hover:border-[#444444] hover:text-[#f0f0f0]'
                }`}
              >
                <span>{c}</span>
                {isActive && <X size={10} />}
              </button>
            )
          })}
        </div>
      )}

      {/* Resolution Tier Filter Chips */}
      {resolutions.length > 0 && (
        <div className="flex items-center gap-1 flex-shrink-0 pl-1 border-l border-[#222222]">
          <Monitor size={11} className="text-[#555555] mr-0.5" />
          {resolutions.map((res) => {
            const isActive = selectedResolution === res
            return (
              <button
                key={res}
                onClick={() => setSelectedResolution(isActive ? null : res)}
                className={`px-2 py-0.5 rounded-none border transition-colors flex items-center gap-1 ${
                  isActive
                    ? 'bg-[#ff0055] text-[#ffffff] border-[#ff0055] font-semibold'
                    : 'bg-[#141414] text-[#aaaaaa] border-[#252525] hover:border-[#444444] hover:text-[#f0f0f0]'
                }`}
              >
                <span>{res}</span>
                {isActive && <X size={10} />}
              </button>
            )
          })}
        </div>
      )}

      {/* Lens Filter Chips */}
      {lenses.length > 0 && (
        <div className="flex items-center gap-1 flex-shrink-0 pl-1 border-l border-[#222222]">
          <Aperture size={11} className="text-[#555555] mr-0.5" />
          {lenses.slice(0, 3).map((lens) => {
            const isActive = selectedLens === lens
            return (
              <button
                key={lens}
                onClick={() => setSelectedLens(isActive ? null : lens)}
                className={`px-2 py-0.5 rounded-none border transition-colors flex items-center gap-1 ${
                  isActive
                    ? 'bg-[#ffffff] text-[#000000] border-[#ffffff] font-semibold'
                    : 'bg-[#141414] text-[#aaaaaa] border-[#252525] hover:border-[#444444] hover:text-[#f0f0f0]'
                }`}
              >
                <span>{lens}</span>
                {isActive && <X size={10} />}
              </button>
            )
          })}
        </div>
      )}

      {/* Clear All Workstation Filters */}
      {hasActiveFilter && (
        <button
          onClick={resetWorkstationFilters}
          className="ml-auto flex items-center gap-1 text-[#ff4444] hover:text-[#ff7777] border border-[#ff4444]/30 hover:border-[#ff4444] px-2 py-0.5 text-[10px] flex-shrink-0 transition-colors"
          title="Reset all media workstation filters"
        >
          <RotateCcw size={10} />
          <span>Reset Filters</span>
        </button>
      )}
    </div>
  )
}

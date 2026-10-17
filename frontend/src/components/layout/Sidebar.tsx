import React from 'react'
import { 
  Clock, 
  Image as ImageIcon, 
  Video, 
  FileText, 
  Archive, 
  Folder, 
  Trash2, 
  Tag as TagIcon, 
  Plus, 
  Database,
  Lock,
  Crop,
  Star,
  Music
} from 'lucide-react'
import { useVaultStore } from '../../store/useVaultStore'

export const Sidebar: React.FC = () => {
  const { 
    activeTab, 
    setActiveTab, 
    albums, 
    files, 
    catalogFiles,
    trashCount,
    selectedAlbumId, 
    setSelectedAlbumId,
    selectedTag,
    setSelectedTag,
    setCreateAlbumModalOpen,
    starredIds
  } = useVaultStore()

  // Use catalogFiles for stable, accurate vault-wide counts that don't fluctuate on active tab
  const catalog = catalogFiles.length > 0 ? catalogFiles : files.filter((f) => !f.is_deleted)
  const allTags = Array.from(new Set(catalog.flatMap((f) => f.tags)))

  const isScreenshotFile = (f: any): boolean => {
    if (f.file_category === 'screenshot' || f.is_screenshot) return true
    const name = (f.filename || '').toLowerCase()
    return (
      name.includes('screenshot') ||
      name.includes('screen_shot') ||
      name.includes('screen shot') ||
      name.includes('screencap') ||
      name.includes('capture') ||
      name.includes('scrn') ||
      (f.tags && f.tags.some((t: string) => t.toLowerCase() === 'screenshot' || t.toLowerCase() === 'screen'))
    )
  }

  const isAudioFile = (f: any): boolean => {
    return (
      f.file_category === 'audio' ||
      (f.mime_type && f.mime_type.startsWith('audio/')) ||
      /\.(mp3|wav|flac|ogg|m4a|aac)$/i.test(f.filename || '')
    )
  }

  const navItemClass = (isActive: boolean) => `
    flex items-center justify-between px-3 py-2 text-xs font-mono select-none cursor-pointer border-l-2 transition-colors
    ${isActive 
      ? 'border-[#ffffff] bg-[#141414] text-[#ffffff] font-semibold' 
      : 'border-transparent text-[#999999] hover:bg-[#0f0f0f] hover:text-[#cccccc]'}
  `

  return (
    <aside className="w-60 bg-[#080808] border-r border-[#282828] flex flex-col justify-between h-[calc(100vh-3.5rem)] flex-shrink-0 select-none">
      {/* Top Nav Tree */}
      <div className="overflow-y-auto py-3 space-y-6">
        {/* Google Photos Section */}
        <div>
          <div className="px-3 pb-1 text-[10px] font-mono uppercase tracking-widest text-[#555555]">
            Photos & Media
          </div>
          <div className="space-y-0.5">
            <div 
              onClick={() => setActiveTab('all')}
              className={navItemClass(activeTab === 'all' && !selectedAlbumId && !selectedTag)}
            >
              <div className="flex items-center gap-2.5">
                <Clock size={14} />
                <span>Timeline</span>
              </div>
              <span className="text-[10px] text-[#555555]">
                {catalog.length}
              </span>
            </div>

            <div 
              onClick={() => setActiveTab('images')}
              className={navItemClass(activeTab === 'images')}
            >
              <div className="flex items-center gap-2.5">
                <ImageIcon size={14} />
                <span>Photos</span>
              </div>
              <span className="text-[10px] text-[#555555]">
                {catalog.filter(f => f.file_category === 'image' && !isScreenshotFile(f)).length}
              </span>
            </div>

            <div 
              onClick={() => setActiveTab('screenshots')}
              className={navItemClass(activeTab === 'screenshots')}
            >
              <div className="flex items-center gap-2.5">
                <Crop size={14} />
                <span>Screenshots</span>
              </div>
              <span className="text-[10px] text-[#555555]">
                {catalog.filter(f => isScreenshotFile(f)).length}
              </span>
            </div>

            <div 
              onClick={() => setActiveTab('videos')}
              className={navItemClass(activeTab === 'videos')}
            >
              <div className="flex items-center gap-2.5">
                <Video size={14} />
                <span>Videos</span>
              </div>
              <span className="text-[10px] text-[#555555]">
                {catalog.filter(f => f.file_category === 'video').length}
              </span>
            </div>

            <div 
              onClick={() => setActiveTab('starred')}
              className={navItemClass(activeTab === 'starred')}
            >
              <div className="flex items-center gap-2.5">
                <Star size={14} />
                <span>Starred</span>
              </div>
              <span className="text-[10px] text-[#555555]">
                {catalog.filter(f => starredIds.includes(f.id)).length}
              </span>
            </div>
          </div>
        </div>

        {/* Google Drive Section */}
        <div>
          <div className="px-3 pb-1 text-[10px] font-mono uppercase tracking-widest text-[#555555]">
            Drive & Files
          </div>
          <div className="space-y-0.5">
            <div 
              onClick={() => { setActiveTab('folders'); setSelectedAlbumId(null); }}
              className={navItemClass((activeTab === 'folders' || activeTab === 'albums') && !selectedAlbumId)}
            >
              <div className="flex items-center gap-2.5">
                <Folder size={14} />
                <span>Folders</span>
              </div>
              <span className="text-[10px] text-[#555555]">
                {albums.length}
              </span>
            </div>

            <div 
              onClick={() => setActiveTab('documents')}
              className={navItemClass(activeTab === 'documents')}
            >
              <div className="flex items-center gap-2.5">
                <FileText size={14} />
                <span>Documents & Code</span>
              </div>
              <span className="text-[10px] text-[#555555]">
                {catalog.filter(f => f.file_category === 'document').length}
              </span>
            </div>

            <div 
              onClick={() => setActiveTab('audio')}
              className={navItemClass(activeTab === 'audio')}
            >
              <div className="flex items-center gap-2.5">
                <Music size={14} />
                <span>Audio Tracks</span>
              </div>
              <span className="text-[10px] text-[#555555]">
                {catalog.filter(f => isAudioFile(f)).length}
              </span>
            </div>

            <div 
              onClick={() => setActiveTab('archives')}
              className={navItemClass(activeTab === 'archives')}
            >
              <div className="flex items-center gap-2.5">
                <Archive size={14} />
                <span>Archives & Split</span>
              </div>
              <span className="text-[10px] text-[#555555]">
                {catalog.filter(f => f.file_category === 'archive' || f.is_split).length}
              </span>
            </div>
          </div>
        </div>

        {/* Folders List Section */}
        <div>
          <div className="px-3 pb-1 flex items-center justify-between text-[10px] font-mono uppercase tracking-widest text-[#555555]">
            <span 
              onClick={() => { setActiveTab('folders'); setSelectedAlbumId(null); }}
              className="cursor-pointer hover:text-[#ffffff] transition-colors"
              title="View All Folders"
            >
              Folders
            </span>
            <button 
              onClick={() => setCreateAlbumModalOpen(true)}
              className="text-[#888888] hover:text-[#ffffff] transition-colors p-0.5"
              title="Create Folder"
            >
              <Plus size={12} />
            </button>
          </div>
          <div className="space-y-0.5">
            {albums.map((folder) => (
              <div
                key={folder.id}
                onClick={() => setSelectedAlbumId(folder.id)}
                className={navItemClass(selectedAlbumId === folder.id)}
              >
                <div className="flex items-center gap-2.5 truncate">
                  <Folder size={14} />
                  <span className="truncate">{folder.name}</span>
                </div>
                <span className="text-[10px] text-[#555555]">{folder.file_count}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Tags Section */}
        {allTags.length > 0 && (
          <div>
            <div className="px-3 pb-1 text-[10px] font-mono uppercase tracking-widest text-[#555555]">
              CLIP Labels & Tags
            </div>
            <div className="px-3 flex flex-wrap gap-1 mt-1">
              {allTags.slice(0, 12).map((tag) => (
                <button
                  key={tag}
                  onClick={() => setSelectedTag(selectedTag === tag ? null : tag)}
                  className={`text-[10px] font-mono px-2 py-0.5 border uppercase transition-colors ${
                    selectedTag === tag
                      ? 'bg-[#ffffff] text-[#000000] border-[#ffffff]'
                      : 'bg-[#111111] text-[#888888] border-[#222222] hover:border-[#444444] hover:text-[#e5e5e5]'
                  }`}
                >
                  #{tag}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Retention & System */}
        <div>
          <div className="px-3 pb-1 text-[10px] font-mono uppercase tracking-widest text-[#555555]">
            System
          </div>
          <div className="space-y-0.5">
            <div
              onClick={() => setActiveTab('trash')}
              className={navItemClass(activeTab === 'trash')}
            >
              <div className="flex items-center gap-2.5">
                <Trash2 size={14} className={trashCount > 0 ? 'text-[#ef4444]' : ''} />
                <span>Trash</span>
              </div>
              {trashCount > 0 && (
                <span className="text-[10px] font-mono text-[#ef4444] bg-[#220808] px-1.5 py-0.2 border border-[#441111]">
                  {trashCount}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Footer Info Box */}
      <div className="p-3 border-t border-[#222222] bg-[#0c0c0c] font-mono text-[11px] text-[#666666]">
        <div className="flex items-center justify-between pb-1">
          <span className="flex items-center gap-1.5 text-[#888888]">
            <Database size={11} /> Channel WAL
          </span>
          <span className="text-[#22c55e]">SYNCED</span>
        </div>
        <div className="w-full bg-[#1c1c1c] h-1 my-1.5">
          <div className="bg-[#ffffff] h-1 w-2/5" />
        </div>
        <div className="flex justify-between text-[10px] text-[#444444]">
          <span>Part Cap: 1.9 GB</span>
          <span>Zero-Cost</span>
        </div>
      </div>
    </aside>
  )
}

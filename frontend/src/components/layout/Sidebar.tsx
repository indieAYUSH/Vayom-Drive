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
  Share2
} from 'lucide-react'
import { useVaultStore } from '../../store/useVaultStore'

export const Sidebar: React.FC = () => {
  const { 
    activeTab, 
    setActiveTab, 
    albums, 
    files, 
    selectedAlbumId, 
    setSelectedAlbumId,
    selectedTag,
    setSelectedTag,
    setCreateAlbumModalOpen
  } = useVaultStore()

  const trashCount = files.filter((f) => f.is_deleted).length
  const allTags = Array.from(new Set(files.flatMap((f) => f.tags)))

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
        {/* Core Timeline & Categories */}
        <div>
          <div className="px-3 pb-1 text-[10px] font-mono uppercase tracking-widest text-[#555555]">
            Library
          </div>
          <div className="space-y-0.5">
            <div 
              onClick={() => setActiveTab('all')}
              className={navItemClass(activeTab === 'all' && !selectedAlbumId && !selectedTag)}
            >
              <div className="flex items-center gap-2.5">
                <Clock size={14} />
                <span>All Media</span>
              </div>
              <span className="text-[10px] text-[#555555]">
                {files.filter(f => !f.is_deleted).length}
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
                {files.filter(f => !f.is_deleted && f.file_category === 'image').length}
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
                {files.filter(f => !f.is_deleted && f.file_category === 'video').length}
              </span>
            </div>

            <div 
              onClick={() => setActiveTab('documents')}
              className={navItemClass(activeTab === 'documents')}
            >
              <div className="flex items-center gap-2.5">
                <FileText size={14} />
                <span>Documents</span>
              </div>
              <span className="text-[10px] text-[#555555]">
                {files.filter(f => !f.is_deleted && f.file_category === 'document').length}
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
                {files.filter(f => !f.is_deleted && (f.file_category === 'archive' || f.is_split)).length}
              </span>
            </div>
          </div>
        </div>

        {/* Albums Section */}
        <div>
          <div className="px-3 pb-1 flex items-center justify-between text-[10px] font-mono uppercase tracking-widest text-[#555555]">
            <span>Albums</span>
            <button 
              onClick={() => setCreateAlbumModalOpen(true)}
              className="text-[#888888] hover:text-[#ffffff] transition-colors p-0.5"
              title="Create Album"
            >
              <Plus size={12} />
            </button>
          </div>
          <div className="space-y-0.5">
            {albums.map((album) => (
              <div
                key={album.id}
                onClick={() => setSelectedAlbumId(album.id)}
                className={navItemClass(selectedAlbumId === album.id)}
              >
                <div className="flex items-center gap-2.5 truncate">
                  <Folder size={14} />
                  <span className="truncate">{album.name}</span>
                </div>
                <span className="text-[10px] text-[#555555]">{album.file_count}</span>
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

        {/* Retention & Trash */}
        <div>
          <div className="px-3 pb-1 text-[10px] font-mono uppercase tracking-widest text-[#555555]">
            System
          </div>
          <div className="space-y-0.5">
            <div
              onClick={() => setActiveTab('shares')}
              className={navItemClass(activeTab === 'shares')}
            >
              <div className="flex items-center gap-2.5">
                <Share2 size={14} />
                <span>Expiring Shares</span>
              </div>
            </div>

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

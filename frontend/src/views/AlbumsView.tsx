import React from 'react'
import { useVaultStore } from '../store/useVaultStore'
import { Folder, Plus } from 'lucide-react'
import { Button } from '../components/common/Button'

export const AlbumsView: React.FC = () => {
  const { albums, setSelectedAlbumId, setCreateAlbumModalOpen } = useVaultStore()

  return (
    <div className="flex-1 overflow-y-auto p-6 bg-[#000000] select-none font-mono">
      {/* Top Header */}
      <div className="flex items-center justify-between pb-6 border-b border-[#282828] mb-6">
        <div>
          <h1 className="text-base font-bold uppercase tracking-wider text-[#ffffff]">
            Albums & Collections
          </h1>
          <p className="text-xs text-[#666666] mt-1">
            Group related photos, RAW captures, and archives into curated folders
          </p>
        </div>

        <Button
          variant="primary"
          size="sm"
          onClick={() => setCreateAlbumModalOpen(true)}
          icon={<Plus size={13} />}
        >
          New Album
        </Button>
      </div>

      {/* Album Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {albums.map((album) => (
          <div
            key={album.id}
            onClick={() => setSelectedAlbumId(album.id)}
            className="group bg-[#0c0c0c] border border-[#222222] hover:border-[#ffffff] cursor-pointer transition-colors flex flex-col"
          >
            {/* Cover Image */}
            <div className="w-full h-44 bg-[#141414] overflow-hidden relative">
              {album.cover_url ? (
                <img
                  src={album.cover_url}
                  alt={album.name}
                  className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-[#444444]">
                  <Folder size={48} strokeWidth={1} />
                </div>
              )}
              <div className="absolute top-2 right-2 bg-[#000000] px-2 py-0.5 border border-[#282828] text-[10px] text-[#ffffff]">
                {album.file_count} items
              </div>
            </div>

            {/* Info */}
            <div className="p-3 bg-[#0a0a0a] border-t border-[#1c1c1c] flex flex-col gap-1">
              <h3 className="font-semibold text-xs text-[#f0f0f0] truncate group-hover:text-[#ffffff]">
                {album.name}
              </h3>
              {album.description && (
                <p className="text-[11px] text-[#666666] line-clamp-2">
                  {album.description}
                </p>
              )}
              <span className="text-[9px] text-[#444444] mt-1">
                Created: {new Date(album.created_at).toLocaleDateString()}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

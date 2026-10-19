import React from 'react'
import { Clock, Image as ImageIcon, Folder, Crop, Star } from 'lucide-react'
import { useVaultStore } from '../../store/useVaultStore'

export const MobileNav: React.FC = () => {
  const { activeTab, setActiveTab, setSelectedAlbumId } = useVaultStore()

  const navItemClass = (isActive: boolean) => `
    flex-1 flex flex-col items-center justify-center py-2 text-[11px] font-medium select-none cursor-pointer transition-colors
    ${isActive ? 'text-[#ffffff] bg-[#141414]' : 'text-[#888888] hover:text-[#ffffff]'}
  `

  return (
    <nav className="md:hidden fixed bottom-0 left-0 right-0 h-[calc(3.5rem+env(safe-area-inset-bottom))] pb-[env(safe-area-inset-bottom)] bg-[#080808] border-t border-[#282828] flex items-center justify-around z-40 select-none">
      <div 
        onClick={() => { setActiveTab('all'); setSelectedAlbumId(null); }} 
        className={navItemClass(activeTab === 'all')}
        title="All Media Timeline"
      >
        <Clock size={16} />
        <span className="mt-1">Timeline</span>
      </div>

      <div 
        onClick={() => { setActiveTab('images'); setSelectedAlbumId(null); }} 
        className={navItemClass(activeTab === 'images')}
        title="Photos"
      >
        <ImageIcon size={16} />
        <span className="mt-1">Photos</span>
      </div>

      <div 
        onClick={() => { setActiveTab('screenshots'); setSelectedAlbumId(null); }} 
        className={navItemClass(activeTab === 'screenshots')}
        title="Screenshots"
      >
        <Crop size={16} />
        <span className="mt-1">Screenshots</span>
      </div>

      <div 
        onClick={() => { setActiveTab('folders'); setSelectedAlbumId(null); }} 
        className={navItemClass(activeTab === 'folders' || activeTab === 'albums')}
        title="Drive Folders"
      >
        <Folder size={16} />
        <span className="mt-1">Drive</span>
      </div>

      <div 
        onClick={() => { setActiveTab('starred'); setSelectedAlbumId(null); }} 
        className={navItemClass(activeTab === 'starred')}
        title="Starred & Favorites"
      >
        <Star size={16} />
        <span className="mt-1">Starred</span>
      </div>
    </nav>
  )
}

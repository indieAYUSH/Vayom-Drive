import React from 'react'
import { Clock, Image as ImageIcon, Folder, Trash2, Settings } from 'lucide-react'
import { useVaultStore } from '../../store/useVaultStore'

export const MobileNav: React.FC = () => {
  const { activeTab, setActiveTab } = useVaultStore()

  const navItemClass = (isActive: boolean) => `
    flex-1 flex flex-col items-center justify-center py-2 text-[10px] font-mono select-none cursor-pointer transition-colors
    ${isActive ? 'text-[#ffffff] bg-[#141414]' : 'text-[#777777] hover:text-[#cccccc]'}
  `

  return (
    <nav className="md:hidden fixed bottom-0 left-0 right-0 h-14 bg-[#080808] border-t border-[#282828] flex items-center justify-around z-40 select-none">
      <div 
        onClick={() => setActiveTab('all')} 
        className={navItemClass(activeTab === 'all')}
      >
        <Clock size={16} />
        <span className="mt-1">All</span>
      </div>

      <div 
        onClick={() => setActiveTab('images')} 
        className={navItemClass(activeTab === 'images')}
      >
        <ImageIcon size={16} />
        <span className="mt-1">Photos</span>
      </div>

      <div 
        onClick={() => setActiveTab('albums')} 
        className={navItemClass(activeTab === 'albums')}
      >
        <Folder size={16} />
        <span className="mt-1">Albums</span>
      </div>

      <div 
        onClick={() => setActiveTab('trash')} 
        className={navItemClass(activeTab === 'trash')}
      >
        <Trash2 size={16} />
        <span className="mt-1">Trash</span>
      </div>

      <div 
        onClick={() => setActiveTab('settings')} 
        className={navItemClass(activeTab === 'settings')}
      >
        <Settings size={16} />
        <span className="mt-1">Config</span>
      </div>
    </nav>
  )
}

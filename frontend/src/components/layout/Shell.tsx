import React, { useEffect } from 'react'
import { Header } from './Header'
import { Sidebar } from './Sidebar'
import { MobileNav } from './MobileNav'
import { DropZoneOverlay } from '../upload/DropZoneOverlay'
import { UploadTray } from '../upload/UploadTray'
import { CommandBar } from '../search/CommandBar'
import { BulkActionBar } from '../actions/BulkActionBar'
import { LightboxModal } from '../lightbox/LightboxModal'
import { CreateAlbumModal } from '../modals/CreateAlbumModal'
import { GalleryView } from '../../views/GalleryView'
import { AlbumsView } from '../../views/AlbumsView'
import { TrashView } from '../../views/TrashView'
import { SettingsView } from '../../views/SettingsView'
import { useVaultStore } from '../../store/useVaultStore'

import { AudioPlayerBar } from '../drive/AudioPlayerBar'

export const Shell: React.FC = () => {
  const { 
    activeTab, 
    selectedAlbumId,
    lightboxItem, 
    closeLightbox, 
    nextLightbox, 
    prevLightbox,
    moveToTrash,
    init
  } = useVaultStore()

  useEffect(() => {
    init()
  }, [init])

  const renderActiveView = () => {
    switch (activeTab) {
      case 'folders':
      case 'albums':
        return selectedAlbumId ? <GalleryView /> : <AlbumsView />
      case 'trash':
        return <TrashView />
      case 'settings':
        return <SettingsView />
      case 'starred':
      case 'audio':
      case 'screenshots':
      case 'all':
      case 'images':
      case 'videos':
      case 'documents':
      case 'archives':
      default:
        return <GalleryView />
    }
  }

  return (
    <div className="flex flex-col h-screen w-screen bg-[#000000] text-[#f0f0f0] overflow-hidden select-none">
      {/* Top Application Header */}
      <Header />

      {/* Main Workspace: Sidebar + Dynamic View */}
      <div className="flex flex-1 overflow-hidden relative">
        {/* Desktop Sidebar (hidden on mobile) */}
        <div className="hidden md:block">
          <Sidebar />
        </div>

        {/* View Router Canvas */}
        <main className="flex-1 flex flex-col h-full overflow-hidden bg-[#000000] pb-[calc(3.5rem+env(safe-area-inset-bottom))] md:pb-0">
          {renderActiveView()}
        </main>
      </div>

      {/* Mobile Bottom Navigation */}
      <MobileNav />

      {/* Floating System Widgets & Modals */}
      <DropZoneOverlay />
      <UploadTray />
      <CommandBar />
      <BulkActionBar />
      <CreateAlbumModal />

      {/* Fullscreen Lightbox / Video Player */}
      <LightboxModal
        item={lightboxItem}
        onClose={closeLightbox}
        onNext={nextLightbox}
        onPrev={prevLightbox}
        onMoveToTrash={(id) => moveToTrash([id])}
      />

      {/* Floating Audio Player Bar */}
      <AudioPlayerBar />
    </div>
  )
}

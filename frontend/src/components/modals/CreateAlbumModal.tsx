import React, { useState, useEffect, useRef } from 'react'
import { Modal } from '../common/Modal'
import { Button } from '../common/Button'
import { Input } from '../common/Input'
import { useVaultStore } from '../../store/useVaultStore'
import { FolderPlus, FolderCheck } from 'lucide-react'
import { translateErrorMessage } from '../../utils/errorTranslation'

export const CreateAlbumModal: React.FC = () => {
  const { 
    isCreateAlbumModalOpen, 
    setCreateAlbumModalOpen, 
    createAlbum, 
    addFilesToAlbum,
    selectedIds,
    albums 
  } = useVaultStore()

  const hasSelected = selectedIds.length > 0
  const [mode, setMode] = useState<'existing' | 'create'>('create')
  const [selectedAlbumId, setSelectedAlbumId] = useState<string>('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const wasOpenRef = useRef(false)
  useEffect(() => {
    const justOpened = isCreateAlbumModalOpen && !wasOpenRef.current
    wasOpenRef.current = isCreateAlbumModalOpen
    // H-40: reset the form only on the open transition — not when `albums`/`hasSelected` change
    if (justOpened) {
      if (hasSelected && albums.length > 0) {
        setMode('existing')
        setSelectedAlbumId(albums[0].id)
      } else {
        setMode('create')
      }
      setName('')
      setDescription('')
    }
  }, [isCreateAlbumModalOpen, hasSelected, albums])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (isSubmitting) return

    setIsSubmitting(true)
    try {
      if (hasSelected && mode === 'existing') {
        if (!selectedAlbumId) throw new Error('Please select a folder')
        await addFilesToAlbum(selectedAlbumId, selectedIds)
      } else {
        if (!name.trim()) return
        const newAlbum = await createAlbum(name.trim(), description.trim() || undefined)
        if (hasSelected && newAlbum?.id) {
          await addFilesToAlbum(newAlbum.id, selectedIds)
        }
      }
      setName('')
      setDescription('')
      setCreateAlbumModalOpen(false)
    } catch (err: any) {
      alert(translateErrorMessage(err))
    } finally {
      setIsSubmitting(false)
    }
  }

  const title = hasSelected 
    ? `Add ${selectedIds.length} ${selectedIds.length === 1 ? 'item' : 'items'} to Folder`
    : 'Create New Folder'

  return (
    <Modal
      isOpen={isCreateAlbumModalOpen}
      onClose={() => setCreateAlbumModalOpen(false)}
      title={title}
      maxWidth="sm"
    >
      <form onSubmit={handleSubmit} className="space-y-4 text-xs">
        {hasSelected && albums.length > 0 && (
          <div className="flex border border-[#282828] bg-[#080808] p-0.5 mb-3">
            <button
              type="button"
              onClick={() => setMode('existing')}
              className={`flex-1 py-1 text-center font-medium transition-colors ${
                mode === 'existing'
                  ? 'bg-[#ffffff] text-[#000000]'
                  : 'text-[#888888] hover:text-[#ffffff]'
              }`}
            >
              Existing Folder
            </button>
            <button
              type="button"
              onClick={() => setMode('create')}
              className={`flex-1 py-1 text-center font-medium transition-colors ${
                mode === 'create'
                  ? 'bg-[#ffffff] text-[#000000]'
                  : 'text-[#888888] hover:text-[#ffffff]'
              }`}
            >
              New Folder
            </button>
          </div>
        )}

        {hasSelected && mode === 'existing' && albums.length > 0 ? (
          <div className="flex flex-col gap-1.5 w-full">
            <label className="text-xs font-medium text-[#cccccc]">
              Select Destination Folder
            </label>
            <select
              value={selectedAlbumId}
              onChange={(e) => setSelectedAlbumId(e.target.value)}
              className="w-full bg-[#080808] text-[#f0f0f0] border border-[#282828] focus:border-[#666666] focus:outline-none p-2 text-xs"
            >
              {albums.map((alb) => (
                <option key={alb.id} value={alb.id}>
                  {alb.name} ({alb.file_count || 0} items)
                </option>
              ))}
            </select>
          </div>
        ) : (
          <>
            <Input
              label="Folder Name"
              placeholder="e.g. Work Documents, Camera Captures, Projects"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              autoFocus
            />

            <div className="flex flex-col gap-1.5 w-full">
              <label className="text-xs font-medium text-[#cccccc]">
                Description (Optional)
              </label>
              <textarea
                rows={3}
                placeholder="Notes or tags for this folder..."
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="w-full bg-[#080808] text-[#f0f0f0] border border-[#282828] focus:border-[#666666] focus:outline-none p-2 text-xs placeholder:text-[#555555]"
              />
            </div>
          </>
        )}

        <div className="flex justify-end gap-2 pt-2 border-t border-[#222222]">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setCreateAlbumModalOpen(false)}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            size="sm"
            disabled={mode === 'create' ? !name.trim() || isSubmitting : !selectedAlbumId || isSubmitting}
            icon={mode === 'existing' ? <FolderCheck size={12} /> : <FolderPlus size={12} />}
          >
            {isSubmitting 
              ? 'Saving...' 
              : hasSelected 
                ? (mode === 'existing' ? 'Add to Folder' : 'Create & Add Items')
                : 'Create Folder'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

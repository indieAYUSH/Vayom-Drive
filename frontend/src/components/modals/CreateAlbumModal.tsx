import React, { useState } from 'react'
import { Modal } from '../common/Modal'
import { Button } from '../common/Button'
import { Input } from '../common/Input'
import { useVaultStore } from '../../store/useVaultStore'
import { FolderPlus } from 'lucide-react'

export const CreateAlbumModal: React.FC = () => {
  const { isCreateAlbumModalOpen, setCreateAlbumModalOpen, createAlbum } = useVaultStore()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || isSubmitting) return
    setIsSubmitting(true)
    try {
      await createAlbum(name.trim(), description.trim() || undefined)
      setName('')
      setDescription('')
    } catch (err: any) {
      alert(err.message || 'Failed to create album')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Modal
      isOpen={isCreateAlbumModalOpen}
      onClose={() => setCreateAlbumModalOpen(false)}
      title="Create New Folder"
      maxWidth="sm"
    >
      <form onSubmit={handleCreate} className="space-y-4 font-mono text-xs">
        <Input
          label="Folder Name"
          placeholder="e.g. Work Documents, Camera Captures, Projects"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          autoFocus
        />

        <div className="flex flex-col gap-1 w-full">
          <label className="text-[11px] text-[#999999] uppercase tracking-wider">
            Description (Optional)
          </label>
          <textarea
            rows={3}
            placeholder="Notes or tags for this folder..."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="w-full bg-[#080808] text-[#f0f0f0] border border-[#282828] focus:border-[#666666] focus:outline-none p-2 text-xs placeholder:text-[#444444]"
          />
        </div>

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
            disabled={!name.trim() || isSubmitting}
            icon={<FolderPlus size={12} />}
          >
            {isSubmitting ? 'Creating...' : 'Create Folder'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

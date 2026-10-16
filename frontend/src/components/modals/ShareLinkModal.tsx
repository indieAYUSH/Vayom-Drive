import React, { useState } from 'react'
import { Modal } from '../common/Modal'
import { Button } from '../common/Button'
import { Input } from '../common/Input'
import { Checkbox } from '../common/Checkbox'
import { useVaultStore } from '../../store/useVaultStore'
import { api } from '../../services/api'
import { Copy, Check, Link, Clock, Lock } from 'lucide-react'

export const ShareLinkModal: React.FC = () => {
  const { isShareModalOpen, closeShareModal, shareTargetFile, fetchShares } = useVaultStore()
  const [expiryHours, setExpiryHours] = useState(24)
  const [hasPassword, setHasPassword] = useState(false)
  const [password, setPassword] = useState('')
  const [isGenerating, setIsGenerating] = useState(false)
  const [createdUrl, setCreatedUrl] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  if (!shareTargetFile) return null

  const handleGenerate = async () => {
    setIsGenerating(true)
    try {
      const res = await api.createShare(
        shareTargetFile.id,
        expiryHours,
        hasPassword,
        hasPassword ? password : undefined
      )
      const url = `${window.location.origin}/api/v1/s/${res.token}`
      setCreatedUrl(url)
      await fetchShares()
    } catch (err: any) {
      alert(err.message || 'Failed to create share link')
    } finally {
      setIsGenerating(false)
    }
  }

  const handleCopy = () => {
    if (!createdUrl) return
    navigator.clipboard.writeText(createdUrl)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleClose = () => {
    setCreatedUrl(null)
    setPassword('')
    setHasPassword(false)
    closeShareModal()
  }

  return (
    <Modal
      isOpen={isShareModalOpen}
      onClose={handleClose}
      title="Create Expiring Share Link"
      maxWidth="md"
    >
      <div className="space-y-4 font-mono text-xs">
        <div className="p-2.5 bg-[#161616] border border-[#282828]">
          <div className="text-[10px] text-[#666666] uppercase">Target File</div>
          <div className="font-semibold text-[#f0f0f0] truncate">{shareTargetFile.filename}</div>
          <div className="text-[10px] text-[#888888]">
            {(shareTargetFile.size_bytes / (1024 * 1024)).toFixed(1)} MB
          </div>
        </div>

        {!createdUrl ? (
          <>
            <div>
              <label className="text-[10px] text-[#888888] uppercase block mb-1">
                Link Expiry Period
              </label>
              <div className="grid grid-cols-4 gap-1.5">
                {[1, 24, 168, 720].map((hours) => (
                  <button
                    key={hours}
                    onClick={() => setExpiryHours(hours)}
                    className={`py-1.5 text-center border uppercase transition-colors ${
                      expiryHours === hours
                        ? 'bg-[#ffffff] text-[#000000] border-[#ffffff] font-semibold'
                        : 'bg-[#141414] text-[#888888] border-[#282828] hover:border-[#555555]'
                    }`}
                  >
                    {hours === 1 ? '1 Hour' : hours === 24 ? '24 Hours' : hours === 168 ? '7 Days' : '30 Days'}
                  </button>
                ))}
              </div>
            </div>

            <div className="pt-2 border-t border-[#1f1f1f]">
              <Checkbox
                checked={hasPassword}
                onChange={setHasPassword}
                label="Require Passcode Access"
              />
              {hasPassword && (
                <div className="mt-2">
                  <Input
                    placeholder="Enter link password..."
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 pt-3 border-t border-[#222222]">
              <Button variant="ghost" size="sm" onClick={handleClose}>
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={handleGenerate}
                disabled={isGenerating}
                icon={<Link size={12} />}
              >
                {isGenerating ? 'Generating...' : 'Generate Secure Link'}
              </Button>
            </div>
          </>
        ) : (
          <div className="space-y-3 pt-1">
            <div className="p-3 bg-[#0a2412] border border-[#164d26] text-[#4ade80] text-xs">
              ✓ Share link active and expiring in {expiryHours} hours
            </div>

            <div>
              <label className="text-[10px] text-[#888888] uppercase block mb-1">
                Public Access URL
              </label>
              <div className="flex gap-1.5">
                <input
                  readOnly
                  value={createdUrl}
                  className="flex-1 bg-[#080808] border border-[#282828] px-2 py-1.5 text-xs text-[#f0f0f0]"
                />
                <Button variant="primary" size="sm" onClick={handleCopy} icon={copied ? <Check size={12} /> : <Copy size={12} />}>
                  {copied ? 'Copied' : 'Copy'}
                </Button>
              </div>
            </div>

            <div className="flex justify-end pt-2">
              <Button variant="secondary" size="sm" onClick={handleClose}>
                Done
              </Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}

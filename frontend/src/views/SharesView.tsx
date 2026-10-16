import React from 'react'
import { useVaultStore } from '../store/useVaultStore'
import { Share2, Clock, Lock, Copy, Check } from 'lucide-react'
import { Button } from '../components/common/Button'
import { Badge } from '../components/common/Badge'

export const SharesView: React.FC = () => {
  const { shares, revokeShareLink } = useVaultStore()
  const [copiedToken, setCopiedToken] = React.useState<string | null>(null)

  const copyUrl = (token: string) => {
    const url = `${window.location.origin}/s/${token}`
    navigator.clipboard.writeText(url)
    setCopiedToken(token)
    setTimeout(() => setCopiedToken(null), 1500)
  }

  const formatBytes = (bytes: number) => {
    if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 bg-[#000000] select-none font-mono">
      {/* Top Header */}
      <div className="flex items-center justify-between pb-6 border-b border-[#282828] mb-6">
        <div>
          <h1 className="text-base font-bold uppercase tracking-wider text-[#ffffff] flex items-center gap-2">
            <Share2 size={16} /> Expiring Share Links
          </h1>
          <p className="text-xs text-[#777777] mt-1">
            Active secure public download links with optional expiration and passwords
          </p>
        </div>
      </div>

      {shares.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-16 text-center text-[#555555]">
          <div className="w-12 h-12 border border-[#222222] flex items-center justify-center mb-3 text-[#444444]">
            Ø
          </div>
          <p className="text-sm uppercase tracking-wider text-[#888888]">No active share links</p>
          <p className="text-xs text-[#444444] mt-1">Generate a share link for any file from the Lightbox</p>
        </div>
      ) : (
        <div className="space-y-3">
          {shares.map((share) => (
            <div
              key={share.id}
              className={`p-4 bg-[#0c0c0c] border transition-colors text-xs flex flex-col sm:flex-row sm:items-center justify-between gap-3 ${
                share.is_revoked ? 'border-[#331111] opacity-50' : 'border-[#282828] hover:border-[#444444]'
              }`}
            >
              <div className="space-y-1.5 truncate max-w-md">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-[#f0f0f0] truncate">{share.filename}</span>
                  {share.has_password && (
                    <Badge variant="warning">
                      <Lock size={10} className="inline mr-1" /> Password
                    </Badge>
                  )}
                  {share.is_revoked ? (
                    <Badge variant="danger">Revoked</Badge>
                  ) : (
                    <Badge variant="success">Active</Badge>
                  )}
                </div>

                <div className="text-[11px] text-[#777777] flex flex-wrap items-center gap-3">
                  <span>Size: {formatBytes(share.file_size)}</span>
                  <span>•</span>
                  <span>Hits: {share.view_count}{share.max_views ? ` / ${share.max_views}` : ''}</span>
                  <span>•</span>
                  <span className="flex items-center gap-1">
                    <Clock size={11} /> Expires: {new Date(share.expires_at).toLocaleDateString()}
                  </span>
                </div>
              </div>

              {!share.is_revoked && (
                <div className="flex items-center gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => copyUrl(share.token)}
                    icon={copiedToken === share.token ? <Check size={12} className="text-[#22c55e]" /> : <Copy size={12} />}
                  >
                    {copiedToken === share.token ? 'Copied' : 'Copy Link'}
                  </Button>

                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => revokeShareLink(share.id)}
                  >
                    Revoke
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

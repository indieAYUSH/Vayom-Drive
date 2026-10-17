import React, { useState, useMemo } from 'react'
import { FileItem } from '../../types/vault'
import { Checkbox } from '../common/Checkbox'
import { Badge } from '../common/Badge'
import { useVaultStore } from '../../store/useVaultStore'
import { 
  Copy, 
  Check, 
  Eye, 
  Trash2, 
  ArrowUpDown, 
  ArrowUp, 
  ArrowDown, 
  Star, 
  Download, 
  Share2,
  Music,
  FileText,
  Film,
  Archive,
  Image
} from 'lucide-react'

interface TableViewProps {
  items: FileItem[]
  selectedIds: string[]
  onToggleSelect: (id: string) => void
  onOpenLightbox: (item: FileItem) => void
  onMoveToTrash: (ids: string[]) => void
}

type SortField = 'filename' | 'size_bytes' | 'created_at' | 'file_category'

export const TableView: React.FC<TableViewProps> = ({
  items,
  selectedIds,
  onToggleSelect,
  onOpenLightbox,
  onMoveToTrash,
}) => {
  const { starredIds, toggleStar, openShareModal, setActiveAudioItem } = useVaultStore()
  const [copiedSha, setCopiedSha] = useState<string | null>(null)
  const [sortField, setSortField] = useState<SortField>('created_at')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    setCopiedSha(text)
    setTimeout(() => setCopiedSha(null), 1500)
  }

  const formatBytes = (bytes: number) => {
    if (bytes >= 1024 * 1024 * 1024) {
      return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
    }
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
  }

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDir('asc')
    }
  }

  const sortedItems = useMemo(() => {
    return [...items].sort((a, b) => {
      let cmp = 0
      if (sortField === 'filename') {
        cmp = a.filename.localeCompare(b.filename)
      } else if (sortField === 'size_bytes') {
        cmp = a.size_bytes - b.size_bytes
      } else if (sortField === 'created_at') {
        const timeA = new Date(a.created_at).getTime() || 0
        const timeB = new Date(b.created_at).getTime() || 0
        cmp = timeA - timeB
      } else if (sortField === 'file_category') {
        cmp = a.file_category.localeCompare(b.file_category)
      }
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [items, sortField, sortDir])

  const renderSortIcon = (field: SortField) => {
    if (sortField !== field) return <ArrowUpDown size={11} className="text-[#444444]" />
    return sortDir === 'asc' ? <ArrowUp size={11} className="text-[#ffffff]" /> : <ArrowDown size={11} className="text-[#ffffff]" />
  }

  const renderFileIcon = (item: FileItem) => {
    const ext = (item.filename.split('.').pop() || '').toLowerCase()
    const isAudio = item.file_category === 'audio' || ['mp3', 'wav', 'flac', 'ogg', 'm4a'].includes(ext)
    if (item.file_category === 'image') return <Image size={13} className="text-[#888888] flex-shrink-0" />
    if (item.file_category === 'video') return <Film size={13} className="text-[#888888] flex-shrink-0" />
    if (isAudio) return <Music size={13} className="text-[#888888] flex-shrink-0" />
    if (item.file_category === 'archive') return <Archive size={13} className="text-[#888888] flex-shrink-0" />
    return <FileText size={13} className="text-[#888888] flex-shrink-0" />
  }

  return (
    <div className="flex-1 overflow-x-auto overflow-y-auto border-t border-[#1c1c1c] font-mono text-xs select-none">
      <table className="w-full min-w-[480px] text-left border-collapse">
        <thead>
          <tr className="bg-[#0c0c0c] border-b border-[#222222] text-[#666666] text-[10px] uppercase tracking-wider sticky top-0 z-10">
            <th className="py-2.5 px-3 w-8"></th>
            <th className="py-2.5 px-2 w-7"></th>
            <th 
              onClick={() => handleSort('filename')}
              className="py-2.5 px-3 cursor-pointer hover:text-[#f0f0f0] transition-colors"
            >
              <div className="flex items-center gap-1">
                <span>Filename</span>
                {renderSortIcon('filename')}
              </div>
            </th>
            <th 
              onClick={() => handleSort('file_category')}
              className="py-2.5 px-3 hidden sm:table-cell cursor-pointer hover:text-[#f0f0f0] transition-colors"
            >
              <div className="flex items-center gap-1">
                <span>Category</span>
                {renderSortIcon('file_category')}
              </div>
            </th>
            <th 
              onClick={() => handleSort('size_bytes')}
              className="py-2.5 px-3 cursor-pointer hover:text-[#f0f0f0] transition-colors"
            >
              <div className="flex items-center gap-1">
                <span>Size</span>
                {renderSortIcon('size_bytes')}
              </div>
            </th>
            <th className="py-2.5 px-3 hidden md:table-cell">Parts</th>
            <th className="py-2.5 px-3 hidden lg:table-cell">SHA-256</th>
            <th 
              onClick={() => handleSort('created_at')}
              className="py-2.5 px-3 hidden sm:table-cell cursor-pointer hover:text-[#f0f0f0] transition-colors"
            >
              <div className="flex items-center gap-1">
                <span>Date</span>
                {renderSortIcon('created_at')}
              </div>
            </th>
            <th className="py-2.5 px-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[#181818]">
          {sortedItems.map((item) => {
            const isSelected = selectedIds.includes(item.id)
            const isStarred = starredIds.includes(item.id)
            const ext = (item.filename.split('.').pop() || '').toLowerCase()
            const isAudio = item.file_category === 'audio' || ['mp3', 'wav', 'flac', 'ogg', 'm4a'].includes(ext)

            const handleRowClick = () => {
              if (isAudio) setActiveAudioItem(item)
              else onOpenLightbox(item)
            }

            return (
              <tr 
                key={item.id}
                className={`hover:bg-[#111111] transition-colors group ${
                  isSelected ? 'bg-[#141414]' : ''
                }`}
              >
                <td className="py-2 px-3">
                  <Checkbox
                    checked={isSelected}
                    onChange={() => onToggleSelect(item.id)}
                  />
                </td>
                <td className="py-2 px-1">
                  <button
                    onClick={() => toggleStar(item.id)}
                    className={`p-1 transition-colors ${
                      isStarred ? 'text-[#ffffff]' : 'text-[#444444] opacity-0 group-hover:opacity-100 hover:text-[#ffffff]'
                    }`}
                    title={isStarred ? 'Unstar' : 'Star file'}
                  >
                    <Star size={12} fill={isStarred ? 'white' : 'none'} />
                  </button>
                </td>
                <td className="py-2 px-3 font-medium text-[#f0f0f0]">
                  <div className="flex items-center gap-2">
                    {renderFileIcon(item)}
                    <span 
                      onClick={handleRowClick}
                      className="cursor-pointer hover:underline truncate max-w-[150px] sm:max-w-xs md:max-w-md block"
                      title={item.filename}
                    >
                      {item.filename}
                    </span>
                  </div>
                </td>
                <td className="py-2 px-3 hidden sm:table-cell">
                  <Badge variant="default">{item.file_category}</Badge>
                </td>
                <td className="py-2 px-3 text-[#aaaaaa]">
                  {formatBytes(item.size_bytes)}
                </td>
                <td className="py-2 px-3 hidden md:table-cell">
                  {item.is_split ? (
                    <Badge variant="warning">{item.total_parts} Parts</Badge>
                  ) : (
                    <span className="text-[#666666]">1 Msg</span>
                  )}
                </td>
                <td className="py-2 px-3 text-[#777777] font-mono text-[11px] hidden lg:table-cell">
                  <button
                    onClick={() => copyToClipboard(item.sha256)}
                    className="flex items-center gap-1.5 hover:text-[#ffffff] transition-colors"
                    title="Click to copy full sha256"
                  >
                    <span>{item.sha256.substring(0, 10)}...</span>
                    {copiedSha === item.sha256 ? (
                      <Check size={11} className="text-[#22c55e]" />
                    ) : (
                      <Copy size={11} className="text-[#555555]" />
                    )}
                  </button>
                </td>
                <td className="py-2 px-3 text-[#777777] text-[11px] hidden sm:table-cell">
                  {new Date(item.created_at).toLocaleDateString()}
                </td>
                <td className="py-2 px-3 text-right">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={handleRowClick}
                      className="p-1 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
                      title="Preview"
                    >
                      <Eye size={13} />
                    </button>
                    <a
                      href={item.preview_url || `/api/v1/files/${item.id}/download`}
                      download={item.filename}
                      className="p-1 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
                      title="Download"
                    >
                      <Download size={13} />
                    </a>
                    <button
                      onClick={() => openShareModal(item)}
                      className="p-1 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333] hidden sm:inline-block"
                      title="Share Link"
                    >
                      <Share2 size={13} />
                    </button>
                    <button
                      onClick={() => onMoveToTrash([item.id])}
                      className="p-1 text-[#888888] hover:text-[#ef4444] border border-transparent hover:border-[#441111]"
                      title="Trash"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

import React from 'react'
import { FileItem } from '../../types/vault'
import { Checkbox } from '../common/Checkbox'
import { Badge } from '../common/Badge'
import { Copy, Check, Eye, Trash2 } from 'lucide-react'

interface TableViewProps {
  items: FileItem[]
  selectedIds: string[]
  onToggleSelect: (id: string) => void
  onOpenLightbox: (item: FileItem) => void
  onMoveToTrash: (ids: string[]) => void
}

export const TableView: React.FC<TableViewProps> = ({
  items,
  selectedIds,
  onToggleSelect,
  onOpenLightbox,
  onMoveToTrash
}) => {
  const [copiedSha, setCopiedSha] = React.useState<string | null>(null)

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

  return (
    <div className="flex-1 overflow-x-auto overflow-y-auto border-t border-[#1c1c1c] font-mono text-xs select-none">
      <table className="w-full min-w-[340px] text-left border-collapse">
        <thead>
          <tr className="bg-[#0c0c0c] border-b border-[#222222] text-[#666666] text-[10px] uppercase tracking-wider sticky top-0 z-10">
            <th className="py-2.5 px-3 w-8"></th>
            <th className="py-2.5 px-3">Filename</th>
            <th className="py-2.5 px-3 hidden sm:table-cell">Category</th>
            <th className="py-2.5 px-3">Size</th>
            <th className="py-2.5 px-3 hidden md:table-cell">TG Parts</th>
            <th className="py-2.5 px-3 hidden lg:table-cell">SHA-256 Checksum</th>
            <th className="py-2.5 px-3 hidden sm:table-cell">Date</th>
            <th className="py-2.5 px-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[#181818]">
          {items.map((item) => {
            const isSelected = selectedIds.includes(item.id)
            return (
              <tr 
                key={item.id}
                className={`hover:bg-[#111111] transition-colors ${
                  isSelected ? 'bg-[#141414]' : ''
                }`}
              >
                <td className="py-2 px-3">
                  <Checkbox
                    checked={isSelected}
                    onChange={() => onToggleSelect(item.id)}
                  />
                </td>
                <td className="py-2 px-3 font-medium text-[#f0f0f0]">
                  <span 
                    onClick={() => onOpenLightbox(item)}
                    className="cursor-pointer hover:underline truncate max-w-[160px] sm:max-w-xs block"
                  >
                    {item.filename}
                  </span>
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
                    <span>{item.sha256.substring(0, 12)}...</span>
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
                      onClick={() => onOpenLightbox(item)}
                      className="p-1 text-[#888888] hover:text-[#ffffff] border border-transparent hover:border-[#333333]"
                      title="View"
                    >
                      <Eye size={13} />
                    </button>
                    <button
                      onClick={() => onMoveToTrash([item.id])}
                      className="p-1 text-[#888888] hover:text-[#ef4444] border border-transparent hover:border-[#441111]"
                      title="Delete"
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

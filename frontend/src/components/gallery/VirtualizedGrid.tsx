import React, { useRef, useState, useEffect, useMemo } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { FileItem } from '../../types/vault'
import { MediaCard } from './MediaCard'
import { useVaultStore } from '../../store/useVaultStore'
import { CheckSquare, Square } from 'lucide-react'

interface VirtualizedGridProps {
  items: FileItem[]
  selectedIds: string[]
  onToggleSelect: (id: string) => void
  onOpenLightbox: (item: FileItem) => void
  groupByDate?: boolean
}

type VirtualGridRow =
  | { type: 'header'; dateKey: string; title: string; count: number; ids: string[] }
  | { type: 'row'; items: FileItem[]; key: string }

function formatHeaderDate(dateStr?: string): { key: string; title: string } {
  if (!dateStr) return { key: 'unknown', title: 'Undated' }
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return { key: 'unknown', title: 'Undated' }

  const now = new Date()
  const todayKey = `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`
  const fileKey = `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`

  const yesterday = new Date()
  yesterday.setDate(now.getDate() - 1)
  const yesterdayKey = `${yesterday.getFullYear()}-${yesterday.getMonth() + 1}-${yesterday.getDate()}`

  if (fileKey === todayKey) {
    return { key: fileKey, title: 'Today' }
  } else if (fileKey === yesterdayKey) {
    return { key: fileKey, title: 'Yesterday' }
  } else if (d.getFullYear() === now.getFullYear()) {
    const formatted = d.toLocaleDateString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
    })
    return { key: fileKey, title: formatted }
  } else {
    const formatted = d.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
    return { key: fileKey, title: formatted }
  }
}

export const VirtualizedGrid: React.FC<VirtualizedGridProps> = ({
  items,
  selectedIds,
  onToggleSelect,
  onOpenLightbox,
  groupByDate = true,
}) => {
  const parentRef = useRef<HTMLDivElement>(null)
  const [columns, setColumns] = useState(4)
  const { selectIds, deselectIds } = useVaultStore()

  // Dynamic responsive column adjustment based on container width
  useEffect(() => {
    const updateColumns = () => {
      if (!parentRef.current) return
      const width = parentRef.current.clientWidth
      if (width < 500) setColumns(2)
      else if (width < 768) setColumns(3)
      else if (width < 1100) setColumns(4)
      else if (width < 1400) setColumns(5)
      else if (width < 1800) setColumns(6)
      else setColumns(8)
    }

    updateColumns()
    window.addEventListener('resize', updateColumns)
    return () => window.removeEventListener('resize', updateColumns)
  }, [])

  // Partition items into virtual rows (either grouped by date or flat)
  const virtualRows = useMemo(() => {
    if (!groupByDate) {
      const result: VirtualGridRow[] = []
      for (let i = 0; i < items.length; i += columns) {
        result.push({
          type: 'row',
          items: items.slice(i, i + columns),
          key: `row-${i}`,
        })
      }
      return result
    }

    // Sort items by date descending
    const sorted = [...items].sort((a, b) => {
      const dateA = new Date(a.taken_at || a.created_at).getTime() || 0
      const dateB = new Date(b.taken_at || b.created_at).getTime() || 0
      return dateB - dateA
    })

    // Group items by date
    const groups: { key: string; title: string; items: FileItem[] }[] = []
    let currentGroup: { key: string; title: string; items: FileItem[] } | null = null

    for (const item of sorted) {
      const { key, title } = formatHeaderDate(item.taken_at || item.created_at)
      if (!currentGroup || currentGroup.key !== key) {
        currentGroup = { key, title, items: [item] }
        groups.push(currentGroup)
      } else {
        currentGroup.items.push(item)
      }
    }

    const result: VirtualGridRow[] = []
    for (const group of groups) {
      result.push({
        type: 'header',
        dateKey: group.key,
        title: group.title,
        count: group.items.length,
        ids: group.items.map((i) => i.id),
      })
      for (let i = 0; i < group.items.length; i += columns) {
        result.push({
          type: 'row',
          items: group.items.slice(i, i + columns),
          key: `${group.key}-row-${i}`,
        })
      }
    }

    return result
  }, [items, columns, groupByDate])

  const rowVirtualizer = useVirtualizer({
    count: virtualRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: React.useCallback(
      (index: number) => {
        const row = virtualRows[index]
        if (row && row.type === 'header') return 44
        const containerWidth = parentRef.current ? parentRef.current.clientWidth - 32 : 800
        const colWidth = containerWidth / Math.max(1, columns)
        return Math.round(colWidth + 70) // aspect-square card + metadata footer
      },
      [columns, virtualRows]
    ),
    overscan: 3,
  })

  // Invalidate measurement cache when column count or grouping changes
  useEffect(() => {
    rowVirtualizer.measure()
  }, [columns, groupByDate, rowVirtualizer])

  if (items.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-[#555555] font-mono">
        <div className="w-12 h-12 border border-[#222222] flex items-center justify-center mb-3 text-[#444444]">
          Ø
        </div>
        <p className="text-sm uppercase tracking-wider text-[#777777]">No files matching filter</p>
        <p className="text-xs text-[#444444] mt-1">Upload files or drop them onto the window</p>
      </div>
    )
  }

  return (
    <div 
      ref={parentRef}
      className="flex-1 overflow-y-auto px-4 py-3 select-none"
    >
      <div
        style={{
          height: `${rowVirtualizer.getTotalSize()}px`,
          width: '100%',
          position: 'relative',
        }}
      >
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const rowData = virtualRows[virtualRow.index]
          if (!rowData) return null

          if (rowData.type === 'header') {
            const allSelected = rowData.ids.length > 0 && rowData.ids.every((id) => selectedIds.includes(id))
            const someSelected = rowData.ids.some((id) => selectedIds.includes(id))

            const handleToggleGroup = () => {
              if (allSelected) {
                deselectIds(rowData.ids)
              } else {
                selectIds(rowData.ids)
              }
            }

            return (
              <div
                key={`header-${rowData.dateKey}-${virtualRow.index}`}
                ref={rowVirtualizer.measureElement}
                data-index={virtualRow.index}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  transform: `translateY(${virtualRow.start}px)`,
                }}
                className="h-11 flex items-center justify-between border-b border-[#202020] bg-[#000000] px-1 font-mono select-none"
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-[#f0f0f0] tracking-wider uppercase">
                    {rowData.title}
                  </span>
                  <span className="text-[10px] text-[#666666]">
                    ({rowData.count} {rowData.count === 1 ? 'item' : 'items'})
                  </span>
                </div>
                <button
                  onClick={handleToggleGroup}
                  className={`text-[10px] px-2 py-0.5 border flex items-center gap-1 transition-colors ${
                    allSelected
                      ? 'border-[#ffffff] bg-[#1a1a1a] text-[#ffffff]'
                      : someSelected
                      ? 'border-[#444444] text-[#cccccc]'
                      : 'border-[#282828] text-[#777777] hover:text-[#ffffff] hover:border-[#444444]'
                  }`}
                  title={allSelected ? 'Deselect date' : 'Select all in date'}
                >
                  {allSelected ? <CheckSquare size={11} /> : <Square size={11} />}
                  <span>{allSelected ? 'Selected' : 'Select Date'}</span>
                </button>
              </div>
            )
          }

          return (
            <div
              key={rowData.key}
              ref={rowVirtualizer.measureElement}
              data-index={virtualRow.index}
              className="grid gap-3 pb-3"
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                transform: `translateY(${virtualRow.start}px)`,
              }}
            >
              {rowData.items.map((item) => (
                <MediaCard
                  key={item.id}
                  item={item}
                  isSelected={selectedIds.includes(item.id)}
                  onToggleSelect={onToggleSelect}
                  onOpenLightbox={onOpenLightbox}
                />
              ))}
            </div>
          )
        })}
      </div>
    </div>
  )
}

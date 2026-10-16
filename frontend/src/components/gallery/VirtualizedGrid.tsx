import React, { useRef, useState, useEffect } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { FileItem } from '../../types/vault'
import { MediaCard } from './MediaCard'

interface VirtualizedGridProps {
  items: FileItem[]
  selectedIds: string[]
  onToggleSelect: (id: string) => void
  onOpenLightbox: (item: FileItem) => void
}

export const VirtualizedGrid: React.FC<VirtualizedGridProps> = ({
  items,
  selectedIds,
  onToggleSelect,
  onOpenLightbox,
}) => {
  const parentRef = useRef<HTMLDivElement>(null)
  const [columns, setColumns] = useState(4)

  // Dynamic responsive column adjustment based on container width
  useEffect(() => {
    const updateColumns = () => {
      if (!parentRef.current) return
      const width = parentRef.current.clientWidth
      if (width < 500) setColumns(2)
      else if (width < 768) setColumns(3)
      else if (width < 1100) setColumns(4)
      else if (width < 1400) setColumns(5)
      else setColumns(6)
    }

    updateColumns()
    window.addEventListener('resize', updateColumns)
    return () => window.removeEventListener('resize', updateColumns)
  }, [])

  // Partition items into rows
  const rows = React.useMemo(() => {
    const result: FileItem[][] = []
    for (let i = 0; i < items.length; i += columns) {
      result.push(items.slice(i, i + columns))
    }
    return result
  }, [items, columns])

  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 280, // estimated height of row
    overscan: 3,
  })

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
          const rowItems = rows[virtualRow.index]
          return (
            <div
              key={virtualRow.key}
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
              {rowItems.map((item) => (
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

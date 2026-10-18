import React, { useEffect } from 'react'
import { X } from 'lucide-react'

interface ModalProps {
  isOpen: boolean
  onClose: () => void
  title?: string
  children: React.ReactNode
  maxWidth?: 'sm' | 'md' | 'lg' | 'xl' | '2xl'
}

export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  children,
  maxWidth = 'md'
}) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  if (!isOpen) return null

  const maxWidths = {
    sm: 'max-w-sm',
    md: 'max-w-md',
    lg: 'max-w-lg',
    xl: 'max-w-xl',
    '2xl': 'max-w-2xl',
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Opaque solid backdrop with zero blur */}
      <div 
        className="fixed inset-0 bg-[#000000] bg-opacity-85"
        onClick={onClose}
      />
      
      {/* Modal Card with sharp 90deg corners and 1px border */}
      <div className={`relative w-full ${maxWidths[maxWidth]} bg-[#111111] border border-[#282828] text-[#f0f0f0] z-10 flex flex-col max-h-[90vh]`}>
        {title && (
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#282828] bg-[#0c0c0c]">
            <h3 className="font-sans text-sm font-semibold text-[#f0f0f0] tracking-normal">
              {title}
            </h3>
            <button
              onClick={onClose}
              className="text-[#666666] hover:text-[#f0f0f0] p-1 border border-transparent hover:border-[#333333] transition-colors"
            >
              <X size={14} />
            </button>
          </div>
        )}
        <div className="p-4 overflow-y-auto">
          {children}
        </div>
      </div>
    </div>
  )
}

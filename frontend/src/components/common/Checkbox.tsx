import React from 'react'
import { Check } from 'lucide-react'

interface CheckboxProps {
  checked: boolean
  onChange: (checked: boolean) => void
  label?: string
  className?: string
}

export const Checkbox: React.FC<CheckboxProps> = ({
  checked,
  onChange,
  label,
  className = ''
}) => {
  return (
    <label className={`inline-flex items-center gap-2 cursor-pointer select-none ${className}`}>
      <div
        onClick={() => onChange(!checked)}
        className={`w-4 h-4 border flex items-center justify-center transition-colors ${
          checked
            ? 'bg-[#ffffff] border-[#ffffff] text-[#000000]'
            : 'bg-[#080808] border-[#333333] hover:border-[#666666]'
        }`}
      >
        {checked && <Check size={12} strokeWidth={3} />}
      </div>
      {label && <span className="text-xs font-mono text-[#cccccc]">{label}</span>}
    </label>
  )
}

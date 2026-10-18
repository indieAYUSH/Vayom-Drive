import React from 'react'

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string
  error?: string
  icon?: React.ReactNode
}

export const Input: React.FC<InputProps> = ({
  label,
  error,
  icon,
  className = '',
  ...props
}) => {
  return (
    <div className="flex flex-col gap-1.5 w-full font-sans">
      {label && (
        <label className="text-xs font-medium text-[#cccccc]">
          {label}
        </label>
      )}
      <div className="relative flex items-center">
        {icon && (
          <span className="absolute left-3 text-[#777777] pointer-events-none">
            {icon}
          </span>
        )}
        <input
          className={`w-full bg-[#080808] text-[#f0f0f0] border border-[#282828] focus:border-[#666666] focus:outline-none h-9 px-3 text-xs placeholder:text-[#777777] font-sans transition-colors ${icon ? 'pl-9' : ''} ${error ? 'border-[#ef4444]' : ''} ${className}`}
          {...props}
        />
      </div>
      {error && <span className="text-xs text-[#ef4444] mt-0.5">{error}</span>}
    </div>
  )
}

import React from 'react'

interface BadgeProps {
  children: React.ReactNode
  variant?: 'default' | 'info' | 'warning' | 'danger' | 'success' | 'white'
  className?: string
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = 'default',
  className = ''
}) => {
  const variants = {
    default: 'bg-[#181818] text-[#999999] border-[#282828]',
    info: 'bg-[#0c1a2e] text-[#60a5fa] border-[#1e3a5f]',
    warning: 'bg-[#291f05] text-[#facc15] border-[#594209]',
    danger: 'bg-[#260b0b] text-[#f87171] border-[#541616]',
    success: 'bg-[#0a2412] text-[#4ade80] border-[#164d26]',
    white: 'bg-[#ffffff] text-[#000000] border-[#ffffff] font-semibold'
  }

  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 text-[10px] font-mono border uppercase tracking-wider ${variants[variant]} ${className}`}
    >
      {children}
    </span>
  )
}

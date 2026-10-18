import React from 'react'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'outline' | 'danger' | 'ghost'
  size?: 'sm' | 'md' | 'lg'
  icon?: React.ReactNode
}

export const Button: React.FC<ButtonProps> = ({
  children,
  variant = 'secondary',
  size = 'md',
  icon,
  className = '',
  disabled,
  ...props
}) => {
  const baseStyles = 'inline-flex items-center justify-center font-sans font-medium transition-colors border select-none cursor-pointer disabled:cursor-not-allowed disabled:opacity-40 text-xs tracking-normal'

  const variants = {
    primary: 'bg-[#ffffff] text-[#000000] border-[#ffffff] hover:bg-[#e5e5e5] active:bg-[#cccccc]',
    secondary: 'bg-[#181818] text-[#f0f0f0] border-[#282828] hover:bg-[#222222] hover:border-[#383838] active:bg-[#282828]',
    outline: 'bg-transparent text-[#f0f0f0] border-[#282828] hover:border-[#555555] hover:bg-[#111111] active:bg-[#181818]',
    danger: 'bg-[#220808] text-[#ef4444] border-[#441111] hover:bg-[#331111] hover:border-[#661111] active:bg-[#441111]',
    ghost: 'bg-transparent text-[#999999] border-transparent hover:text-[#f0f0f0] hover:bg-[#111111] active:bg-[#181818]'
  }

  const sizes = {
    sm: 'h-7 px-2.5 gap-1.5 text-[11px]',
    md: 'h-9 px-3.5 gap-2 text-xs',
    lg: 'h-11 px-5 gap-2.5 text-sm'
  }

  return (
    <button
      className={`${baseStyles} ${variants[variant]} ${sizes[size]} ${className}`}
      disabled={disabled}
      {...props}
    >
      {icon && <span className="flex-shrink-0">{icon}</span>}
      {children}
    </button>
  )
}

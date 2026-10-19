import React, { useState, useEffect } from 'react'
import { HardDrive, ShieldCheck, Cloud, Sparkles, X, ChevronRight, ChevronLeft, FolderSync, Lock, Zap } from 'lucide-react'
import { Button } from '../common/Button'

export const OnboardingModal: React.FC = () => {
  const [isOpen, setIsOpen] = useState(false)
  const [step, setStep] = useState(1)

  useEffect(() => {
    // Only show once upon login / first visit.
    // Check both localStorage (permanent dismissal) and sessionStorage (session dismissal)
    const onboarded = localStorage.getItem('vayom_onboarded_v1')
    const sessionSeen = sessionStorage.getItem('vayom_guide_session_seen')
    
    if (!onboarded && !sessionSeen) {
      sessionStorage.setItem('vayom_guide_session_seen', 'true')
      setIsOpen(true)
    }
  }, [])

  // Listen for manual trigger from Settings or Help
  useEffect(() => {
    const handleOpenGuide = () => {
      setStep(1)
      setIsOpen(true)
    }
    window.addEventListener('open-vayom-guide', handleOpenGuide)
    return () => window.removeEventListener('open-vayom-guide', handleOpenGuide)
  }, [])

  const handleFinish = () => {
    localStorage.setItem('vayom_onboarded_v1', 'true')
    sessionStorage.setItem('vayom_guide_session_seen', 'true')
    setIsOpen(false)
  }

  // Close on Escape key press
  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleFinish()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen])

  if (!isOpen) return null

  return (
    <div 
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 select-none"
      onClick={handleFinish}
    >
      <div 
        className="w-full max-w-xl bg-[#0a0a0a] border border-[#282828] shadow-2xl flex flex-col overflow-hidden animate-in fade-in duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        
        {/* Header */}
        <div className="px-5 py-3.5 border-b border-[#222222] bg-[#0f0f0f] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-4 h-4 bg-[#ffffff] flex items-center justify-center">
              <div className="w-2 h-2 bg-[#000000]" />
            </div>
            <span className="text-xs font-semibold tracking-wider text-[#ffffff] uppercase font-sans">
              Vayom Drive Setup
            </span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs font-mono text-[#888888]">
              Step 0{step} / 04
            </span>
            <button
              onClick={handleFinish}
              className="text-[#666666] hover:text-[#ffffff] transition-colors p-1"
              title="Close guide (Esc)"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        {/* Step Progress Line */}
        <div className="w-full h-0.5 bg-[#181818] flex">
          <div 
            className="h-full bg-[#ffffff] transition-all duration-300"
            style={{ width: `${(step / 4) * 100}%` }}
          />
        </div>

        {/* Body Content */}
        <div className="p-6 space-y-4 min-h-[300px] flex flex-col justify-between">
          
          {/* STEP 1: WELCOME */}
          {step === 1 && (
            <div className="space-y-4 animate-in fade-in duration-150">
              <div className="w-10 h-10 bg-[#ffffff] flex items-center justify-center text-[#000000]">
                <Sparkles size={20} />
              </div>
              <div className="space-y-1.5">
                <h2 className="text-base font-semibold text-[#ffffff] tracking-normal">
                  Desktop-First Media Workstation
                </h2>
                <p className="text-xs text-[#a3a3a3] leading-relaxed">
                  Welcome to Vayom Drive. Designed specifically for video creators, photographers, editors, and archivists who need the fluidity of local desktop tools paired with decentralised and self-owned cloud storage.
                </p>
              </div>
              <div className="p-3 bg-[#111111] border border-[#1f1f1f] space-y-1.5 text-xs text-[#a3a3a3]">
                <div className="flex items-center gap-2 text-[#e5e5e5]">
                  <span className="w-1.5 h-1.5 bg-[#22c55e] inline-block" />
                  <span>You own your data: No middleman servers or subscription lock-ins.</span>
                </div>
                <div className="flex items-center gap-2 text-[#e5e5e5]">
                  <span className="w-1.5 h-1.5 bg-[#22c55e] inline-block" />
                  <span>Dual modes: Seamlessly switch between Photos timeline and Drive folders.</span>
                </div>
              </div>
            </div>
          )}

          {/* STEP 2: STORAGE CONNECTIONS */}
          {step === 2 && (
            <div className="space-y-4 animate-in fade-in duration-150">
              <div className="w-10 h-10 bg-[#0284c7]/20 border border-[#0284c7]/40 flex items-center justify-center text-[#38bdf8]">
                <Cloud size={20} />
              </div>
              <div className="space-y-1.5">
                <h2 className="text-base font-semibold text-[#ffffff] tracking-normal">
                  Multi-Backend Storage
                </h2>
                <p className="text-xs text-[#a3a3a3] leading-relaxed">
                  Vayom lets you store files across multiple backends simultaneously:
                </p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                <div className="p-2.5 bg-[#111111] border border-[#1f1f1f] space-y-1">
                  <span className="text-[#38bdf8] font-medium block">Telegram Cloud</span>
                  <span className="text-[#888888] text-xs">Decentralised storage via direct MTProto streaming.</span>
                </div>
                <div className="p-2.5 bg-[#111111] border border-[#1f1f1f] space-y-1">
                  <span className="text-[#fbbf24] font-medium block">S3 / Cloudflare R2</span>
                  <span className="text-[#888888] text-xs">Connect any standard S3 bucket for high-speed object delivery.</span>
                </div>
                <div className="p-2.5 bg-[#111111] border border-[#1f1f1f] space-y-1">
                  <span className="text-[#22c55e] font-medium block">Local & NAS</span>
                  <span className="text-[#888888] text-xs">Map folders from external SSDs or your local network storage.</span>
                </div>
                <div className="p-2.5 bg-[#111111] border border-[#1f1f1f] space-y-1">
                  <span className="text-[#c084fc] font-medium block">Nextcloud WebDAV</span>
                  <span className="text-[#888888] text-xs">Integrate directly with your self-hosted personal cloud.</span>
                </div>
              </div>
            </div>
          )}

          {/* STEP 3: SECURITY & ENCRYPTION */}
          {step === 3 && (
            <div className="space-y-4 animate-in fade-in duration-150">
              <div className="w-10 h-10 bg-[#22c55e]/20 border border-[#22c55e]/40 flex items-center justify-center text-[#22c55e]">
                <ShieldCheck size={20} />
              </div>
              <div className="space-y-1.5">
                <h2 className="text-base font-semibold text-[#ffffff] tracking-normal">
                  Client-Side Privacy & Protection
                </h2>
                <p className="text-xs text-[#a3a3a3] leading-relaxed">
                  Your privacy is preserved through transparent security measures:
                </p>
              </div>
              <div className="space-y-2 text-xs text-[#888888]">
                <div className="p-2.5 bg-[#111111] border border-[#1f1f1f] space-y-1">
                  <div className="flex items-center gap-1.5 text-[#e5e5e5] font-medium">
                    <Lock size={12} className="text-[#22c55e]" /> Local Encryption
                  </div>
                  <p className="text-xs text-[#888888]">
                    Supported files are encrypted locally on your machine before being uploaded to cloud channels.
                  </p>
                </div>
                <div className="p-2.5 bg-[#111111] border border-[#1f1f1f] space-y-1">
                  <div className="flex items-center gap-1.5 text-[#e5e5e5] font-medium">
                    <Zap size={12} className="text-[#fbbf24]" /> Portable Catalog Backups
                  </div>
                  <p className="text-xs text-[#888888]">
                    Export a single JSON catalog file anytime. Restore your entire library instantly on any machine with zero re-scanning.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* STEP 4: QUICK START */}
          {step === 4 && (
            <div className="space-y-4 animate-in fade-in duration-150">
              <div className="w-10 h-10 bg-[#ffffff] flex items-center justify-center text-[#000000]">
                <FolderSync size={20} />
              </div>
              <div className="space-y-1.5">
                <h2 className="text-base font-semibold text-[#ffffff] tracking-normal">
                  Ready to Ingest & Organize
                </h2>
                <p className="text-xs text-[#a3a3a3] leading-relaxed">
                  Here are quick ways to get the most out of your workstation:
                </p>
              </div>
              <div className="space-y-2 text-xs">
                <div className="flex items-start gap-2.5 text-[#cccccc]">
                  <span className="text-[#ffffff] font-semibold font-mono">1.</span>
                  <span><strong className="text-[#ffffff] font-medium">Drag and Drop:</strong> Drop media files or whole folders anywhere in Vayom to start uploading.</span>
                </div>
                <div className="flex items-start gap-2.5 text-[#cccccc]">
                  <span className="text-[#ffffff] font-semibold font-mono">2.</span>
                  <span><strong className="text-[#ffffff] font-medium">Folder Automation:</strong> In Storage Connections, add Watch Folders to auto-sync OBS captures or camera dumps.</span>
                </div>
                <div className="flex items-start gap-2.5 text-[#cccccc]">
                  <span className="text-[#ffffff] font-semibold font-mono">3.</span>
                  <span><strong className="text-[#ffffff] font-medium">Instant Search:</strong> Press <kbd className="px-1.5 py-0.5 bg-[#1c1c1c] text-[#ffffff] border border-[#333333] font-mono text-[11px]">⌘K</kbd> anytime to search by filename, camera, lens, or tag.</span>
                </div>
              </div>
            </div>
          )}

          {/* Footer Controls */}
          <div className="pt-4 border-t border-[#1a1a1a] flex items-center justify-between">
            {step > 1 ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setStep(step - 1)}
                icon={<ChevronLeft size={12} />}
              >
                Back
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                onClick={handleFinish}
              >
                Skip Guide
              </Button>
            )}

            {step < 4 ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() => setStep(step + 1)}
                className="flex items-center gap-1.5"
              >
                <span>Next</span>
                <ChevronRight size={12} />
              </Button>
            ) : (
              <Button
                variant="primary"
                size="sm"
                onClick={handleFinish}
              >
                Launch Workstation
              </Button>
            )}
          </div>

        </div>

      </div>
    </div>
  )
}

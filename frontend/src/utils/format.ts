/**
 * Robust human-readable byte formatting across all magnitudes (B, KB, MB, GB, TB).
 * Guarantees small files (<1 MB) never truncate to 0.0 MB or 0.0 GB.
 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || isNaN(bytes) || bytes <= 0) {
    return '0 B'
  }
  if (bytes < 1024) {
    return `${Math.round(bytes)} B`
  }
  if (bytes < 1024 * 1024) {
    const kb = bytes / 1024
    return `${kb < 10 ? kb.toFixed(2) : kb.toFixed(1)} KB`
  }
  if (bytes < 1024 * 1024 * 1024) {
    const mb = bytes / (1024 * 1024)
    return `${mb < 10 ? mb.toFixed(2) : mb.toFixed(1)} MB`
  }
  if (bytes < 1024 * 1024 * 1024 * 1024) {
    const gb = bytes / (1024 * 1024 * 1024)
    return `${gb.toFixed(2)} GB`
  }
  return `${(bytes / (1024 * 1024 * 1024 * 1024)).toFixed(2)} TB`
}

export function formatSpeed(bps: number | null | undefined): string {
  if (!bps || bps <= 0) return ''
  return `${formatBytes(bps)}/s`
}

/**
 * Utility for translating low-level technical errors (MTProto, S3, WebDAV, Network, Cryptography, File Paths)
 * into clear, actionable, creator-friendly prompts that never expose internal architecture or exploitable paths.
 */

// Regular expressions to detect and redact sensitive file paths and technical internals
const UNIX_PATH_REGEX = /(?:(?:\/[a-zA-Z0-9_\-\.]+){2,})/g
const WIN_PATH_REGEX = /(?:[a-zA-Z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]*)/g
const SENSITIVE_PARAM_REGEX = /(?:access_key|secret_key|password|token|bearer|api_key|hash)[=:\s]+[^\s&"',;]+/gi

export function sanitizeTechnicalDetails(input: string): string {
  if (!input) return ''
  let cleaned = input
    // Mask sensitive parameter values
    .replace(SENSITIVE_PARAM_REGEX, '[redacted]')
    // Mask absolute server or client filesystem paths
    .replace(UNIX_PATH_REGEX, 'specified location')
    .replace(WIN_PATH_REGEX, 'specified location')
  return cleaned
}

export function translateErrorMessage(raw: string | Error | null | undefined): string {
  if (!raw) return 'An unexpected error occurred. Please try again.'

  let message = typeof raw === 'string' ? raw : raw.message || String(raw)

  // 0. Unpack JSON error payloads if wrapped as string (e.g. {"error": "...", "message": "..."})
  const trimmed = message.trim()
  if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('{"') && trimmed.includes('}'))) {
    try {
      const parsed = JSON.parse(trimmed)
      message = parsed?.message || parsed?.detail || parsed?.error || message
    } catch {
      // If parsing fails, proceed with string matching
    }
  }

  // 1. License & Activation errors (never expose schema, prefix, payload or crypto internals)
  if (/INVALID_LICENSE|Malformed license|VAYOM-PRO|<payload>|<sig>|signature verification|InvalidSignature|base64.*license|forged license/i.test(message)) {
    return 'The license key entered is invalid. Please double-check your key and try again.'
  }
  if (/EXPIRED_LICENSE|License expired|validity period has expired/i.test(message)) {
    return 'This license key has expired. Please check your license details or obtain a new key.'
  }
  if (/ACTIVATION_FAILED|activate.*failed/i.test(message)) {
    return 'Unable to activate license key. Please check your key and try again.'
  }
  if (/PRO_FEATURE_REQUIRED/i.test(message)) {
    return 'This storage adapter is a Pro feature. Please unlock Vayom Drive Pro to continue.'
  }

  // 2. Telegram FLOOD_WAIT errors
  const floodMatch = message.match(/FLOOD_WAIT_(\d+)/i) || message.match(/wait\s*(\d+)\s*seconds/i)
  if (floodMatch) {
    const seconds = parseInt(floodMatch[1], 10)
    if (seconds >= 60) {
      const minutes = Math.ceil(seconds / 60)
      return `Telegram is temporarily rate-limiting requests. Vayom will resume automatically in ${minutes} minute${minutes > 1 ? 's' : ''}.`
    }
    return `Telegram is temporarily rate-limiting requests. Retrying in ${seconds} second${seconds > 1 ? 's' : ''}.`
  }

  // 3. Telegram Auth & Session errors
  if (/SESSION_REVOKED|AUTH_KEY_UNREGISTERED|USER_DEACTIVATED/i.test(message)) {
    return 'Your Telegram session has expired or was revoked. Please reconnect your Telegram account in Storage Connections.'
  }
  if (/PHONE_CODE_INVALID/i.test(message)) {
    return 'The verification code you entered is invalid. Please double-check and try again.'
  }
  if (/PHONE_CODE_EXPIRED/i.test(message)) {
    return 'The verification code has expired. Please request a new code.'
  }
  if (/PASSWORD_HASH_INVALID|2FA|Two-Step/i.test(message)) {
    return 'Incorrect Two-Step Verification (2FA) cloud password. Please try again.'
  }

  // 4. S3-Compatible Storage errors
  if (/NoSuchBucket/i.test(message)) {
    return 'The specified S3 bucket does not exist. Please check your bucket name.'
  }
  if (/InvalidAccessKeyId|SignatureDoesNotMatch|AccessDenied/i.test(message)) {
    return 'S3 access denied. Please verify your S3 Access Key ID, Secret Key, and bucket permissions.'
  }
  if (/EndpointConnectionError|Failed to connect to S3/i.test(message)) {
    return 'Unable to reach the S3 endpoint. Please check your internet connection and endpoint URL.'
  }

  // 5. Nextcloud / WebDAV errors
  if (/WebDAV.*(401|Unauthorized)/i.test(message) || /Nextcloud.*(401|password)/i.test(message)) {
    return 'Nextcloud login failed. Please check your WebDAV username and App Password.'
  }
  if (/WebDAV.*(404|Not Found)/i.test(message)) {
    return 'Nextcloud storage directory not found. Please verify your WebDAV URL and folder prefix.'
  }
  if (/WebDAV.*(403|Forbidden)/i.test(message)) {
    return 'Permission denied by Nextcloud server. Ensure your account has write access to the folder.'
  }

  // 6. Filesystem & Local Storage errors (sanitize paths completely)
  if (/Errno 2|No such file or directory/i.test(message)) {
    return 'The specified file or folder could not be found.'
  }
  if (/Errno 13|Permission denied/i.test(message)) {
    return 'Access denied. Insufficient permissions to access this folder or file.'
  }
  if (/Errno 17|File exists/i.test(message)) {
    return 'A file or folder with this name already exists.'
  }
  if (/Errno 28|ENOSPC|disk full|no space left/i.test(message)) {
    return 'Disk storage is full. Please free up space on your drive and try again.'
  }
  if (/PathTraversalError|outside workspace|traversal/i.test(message)) {
    return 'Access denied. The specified path is outside the allowed directory.'
  }

  // 7. Database & Internal Server errors
  if (/sqlite3|OperationalError|IntegrityError|no such table|no such column|database locked/i.test(message)) {
    return 'A database storage error occurred. Please try again.'
  }
  if (/Traceback|File ".*", line \d+|Internal Server Error/i.test(message)) {
    return 'A temporary server error occurred. Please try again later.'
  }

  // 8. General HTTP & Network errors
  if (/Failed to fetch|NetworkError|ERR_CONNECTION_REFUSED|ECONNREFUSED/i.test(message)) {
    return 'Cannot reach the Vayom desktop service. Please ensure the local service is running and accessible.'
  }
  if (/\b401\b|token expired|unauthorized/i.test(message)) {
    return 'Your session has expired. Please sign in again to continue.'
  }
  if (/\b403\b|forbidden/i.test(message)) {
    return 'Access denied. You do not have permission to perform this action.'
  }
  if (/\b404\b|not found/i.test(message)) {
    return 'The requested file or resource could not be found.'
  }

  // 9. Mask any lingering paths or JSON brackets from generic text
  const sanitized = sanitizeTechnicalDetails(message).trim()
  if (sanitized.startsWith('{') || sanitized.includes('{"') || sanitized.length > 250) {
    return 'Unable to complete request. Please verify your settings and try again.'
  }

  return sanitized
}

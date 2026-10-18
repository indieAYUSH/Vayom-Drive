/**
 * Utility for translating low-level technical errors (MTProto, S3, WebDAV, Network)
 * into clear, actionable, creator-friendly messages.
 */

export function translateErrorMessage(raw: string | Error | null | undefined): string {
  if (!raw) return 'An unexpected error occurred. Please try again.'

  const message = typeof raw === 'string' ? raw : raw.message || String(raw)

  // 1. Telegram FLOOD_WAIT errors
  const floodMatch = message.match(/FLOOD_WAIT_(\d+)/i) || message.match(/wait\s*(\d+)\s*seconds/i)
  if (floodMatch) {
    const seconds = parseInt(floodMatch[1], 10)
    if (seconds >= 60) {
      const minutes = Math.ceil(seconds / 60)
      return `Telegram is temporarily rate-limiting requests. Vayom will resume automatically in ${minutes} minute${minutes > 1 ? 's' : ''}.`
    }
    return `Telegram is temporarily rate-limiting requests. Retrying in ${seconds} second${seconds > 1 ? 's' : ''}.`
  }

  // 2. Telegram Auth & Session errors
  if (/SESSION_REVOKED|AUTH_KEY_UNREGISTERED|USER_DEACTIVATED/i.test(message)) {
    return 'Your Telegram session has expired or was revoked. Please reconnect your Telegram account in Storage Connections.'
  }
  if (/PHONE_CODE_INVALID/i.test(message)) {
    return 'The verification code you entered is invalid. Please double-check and try again.'
  }
  if (/PHONE_CODE_EXPIRED/i.test(message)) {
    return 'The verification code has expired. Please request a new code.'
  }
  if (/PASSWORD_HASH_INVALID|2FA/i.test(message)) {
    return 'Incorrect Two-Step Verification (2FA) cloud password. Please try again.'
  }

  // 3. S3-Compatible Storage errors
  if (/NoSuchBucket/i.test(message)) {
    return 'The specified S3 bucket does not exist. Please check your bucket name.'
  }
  if (/InvalidAccessKeyId|SignatureDoesNotMatch|AccessDenied/i.test(message)) {
    return 'S3 access denied. Please verify your S3 Access Key ID, Secret Key, and bucket permissions.'
  }
  if (/EndpointConnectionError|Failed to connect to S3/i.test(message)) {
    return 'Unable to reach the S3 endpoint. Please check your internet connection and endpoint URL.'
  }

  // 4. Nextcloud / WebDAV errors
  if (/WebDAV.*(401|Unauthorized)/i.test(message) || /Nextcloud.*(401|password)/i.test(message)) {
    return 'Nextcloud login failed. Please check your WebDAV username and App Password.'
  }
  if (/WebDAV.*(404|Not Found)/i.test(message)) {
    return 'Nextcloud storage directory not found. Please verify your WebDAV URL and folder prefix.'
  }
  if (/WebDAV.*(403|Forbidden)/i.test(message)) {
    return 'Permission denied by Nextcloud server. Ensure your account has write access to the folder.'
  }

  // 5. General HTTP & Network errors
  if (/Failed to fetch|NetworkError|ERR_CONNECTION_REFUSED/i.test(message)) {
    return 'Cannot reach the Vayom desktop service. Please ensure the local service is running and accessible.'
  }
  if (/401|token expired|unauthorized/i.test(message)) {
    return 'Your session has expired. Please sign in again to continue.'
  }
  if (/ENOSPC|disk full|no space left/i.test(message)) {
    return 'Disk storage is full. Please free up space on your local drive and try again.'
  }

  // Clean fallback
  return message
}

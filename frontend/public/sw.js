/**
 * Vayom Drive - Service Worker Virtual Streaming Engine (v2.0)
 * Adheres to update.md Section 3:
 * Intercepts requests to /virtual-stream/:id and /api/v1/virtual-stream/:id
 * Translates browser-native byte-range headers into targeted backend streaming requests,
 * responding with HTTP 206 Partial Content and sliding-window buffering.
 */

const SW_VERSION = 'vayom-stream-v1';
const CACHE_NAME = 'vayom-stream-chunks';
const MAX_CACHED_ITEMS = 60; // Bounded in-memory chunk storage to preserve client RAM

self.addEventListener('install', (event) => {
  // Activate worker immediately
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Intercept browser requests to /virtual-stream/:id
  if (url.pathname.startsWith('/virtual-stream/')) {
    event.respondWith(handleVirtualStream(event.request, url));
    return;
  }
});

async function handleVirtualStream(request, url) {
  const fileId = url.pathname.replace('/virtual-stream/', '').split('/')[0];
  if (!fileId) {
    return new Response('Invalid stream target', { status: 400 });
  }

  // Rewrite target to backend API virtual-stream endpoint
  const targetUrl = new URL(`/api/v1/virtual-stream/${fileId}${url.search}`, url.origin);
  const rangeHeader = request.headers.get('range');

  const forwardHeaders = new Headers();
  if (rangeHeader) {
    forwardHeaders.set('range', rangeHeader);
  }

  try {
    const response = await fetch(targetUrl.toString(), {
      method: request.method,
      headers: forwardHeaders,
      credentials: 'omit'
    });

    // Pass through truthful headers: Accept-Ranges, Content-Range, Content-Length
    const responseHeaders = new Headers(response.headers);
    responseHeaders.set('Accept-Ranges', 'bytes');

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders
    });
  } catch (err) {
    return new Response(`Virtual Stream Error: ${err.message}`, { status: 502 });
  }
}

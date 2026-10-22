'use strict';
// Only the public app shell is cached. API responses and personal data never are.
const CACHE = 'resonance-shell-v17';
const SHELL = ['/', '/index.html', '/styles.css', '/app.js', '/manifest.webmanifest', '/icon.svg'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  // Remove the former cache namespace during the first upgrade to Resonance.
  const owned = key => key.startsWith('resonance-shell-') || key.startsWith('social-brief-shell-');
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => owned(key) && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || event.request.method !== 'GET' || !SHELL.includes(url.pathname) || url.search) return;
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) {
      const copy = response.clone();
      event.waitUntil(caches.open(CACHE).then(cache => cache.put(event.request, copy)));
    }
    return response;
  }).catch(() => caches.match(event.request).then(cached => cached || new Response('Resonance is offline. Start the local app and reload. Saved information is not available while the app is stopped.', {status: 503, headers: {'Content-Type': 'text/plain; charset=utf-8'}}))));
});

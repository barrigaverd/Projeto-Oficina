const CACHE_NAME = 'oficina-cache-v1'

let URLS_TO_CACHE = ['/', '/offline.html', 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css', 'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css', 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js', 'https://unpkg.com/imask']

self.addEventListener('install', (event) => {
    event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
        return cache.addAll(URLS_TO_CACHE)
    })
    )
});

self.addEventListener('fetch', (event) => {
    event.respondWith(
    caches.match(event.request).then((response) => {
        if (response) {
            return response
        }
        else {
            return fetch(event.request).catch(() => {
                return caches.match('/offline.html')
            })
        }
    })
    )
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
    caches.keys().then((cacheNames) => {
        return Promise.all(
        cacheNames.map((cacheName) => {
            if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName)
            }
        }))
    }))
})



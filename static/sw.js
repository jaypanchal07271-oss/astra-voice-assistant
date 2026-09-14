/*
 * Astra PWA Service Worker
 * Provides offline caching for the application shell, assets, and graceful network fallbacks.
 */

// Upgraded from astra-pwa-v2 to astra-pwa-v7 for cache invalidation
const CACHE_NAME = 'astra-pwa-v7';

const APP_SHELL_ASSETS = [
  '/',
  '/manifest.json',
  '/static/style.css',
  '/static/app.js',
  '/static/manifest.json',
  '/static/icons/icon.svg',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  'https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap'
];

// Install Event: Pre-cache App Shell
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[Astra SW] Pre-caching offline app shell');
      return cache.addAll(APP_SHELL_ASSETS).catch((err) => {
        console.warn('[Astra SW] Failed to pre-cache some assets:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// Activate Event: Clean up stale caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keyList) => {
      return Promise.all(
        keyList.map((key) => {
          if (key !== CACHE_NAME) {
            console.log('[Astra SW] Removing old cache:', key);
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch Event: Network-First for APIs, Cache-First for static assets
self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // 1. API Endpoints: Network-First with graceful offline fallback
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request)
        .catch(() => {
          return new Response(
            JSON.stringify({
              reply: "Aap abhi offline hain. Server se connection nahi ho paa raha hai. Kripya network check karein.",
              audio_url: null,
              action: { status: "offline", error: "network_unavailable" }
            }),
            {
              headers: { 'Content-Type': 'application/json' },
              status: 200
            }
          );
        })
    );
    return;
  }

  // 2. Navigation (HTML pages): Network-First, show clear diagnostic error page if server unreachable
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .catch(() => {
          return new Response(
            `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Cannot Reach Astra Server</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 24px;
      background: #07090e;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      text-align: center;
    }
    .error-card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 20px;
      padding: 32px 24px;
      max-width: 440px;
      width: 100%;
      box-shadow: 0 16px 48px rgba(0, 0, 0, 0.7);
    }
    .icon {
      font-size: 44px;
      margin-bottom: 16px;
      display: inline-block;
    }
    h1 {
      font-size: 20px;
      margin: 0 0 12px;
      color: #f87171;
      font-weight: 600;
    }
    p {
      font-size: 14px;
      line-height: 1.6;
      color: #94a3b8;
      margin: 0 0 18px;
    }
    .checklist {
      text-align: left;
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: 12px;
      padding: 14px 16px;
      margin-bottom: 20px;
      font-size: 13px;
      color: #cbd5e1;
    }
    .checklist li {
      margin-bottom: 8px;
      line-height: 1.4;
    }
    .checklist li:last-child {
      margin-bottom: 0;
    }
    .code-snippet {
      background: #0f172a;
      border: 1px solid rgba(56, 189, 248, 0.2);
      border-radius: 8px;
      padding: 10px 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 11px;
      color: #38bdf8;
      word-break: break-all;
      margin-top: 6px;
      display: block;
      user-select: all;
    }
    .retry-btn {
      display: inline-block;
      width: 100%;
      background: linear-gradient(135deg, #3b82f6, #6366f1);
      color: #ffffff;
      padding: 14px 24px;
      border-radius: 12px;
      font-size: 14px;
      font-weight: 600;
      border: none;
      cursor: pointer;
      box-shadow: 0 4px 16px rgba(59, 130, 246, 0.4);
      transition: transform 0.15s ease;
    }
    .retry-btn:active {
      transform: scale(0.98);
    }
  </style>
</head>
<body>
  <div class="error-card">
    <span class="icon">📡</span>
    <h1>Cannot reach Astra server</h1>
    <p>Cannot reach Astra server — check you're on the same WiFi and the firewall allows port 8000.</p>
    
    <div class="checklist">
      <ul style="margin: 0; padding-left: 20px;">
        <li>Make sure your phone and laptop are connected to the <b>same Wi-Fi</b> network.</li>
        <li>Ensure you opened the laptop's LAN IP (e.g. <code>http://192.168.x.x:8000</code>), not <code>localhost</code>.</li>
        <li>Allow inbound TCP traffic on port 8000 in Windows Firewall (Admin Prompt):
          <span class="code-snippet">netsh advfirewall firewall add rule name="Astra" dir=in action=allow protocol=TCP localport=8000</span>
        </li>
      </ul>
    </div>

    <button class="retry-btn" onclick="window.location.reload()">Retry Connection</button>
  </div>
</body>
</html>`,
            {
              headers: { 'Content-Type': 'text/html; charset=utf-8' },
              status: 503,
              statusText: 'Service Unavailable'
            }
          );
        })
    );
    return;
  }

  // 3. Static Assets: Stale-While-Revalidate
  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      const fetchPromise = fetch(request).then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200 && request.method === 'GET') {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseToCache);
          });
        }
        return networkResponse;
      }).catch(() => cachedResponse);

      return cachedResponse || fetchPromise;
    })
  );
});

// 4. Push Event: Display push notification when app is closed or backgrounded
self.addEventListener('push', (event) => {
  let data = {
    title: 'Astra Reminder',
    body: 'Astra alert received.',
    url: '/?action=reminder',
    tag: 'astra-notification',
    actions: [
      { action: 'talk', title: '🎙️ Tap to talk' },
      { action: 'view', title: 'Open Astra' }
    ]
  };

  try {
    if (event.data) {
      const parsed = event.data.json();
      data = Object.assign(data, parsed);
    }
  } catch (err) {
    if (event.data) {
      data.body = event.data.text();
    }
  }

  const notificationOptions = {
    body: data.body,
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    tag: data.tag || 'astra-notification',
    renotify: true,
    requireInteraction: true,
    data: {
      url: data.url || '/'
    },
    actions: data.actions || [
      { action: 'talk', title: '🎙️ Tap to talk' },
      { action: 'view', title: 'Open Astra' }
    ]
  };

  event.waitUntil(
    self.registration.showNotification(data.title || 'Astra Assistant', notificationOptions)
  );
});

// 5. Notification Click Event: Handle "Tap to talk" or body tap
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const action = event.action;
  let targetUrl = event.notification.data && event.notification.data.url ? event.notification.data.url : '/';

  if (action === 'talk') {
    targetUrl = '/?action=talk';
  }

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      // If an existing window is open, focus it and signal voice talk
      for (let client of windowClients) {
        if ('focus' in client) {
          if (action === 'talk') {
            client.postMessage({ type: 'TRIGGER_TALK' });
          }
          return client.focus();
        }
      }
      // If no window open, launch new window directly into voice mode
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});

// 6. Client Message Event: Display sticky "Tap to talk" persistent notification
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SHOW_QUICK_TALK_NOTIFICATION') {
    const options = {
      body: 'Astra background trigger active. Tap to speak anytime.',
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      tag: 'astra-quick-talk',
      sticky: true,
      requireInteraction: true,
      data: { url: '/?action=talk' },
      actions: [
        { action: 'talk', title: '🎙️ Tap to talk' }
      ]
    };
    self.registration.showNotification('Astra Quick-Talk', options);
  }
});


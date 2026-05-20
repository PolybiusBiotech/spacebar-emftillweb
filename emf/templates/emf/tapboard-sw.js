{% load static %}
const VERSION = "v4";

const CACHE_NAME = `tapboard-${VERSION}`;

const APP_STATIC_RESOURCES = [
    "{% url "tapboard" %}",
    "{% static "emf/js/tapboard.js" %}",
    "{% static "emf/fonts/fonts.css" %}",
    "{% static "emf/css/tapboard.css" %}",
    "{% static "emf/fonts/raleway-regular-webfont.woff" %}",
    "{% static "emf/fonts/raleway-regular-webfont.woff2" %}",
    "{% static "emf/fonts/raleway-semibold-webfont.woff" %}",
    "{% static "emf/fonts/raleway-semibold-webfont.woff2" %}",
    "{% static "emf/tapboard-manifest.json" %}",
    "{% static "emf/img/tapboard-menu-icon-white.svg" %}",
    "{% static "emf/img/tapboard-not-connected.svg" %}",
    "{% static "emf/img/unknown-brand.png" %}",
    "{% static "emf/img/caution-icon.svg" %}",
];

// On install, cache the static resources
self.addEventListener("install", (event) => {
    event.waitUntil(
	(async () => {
	    const cache = await caches.open(CACHE_NAME);
	    cache.addAll(APP_STATIC_RESOURCES);
	})(),
    );
});

// delete old caches on activate
self.addEventListener("activate", (event) => {
    event.waitUntil(
	(async () => {
	    const names = await caches.keys();
	    await Promise.all(
		names.map((name) => {
		    if (name !== CACHE_NAME) {
			return caches.delete(name);
		    }
		}),
	    );
	    await clients.claim();
	})(),
    );
});

// On fetch, intercept server requests
// and respond with cached responses instead of going to network
self.addEventListener("fetch", (event) => {
    // Go to the cache first, and then the network.
    event.respondWith(
	(async () => {
	    const cache = await caches.open(CACHE_NAME);
	    const cachedResponse = await cache.match(event.request.url);
	    if (cachedResponse) {
		// Return the cached response if it's available.
		return cachedResponse;
	    }
	    // If resource isn't in the cache, fetch it
	    return fetch(event.request);
	    // Old version: return a 404
	    //return new Response(null, { status: 404 });
	})(),
    );
});

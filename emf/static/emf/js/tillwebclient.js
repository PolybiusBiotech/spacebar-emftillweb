/* Client code for the websocket API provided by emftillweb */
"use strict";

/* Number of ms to wait after websocket connection drops unexpectedly
 * (i.e. closed from other end, or network failure) before trying to
 * open it again */
let recoverTime = 5000;


/* Utility function to update text only if it has changed */
function updateText(element, contents) {
    if (element.innerText != contents) {
	element.innerText = contents;
    }
}


/* Utility function to update HTML only if it has changed */
function updateHTML(element, contents) {
    if (element.innerHTML != contents) {
	element.innerHTML = contents;
    }
}


/* Utility function to replace a node only if the replacement is different */
function updateNode(element, replacement) {
    if (!element.isEqualNode(replacement)) {
	element.replaceWith(document.importNode(replacement, true));
    }
}


function TillWebClient(ws_address, debug) {
    log("TillWebClient created for", ws_address);

    var socket = null;
    var close_in_progress = false;
    var nextRecoverTime = recoverTime;
    const connect_callbacks = [];
    const disconnect_callbacks = [];
    const mtypes = new Map();
    const keys = new Map();

    /* Arrange to close websocket when page is hidden, so page is
     * eligible for bfcache */
    window.addEventListener("pagehide", function on_pagehide() {
	log("TillWebClient: pagehide event");
	close_in_progress = true;
	if (socket) {
	    socket.close();
	    log("socket state is now", socket.readyState);
	}
    });

    window.addEventListener("pageshow", function on_pageshow(event) {
	if (event.persisted) {
	    log("TillWebClient: persisted pageshow event");
	    close_in_progress = false;
	    if (!socket) {
		log("persisted pageshow — no socket, calling connect()");
		connect();
	    } else {
		nextRecoverTime = 0;
	    }
	}
    });

    connect();

    function log(...args) {
	if (debug) {
	    console.log(...args);
	}
    }

    function connect() {
	log("TillWebClient connect()");
	if (socket) {
	    log("connect(): socket already present, state", socket.readyState);
	    return;
	}
	close_in_progress = false;
	nextRecoverTime = recoverTime;
	socket = new WebSocket(ws_address);
	log("connect() new socket state is", socket.readyState);
	socket.addEventListener("open", function on_connect() {
	    if (debug) {
		console.log("on_connect() socket state is", socket.readyState);
	    }
	    const cbs = [...connect_callbacks];
	    for (const callback of cbs) {
		callback();
	    }
	    for (const key of keys.keys()) {
		subscribe(key);
	    }
	});
	socket.addEventListener("message", function message(event) {
	    process_message(event.data);
	});
	socket.addEventListener("error", function ws_error(event) {
	    console.log("TillWebClient: websocket error");
	});
	socket.addEventListener("close", function ws_close(event) {
	    log("TillWebClient: websocket close event");
	    if (socket === null) {
		log("TillWebClient: close event when socket=null");
		return;
	    }
	    log("socket state is", socket.readyState);
	    socket = null;
	    if (!close_in_progress) {
		log("TillWebClient: scheduling reconnect in", nextRecoverTime);
		setTimeout(connect, nextRecoverTime);
	    }
	    /* Call close handlers */
	    const cbs = [...disconnect_callbacks];
	    for (const callback of cbs) {
		callback();
	    }
	});
    }

    function subscribe(key) {
	socket.send(`subscribe ${key}`);
    }

    function process_message(data) {
	const message = JSON.parse(data);
	const key_callbacks = keys.get(message.key) ?? [];
	const mtype_callbacks = mtypes.get(message.type) ?? [];
	for (const callback of key_callbacks) {
	    callback(message);
	}
	for (const callback of mtype_callbacks) {
	    callback(message);
	}
    }

    function onConnect(listener) {
	connect_callbacks.push(listener);
    }

    function onDisconnect(listener) {
	disconnect_callbacks.push(listener);
    }

    function onMessage(mtype, listener) {
	const callbacks = mtypes.get(mtype) ?? [];
	callbacks.push(listener);
	mtypes.set(mtype, callbacks);
    }

    function onKey(key, listener) {
	const callbacks = keys.get(key) ?? [];
	const nh = callbacks.push(listener);
	keys.set(key, callbacks);
	if (nh == 1 && socket && socket.readyState == WebSocket.OPEN) {
	    subscribe(key);
	}
    }

    return {
	onConnect: onConnect,
	onDisconnect: onDisconnect,
	onMessage: onMessage,
	onKey: onKey,
    };
}

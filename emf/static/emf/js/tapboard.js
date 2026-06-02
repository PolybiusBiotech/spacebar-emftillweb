/* EMF tap board   (main page, not service worker)
 */

const setup = document.getElementById("setup");
const run = document.getElementById("run");
const linename = document.getElementById("linename");
const logo = document.querySelector(".logo");
const tastingNotesContainer = document.getElementById("tastingNotesContainer");
const tastingNotes = document.getElementById("tastingNotes");
const noteFormContainer = document.getElementById("noteFormContainer");
const noteInput = document.getElementById("noteInput");
const product = document.querySelector(".product");
const price = document.querySelector(".price");
const linenote = document.querySelector(".linenote");
const notebutton = document.getElementById("notebutton");
const setupStatus = document.getElementById("setupStatus");
const setupForm = document.getElementById("setupForm");
const stocklineSelect = document.getElementById("stocklineSelect");
const notePassword = document.getElementById("notePassword");
const totalStock = document.querySelector(".totalStock");
const connectedStock = document.querySelector(".connectedStock");


/* The default HTML for the page includes the "not connected" logo */
const idleLogo = document.querySelector(".logo").style.backgroundImage;
const notConnectedStatus = linenote.innerText;

const recoverTime = 5000; /* How long to wait after network error? */

/* Local storage keys */
const lsStocklineKey = "tapboard-stockline";
const lsNotePassword = "tapboard-password";

/* Global state */
let stockline = null;
let stockline_id = null;
let password = null;
let socket = null;
let running = false;


// On page load, set up event handlers for the various buttons and interactive elements on the page.
document.addEventListener('DOMContentLoaded', () => {

    // Show menu
    menubutton.addEventListener("click", setup_mode);

    // Show tasting notes
    logo.addEventListener("click", toggle_tasting_notes);
    tastingNotesContainer.addEventListener("click", show_logo);

    // Show notes form
    notebutton.addEventListener("click", show_note_form);
    noteFormContainer.querySelector(".cancel").addEventListener("click", show_logo);

    // Handle set note buttons
    document.querySelectorAll(".setNote").forEach((button) => {
        button.addEventListener("click", (e) => {
            e.preventDefault();
            const note = button.innerText;
            set_note(note);
        });
    });

    // Handle update note button
    document.querySelector(".updateNote").addEventListener("click", (e) => {
        e.preventDefault();
        set_note();
    });

    // Handle development tap sequence
    document.querySelectorAll("header h1").forEach((h1) => {
        h1.addEventListener("click", _dev_tap);
    });

});

/* Utilities — prevent flicker when nothing has changed */
function updateHTML(element, contents) {
    if (element.innerHTML != contents) {
    	element.innerHTML = contents;
    }
}

function updateText(element, contents) {
    if (element.innerText != contents) {
	    element.innerText = contents;
    }
}

/* "Run" mode */
function toggle_tasting_notes() {
    if (tastingNotes.innerHTML) {
        if (tastingNotesContainer.classList.contains("d-none")) {
            tastingNotesContainer.classList.remove("d-none");
            noteFormContainer.classList.add("d-none");
        } else {
            show_logo();
        }
    }
}

function show_logo() {
    tastingNotesContainer.classList.add("d-none");
    noteFormContainer.classList.add("d-none");
    logo.classList.remove("d-none");
}

function show_note_form() {
    if (noteFormContainer.classList.contains("d-none")) {
        tastingNotesContainer.classList.add("d-none");
        noteFormContainer.classList.remove("d-none");
    } else {
        show_logo();
    }
}

function set_note(note) {
    const new_note = note ?? noteInput.value;
    show_logo();
    noteInput.value = "";
    if (stockline_id === null) {
        return false;
    }
    fetch(`/api/stockline/${stockline_id}/set-note/`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            password: password,
            note: new_note,
        }),
    });
    return false;
}

function not_connected_message() {
    updateText(linenote, notConnectedStatus);
    logo.style.backgroundImage = idleLogo;
    connectedStock.classList.add("d-none");
    totalStock.classList.add("d-none");
    updateText(product, "");
    updateText(price, "");
}

function process_message(message) {
    m = JSON.parse(message);
    if (m.type == "error") {
	    /* Go back to setup mode and try again */
	    setup_mode();
    } else if (m.type == "not present") {
        /* The server is probably restarting; leave things as they are
        for now but note in the status line */
        stockline_id = null;
        updateText(linenote, "(No data received from server; waiting...)");
    } else if (m.type == "stockline") {
        /* Normal expected response */
        updateText(linename, m.name);
        stockline_id = m.id;

        if (m.stockitem === null) {
            /* There's nothing connected to the line right now */
            show_logo();
            logo.style.backgroundImage = idleLogo;
            updateHTML(tastingNotes, "");
            updateText(product, "No product connected");
            updateText(price, "");
            updateHTML(linenote, m.note);
            connectedStock.classList.add("d-none");
            totalStock.classList.add("d-none");

        } else {
            if (m.stockitem.stocktype.logo) {
                logo.style.backgroundImage = `url(${m.stockitem.stocktype.logo})`;
            } else {
                logo.style.backgroundImage = `url(${idleLogo})`;
            }

            if (m.stockitem.stocktype.tasting_notes) {
                updateHTML(tastingNotes, m.stockitem.stocktype.tasting_notes);
            } else {
                updateHTML(tastingNotes, "");
                show_logo();
            }

            /* The space in '% ABV' is replaced with a non-breaking space
            to improve how this looks on narrow displays */
            updateText(product, m.stockitem.stocktype.fullname.replace("% ABV", "% ABV"));
            updateText(price, `£${m.stockitem.stocktype.price}/${m.stockitem.stocktype.sale_unit_name}`);


            if (m.note) {
                linenote.classList.add("caution");
            } else {
                linenote.classList.remove("caution");
            }
            updateText(linenote, m.note);

            // Parse JSON integers
            let connectedStockRemaining = parseInt(m.stockitem.remaining);
            let connectedStockSize = parseInt(m.stockitem.size);
            let totalStockRemaining = parseInt(m.stockitem.stocktype.base_units_remaining);
            let totalStockBought = parseInt(m.stockitem.stocktype.base_units_bought);

            // Label QS
            let connectedStockLabel = connectedStock.querySelector(".label");
            let totalStockLabel = totalStock.querySelector(".label");

            // Update progress bars
            connectedStock.querySelector(".bar").style.width = connectedStockRemaining > 0 ? `${(connectedStockRemaining / connectedStockSize) * 100}%` : "%";
            totalStock.querySelector(".bar").style.width = totalStockBought > 0 ? `${(totalStockRemaining / totalStockBought) * 100}%` : "%";
            updateText(connectedStockLabel, `Connected: ${connectedStockRemaining} / ${connectedStockSize} ${m.stockitem.stocktype.base_unit_name}s`);
            updateText(totalStockLabel, `Total: ${totalStockRemaining} / ${totalStockBought} ${m.stockitem.stocktype.base_unit_name}s`);

            // Display progress bars
            connectedStock.classList.remove("d-none");
            totalStock.classList.remove("d-none");
        }
    } else {
        /* Unknown message type */
        updateText(linenote, `Unknown message type ${m.type} received!`);
    }
}

function subscribe() {
    socket.send(`subscribe ${stockline}`);
}

function run_mode() {
    running = true;
    setup.classList.add("d-none");

    if (!password) {
        /* Hide the "Problem?" note button if we don't have a password */
        notebutton.classList.add("d-none");
    } else {
        notebutton.classList.remove("d-none");
    }

    run.classList.remove("d-none");

    socket = new WebSocket(websocket_address);

    socket.addEventListener("open", subscribe);

    socket.addEventListener("message", (event) => {
	    process_message(event.data);
    });

    socket.addEventListener("error", (event) => {
        console.log("websocket error");
    });

    socket.addEventListener("close", (event) => {
        socket = null;
        not_connected_message();

        if (running) {
            setTimeout(run_mode, recoverTime);
        }
    });
}

/* "Setup" mode */

async function setup_mode() {
    let stocklines = null;

    show_logo();

    running = false;
    run.classList.add("d-none");
    setupForm.classList.add("d-none");
    setupStatus.innerText = "Fetching list of stock lines...";
    setup.classList.remove("d-none");

    /* Make sure websocket is closed down */
    if (socket !== null) {
	    socket.close();
    }

    /* Fetch list of stocklines and set up form element */
    try {
        const response = await fetch("/api/stocklines.json?type=regular");
        stocklines = await response.json();
    } catch (error) {
        console.error(error);
        setupStatus.innerText = "Failed to fetch list of stock lines. Are we offline?";
        return;
    }

    const options = stocklines.stocklines.map((x) => {
        o = document.createElement("option");
        o.innerText = x.name;
        o.value = x.key;
        return o;
    });

    stocklineSelect.replaceChildren(...options);
    stocklineSelect.value = stockline;
    notePassword.value = password;
    setupStatus.innerText = "Choose a stock line to display:";
    setupForm.classList.remove("d-none");
}

function finish_setup() {
    /* Called when form is submitted */
    stockline = stocklineSelect.value;
    password = notePassword.value;
    /* Try to suppress "save password" prompt which sometimes pops up
       when the user submits the "set note" form */
    notePassword.value = "";
    localStorage.setItem(lsStocklineKey, stockline);
    localStorage.setItem(lsNotePassword, password);
    run_mode();
    return false;
}

/* Wake lock management — we try to always have it */
if ("wakeLock" in navigator) {
    let wakeLock = null;

    async function acquire_wakelock() {
        try {
            wakeLock = await navigator.wakeLock.request("screen");
            wakeLock.addEventListener("release", () => {
            wakeLock = null;
            });
        } catch (err) {
            console.log("failed to acquire wakelock", err);
        }
    }

    (async () => {
        await acquire_wakelock();

        document.addEventListener("visibilitychange", async () => {
            if (wakeLock === null && document.visibilityState === "visible") {
                await acquire_wakelock();
            }
        });
    })();
}

/* Hidden dev command: tap the setup screen header 5 times quickly to
 * unregister the service worker, clear all caches, and hard-reload.
 * Useful during development when the SW has cached stale assets. */
let _devTapCount = 0;
let _devTapTimer = null;

function _dev_tap() {
    _devTapCount++;
    if (_devTapTimer) {
        clearTimeout(_devTapTimer);
    }
    if (_devTapCount >= 5) {
        _devTapCount = 0;
        _dev_clear_cache();
    } else {
        _devTapTimer = setTimeout(() => { _devTapCount = 0; }, 2000);
    }
}

async function _dev_clear_cache() {
    setupStatus.innerText = "Clearing cache and reloading…";
    if ("serviceWorker" in navigator) {
        const registrations = await navigator.serviceWorker.getRegistrations();
        for (const reg of registrations) {
            await reg.unregister();
        }
    }
    if ("caches" in window) {
        const names = await caches.keys();
        await Promise.all(names.map((n) => caches.delete(n)));
    }
    location.reload(true);
}

/* Initialisation */

function init() {
    stockline = localStorage.getItem(lsStocklineKey);
    password = localStorage.getItem(lsNotePassword);
    if (stockline === null) {
	    setup_mode();
    } else {
	    run_mode();
    }
}

/* If we become visible, send the "subscribe" message again to get an
 * immediate update. If the websocket has become unusable, hopefully
 * this will trigger an error and we can reopen it in response. */
document.addEventListener("visibilitychange", () => {
    if (socket !== null && stockline !== null) {
	subscribe();
    }
});

init();

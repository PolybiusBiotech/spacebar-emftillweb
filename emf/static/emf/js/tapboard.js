/* EMF tap board   (main page, not service worker)
 */

const setup = document.getElementById("setup");
const run = document.getElementById("run");
const linename = document.getElementById("linename");
const logo = document.getElementById("logo");
const tastingNotesContainer = document.getElementById("tastingNotesContainer");
const tastingNotes = document.getElementById("tastingNotes");
const noteFormContainer = document.getElementById("noteFormContainer");
const noteInput = document.getElementById("noteInput");
const product = document.getElementById("product");
const price = document.getElementById("price");
const linenote = document.getElementById("linenote");
const notebutton = document.getElementById("notebutton");
const status = document.getElementById("status");
const setupStatus = document.getElementById("setupStatus");
const setupForm = document.getElementById("setupForm");
const stocklineSelect = document.getElementById("stocklineSelect");
const notePassword = document.getElementById("notePassword");

/* The default HTML for the page includes the "not connected" logo */
const notConnectedLogo = logo.innerHTML;
const notConnectedStatus = status.innerText;

const idleLogo = document.getElementById("idleLogo").innerHTML;

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
function show_tasting_notes() {
    if (tastingNotes.innerHTML) {
	logo.classList.add("d-none");
	noteFormContainer.classList.add("d-none");
	tastingNotesContainer.classList.remove("d-none");
    }
}

function show_logo() {
    tastingNotesContainer.classList.add("d-none");
    noteFormContainer.classList.add("d-none");
    logo.classList.remove("d-none");
}

function show_note_form() {
    tastingNotesContainer.classList.add("d-none");
    logo.classList.add("d-none");
    noteFormContainer.classList.remove("d-none");
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
    updateText(status, notConnectedStatus);
    updateHTML(logo, notConnectedLogo);
    /* Leave the line name up if present */
    updateText(product, "");
    updateText(price, "");
    updateHTML(linenote, "");
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
	updateText(status, "(No data received from server; waiting...)");
    } else if (m.type == "stockline") {
	/* Normal expected response */
	updateText(linename, m.name);
	stockline_id = m.id;
	if (m.stockitem === null) {
	    /* There's nothing connected to the line right now */
	    show_logo();
	    updateHTML(logo, idleLogo);
	    updateHTML(tastingNotes, "");
	    updateText(product, "");
	    updateText(price, "");
	    updateHTML(linenote, `<p>${m.note}</p>`);
	    updateText(status, "No product connected");
	} else {
	    if (m.stockitem.stocktype.logo) {
		updateHTML(logo, `<a onclick="show_tasting_notes();"><img src="${m.stockitem.stocktype.logo}" alt=""></a>`);
	    } else {
		updateHTML(logo, "");
	    }
	    if (m.stockitem.stocktype.tasting_notes) {
		updateHTML(tastingNotes, `<p>${m.stockitem.stocktype.tasting_notes}</p>`);
	    } else {
		updateHTML(tastingNotes, "");
		show_logo();
	    }
	    /* The space in '% ABV' is replaced with a non-breaking space
	       to improve how this looks on narrow displays */
	    updateText(product, m.stockitem.stocktype.fullname.replace(
		"% ABV", "% ABV"));
	    updateText(price, `£${m.stockitem.stocktype.price}/${m.stockitem.stocktype.sale_unit_name}`);
	    if (m.note) {
		updateHTML(linenote, `<p><div class="caution"></div> ${m.note}</p>`);
	    } else {
		updateHTML(linenote, "");
	    }
	    updateText(status, `Connected: ${m.stockitem.remaining}/${m.stockitem.size} ${m.stockitem.stocktype.base_unit_name}s; total: ${m.stockitem.stocktype.base_units_remaining}/${m.stockitem.stocktype.base_units_bought} ${m.stockitem.stocktype.base_unit_name}s`);
	}
    } else {
	/* Unknown message type */
	updateText(status, `Unknown message type ${m.type} received!`);
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

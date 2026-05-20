const lineTable = document.getElementById("lineTable");

let socket = null;
let subscriptions = null;

async function setup() {
    const response = await fetch(`/api/stocklines.json?type=regular&location=${sl_location}`);
    const stocklines = await response.json();

    const rows = stocklines.stocklines.map((x) => {
	const template = document.createElement('template');
	template.innerHTML = `<tr id="sl${x.id}"><td id="slName${x.id}">${x.name}</td><td id="slProduct${x.id}"></td><td id="slRemaining${x.id}"></td><td id="slNote${x.id}"></td>`;
	return template.content.children[0];
    });
    subscriptions = stocklines.stocklines.map((x) => x.key);
    lineTable.replaceChildren(...rows);
    run();
}

function subscribe() {
    for (const key of subscriptions) {
	socket.send(`subscribe ${key}`);
    }
}

function updateText(element, contents) {
    if (element.innerText != contents) {
	element.innerText = contents;
    }
}

function process_message(message) {
    m = JSON.parse(message);
    if (m.type == "stockline") {
	const id = m.id;
	try {
	    const name = document.getElementById(`slName${id}`);
	    const product = document.getElementById(`slProduct${id}`);
	    const remaining = document.getElementById(`slRemaining${id}`);
	    const note = document.getElementById(`slNote${id}`);

	    updateText(name, m.name);
	    updateText(note, m.note);
	    if (m.stockitem === null) {
		updateText(product, "");
		updateText(remaining, "");
	    } else {
		updateText(product, `${m.stockitem.id}: ${m.stockitem.stocktype.fullname}`);
		updateText(remaining, `${m.stockitem.remaining}/${m.stockitem.size} ${m.stockitem.stocktype.base_unit_name}s`);
	    }
	} catch (e) {
	    console.log("exception while updating stockline", m);
	}
    }
}

function run() {
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
    });
}
    
setup();

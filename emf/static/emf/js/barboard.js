const barboard = document.getElementById("barboard");

let socket = null;
let subscriptions = null;

async function setup() {
    const response = await fetch(`/api/stocklines.json?type=regular&location=${sl_location}&startswith=Tap`);
    const stocklines = await response.json();

    const rows = stocklines.stocklines.map((x) => {
	const template = document.createElement('template');
	template.innerHTML = `<div class="tapcard"><div class="logo" id="slLogo${x.id}"></div><div id="slPrice${x.id}"></div></div>`;
	return template.content.children[0];
    });
    subscriptions = stocklines.stocklines.map((x) => x.key);
    barboard.replaceChildren(...rows);
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

function updateHTML(element, contents) {
    if (element.innerHTML != contents) {
	element.innerHTML = contents;
    }
}

function process_message(message) {
    m = JSON.parse(message);
    if (m.type == "stockline") {
	const id = m.id;
	try {
	    const logo = document.getElementById(`slLogo${id}`);
	    const price = document.getElementById(`slPrice${id}`)
	    
	    if (m.stockitem === null) {
		updateText(price, "");
	    } else {
		if (m.stockitem.stocktype.logo === null) {
		    updateText(logo, m.stockitem.stocktype.fullname.replace(
			"% ABV", "% ABV"));
		} else {
		    updateHTML(logo, `<img src="${m.stockitem.stocktype.logo}" alt="" />`);
		}
		updateText(price, `£${m.stockitem.stocktype.price}/${m.stockitem.stocktype.sale_unit_name}`);
	    }
	} catch (e) {
	    console.log("exception while updating stockline", e);
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
	setTimeout(run, 5000);
    });
}
    
setup();

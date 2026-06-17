const clock = document.getElementById("clock");

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/* Clock loop */
(async () => {
    while (true) {
	const date = new Date();
	const h = date.getHours().toString().padStart(2, '0');
	const m = date.getMinutes().toString().padStart(2, '0');
	const s = date.getSeconds();

	clock.innerText = h + ':' + m;

	/* We want to wait until just after the minute ticks over */
	await sleep((60 - s) * 1000);
    }
})();

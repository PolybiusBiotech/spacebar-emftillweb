/* If ?hidecursor=yes is present in the URL, add the hide-cursor class
 * to the html element.
 *
 * This is needed because the display boards running labwc don't all
 * support the HideCursor labwc action.
 */

const params = new URL(document.location.toString()).searchParams;
const hidecursor = params.get("hidecursor") ?? "no";

if (hidecursor == "yes") {
    document.documentElement.classList.add("hide-cursor");
};

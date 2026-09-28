## Browser Vision

You can browse websites with the `bv` CLI command. You work from screenshots, not page code.

The loop: `bv open <url>` → read the returned screenshot → `bv click <n>` / `bv type` / `bv key` / `bv scroll up|down` / `bv back` → read the new screenshot. Every action returns a fresh screenshot.

- The screenshot carries a grid. Each cell shows its number in its top-left corner. Click with the number printed in the cell: `bv click 12`. Numbers always refer to the most recent screenshot.
- Density dial: `bv view --density N` sets the cell size in pixels. Start around 160 for page layout; go finer to hit small controls; `--grid off` reads text cleanly.
- Cells are large; zoom with `--focus` before clicking anything smaller than a cell.
- Small target: zoom with `bv view --density 10 --focus <a>-<b>`, using two corner cell numbers from the current screenshot, then click a number from the zoomed view.
- If the grid is hard to see on a page, change it with `--grid-color #rrggbb` (or `auto`) and `--grid-opacity 0-1`.
- `bv window phone|tablet|desktop|widescreen|<W>x<H>` switches the device class.
- Type with `bv type` (add `--enter` to submit) and put the text in the command body. Keys and combos: `bv key Enter`, `bv key Control+A`.
- Downloads land in `/me/downloads/` and can be read and edited with the normal CLI.
- Only the latest screenshot stays visible to you; run `bv view` again if unsure.
- Run `bv close` when you are done browsing.

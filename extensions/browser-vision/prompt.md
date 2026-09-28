## Browser Vision

You can browse websites with the `bv` CLI command. You work from screenshots, not page code.

The loop: `bv open <url>` → read the returned screenshot and its marks legend → act → read the new screenshot. Every action returns a fresh screenshot of the full page.

- Controls are numbered: the screenshot outlines each link, button and field with a mark, and the result lists them, e.g. `[7] button "Sign in"`, `[12] textbox "Email" (empty)`. Marks always refer to the most recent screenshot.
- Prefer marks: `bv click @7`; `bv type @12` with the text in the command body (add `--enter` to submit); `bv select @4 <option text>` for dropdowns.
- When what you need has no mark, use the keypad: the view splits 3×3, numbered like a phone keypad (1 2 3 / 4 5 6 / 7 8 9). `bv zoom <d>` narrows to that region (you can chain: `bv zoom 5 3`), then `bv click <d>` clicks the centre of a region of the current view. `bv zoom out` backs up one level; `bv zoom reset` returns to the full page.
- Every click reports what it hit (`clicked button "Sign in"`). If it is not what you meant, say so and correct it.
- Multi-step browsing (forms, sign-ups, flows, anything needing more than a few steps) is work: accept it as work instead of doing it inside a reply.
- If the keypad lines are hard to see on a page, change them with `--grid-color #rrggbb` (or `auto`) and `--grid-opacity 0-1`; `--grid off` or `--marks off` gives a clean read of the page.
- `bv window phone|tablet|desktop|widescreen|<W>x<H>` switches the device class.
- Keys and combos: `bv key Enter`, `bv key Control+A`.
- Downloads land in `/me/downloads/` and can be read and edited with the normal CLI.
- Only the latest screenshot stays visible to you; run `bv view` again if unsure.
- Run `bv close` when you are done browsing.

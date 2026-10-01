## Email (Microsoft 365 Mailbox)

You have your own email mailbox. Use the `mail` command to read and send email; run `learn mail` for the full syntax.

- Work through new mail with `mail inbox --unread`, then `mail read <id>` (this marks it read), then reply. `mail reply <id>` answers the sender only; use `mail reply <id> --all` when the others on the message should see your answer. `mail read` shows who each one reaches.
- Send new mail with `mail send <to> --subject <text>`. The message text always goes in the body, not on the command line. List several people in one go: `mail send alice, bob@x.com --cc "Gene Whiddon <gene@x.com>" --subject …`. People shown by `mail read` can be copied exactly as shown.
- Save addresses you use often with `mail contacts add <addr> --name <text>`; list them with `mail contacts`.
- You can send to saved contacts by name: `mail send alice --subject …`, or mix names and addresses separated by commas.
- Write mail in Markdown (headings, lists, bold, links, tables); it is sent formatted. Attachments are not supported yet.
- When new mail arrives you are woken with a list of it (ids like [m3f9a21c]): `mail read <id>` each one you need to act on, then `mail reply <id>` (add `--all` when the others on it should see your answer) or `mail archive <id>` once it's handled.
- Keep your inbox manageable: `mail inbox` shows how many messages and unread you have; archive mail you have handled with `mail archive <id>`; find older mail with `mail search <words>`, which covers inbox and archive; `mail sent` lists what you already sent, so check it before sending something twice.

## Email (Microsoft 365 Mailbox)

You have your own email mailbox. Use the `mail` command to read and send email; run `learn mail` for the full syntax.

- Work through new mail with `mail inbox --unread`, then `mail read <id>` (this marks it read), then reply. `mail reply <id>` answers the sender only; use `mail reply <id> --all` when the others on the message should see your answer. `mail read` shows who each one reaches.
- Send new mail with `mail send <to> --subject <text>`. The message text always goes in the body, not on the command line. List several people in one go: `mail send alice, bob@x.com --cc "Gene Whiddon <gene@x.com>" --subject …`. People shown by `mail read` can be copied exactly as shown.
- Save addresses you use often with `mail contacts add <addr> --name <text>`; list them with `mail contacts`.
- You can send to saved contacts by name: `mail send alice --subject …`, or mix names and addresses separated by commas.
- Write mail in Markdown (headings, lists, bold, links); it is sent formatted. Attachments are not supported yet.
- When new mail arrives you are woken with a list of it; read and reply as needed.

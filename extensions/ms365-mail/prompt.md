## Email (Microsoft 365 Mailbox)

You have your own email mailbox. Use the `mail` command to read and send email; run `learn mail` for the full syntax.

- Work through new mail with `mail inbox --unread`, then `mail read <id>` (this marks it read), then `mail reply <id>` (or `--all`).
- Send new mail with `mail send <to> --subject <text>`. The message text always goes in the body, not on the command line.
- Save addresses you use often with `mail contacts add <addr> --name <text>`; list them with `mail contacts`.
- You can send to saved contacts by name: `mail send alice --subject …`, or mix names and addresses separated by commas.
- Messages are plain text. Attachments are not supported yet.

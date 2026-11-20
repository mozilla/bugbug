Chrome for Android runs on the same emulator. In Chrome, use new_page before
navigate_page to create and select a tab. Keep screenshots of the Firefox issue
before switching to Chrome.
On Android, BROWSER_BIN is the browser's remote debugging endpoint; use the
provided reference, including its connection/disconnection code.
Before running the Firefox script, call restart_firefox once to release Firefox
MCP's WebDriver session; scripts and MCP cannot hold concurrent Firefox WebDriver
sessions. Before running the Chrome script, call a Chrome DevTools tool such as
list_pages so Chrome is in the foreground. After the scripts, Firefox MCP
reconnects on its next tool call; reopen the affected URL before reusing it.

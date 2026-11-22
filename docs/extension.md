# The ApplAI extension

JobTeaser refuses ApplAI's requests, but serves its offer pages to the candidate's own Chrome. The ApplAI extension (`extension/`) reads them there, with the session Chrome already has: the candidate never signs in anywhere for ApplAI. It only reads: no tab, no click, no form. Its permissions are `*.jobteaser.com`, ApplAI's address `http://127.0.0.1`, and an alarm to ask ApplAI every minute.

## Set it up in Chrome on Windows, with ApplAI in WSL

1. In Chrome, open `chrome://extensions` and turn on **Developer mode**.
2. **Load unpacked**, then pick the `extension` folder of the WSL checkout, for example `\\wsl.localhost\Ubuntu\home\<you>\applai\extension`. If Chrome refuses that path, copy the folder to Windows (`cp -r extension /mnt/c/Users/<you>/applai-extension`) and load the copy. After a change in `extension/`, copy it again if need be and press ApplAI's reload button.
3. Copy the 32-letter **ID** Chrome shows under ApplAI, and set it in `settings.yaml` of the data directory:

   ```yaml
   extension_id: kfbcmpgnoaljdhiekfbcmpgnoaljdhie
   ```

4. Run `applai serve` (port 8000: the extension asks there). WSL forwards Windows' `127.0.0.1` to it.
5. Sign in to JobTeaser in that Chrome as usual. Within a minute, `GET /api/browser/status` answers `"connected": true`.

A page the extension cannot read is reported to ApplAI and asked for again only after the next scan. After a second failure, the offer is triaged from its job alert, and its card says "full text not read".

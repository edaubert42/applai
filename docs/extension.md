# The ApplAI extension

JobTeaser refuses ApplAI's requests, but serves its offer pages to the candidate's own Chrome. The ApplAI extension (`extension/`) reads them there, with the session Chrome already has: the candidate never signs in anywhere for ApplAI. It also reads the form of each accepted offer (`extension/forms.js`) and fills waiting forms (`extension/fill.js`), blocking every submission in code (ADR 0003) and leaving the filled tab open for the candidate. Opening, reading and filling stay limited in code to the manifest's explicit ATS and board hosts; `<all_urls>` is added only for Chrome's `captureVisibleTab` screenshot after filling. Its permissions are `alarms` and `scripting`.

## Set it up in Chrome on Windows, with ApplAI in WSL

1. In Chrome, open `chrome://extensions` and turn on **Developer mode**.
2. **Load unpacked**, then pick the `extension` folder of the WSL checkout, for example `\\wsl.localhost\Ubuntu\home\<you>\applai\extension`. If Chrome refuses that path, copy the folder to Windows (`cp -r extension /mnt/c/Users/<you>/applai-extension`) and load the copy. After a change in `extension/`, copy it again if need be and press ApplAI's reload button.
3. Copy the 32-letter **ID** Chrome shows under ApplAI, and set it in `settings.yaml` of the data directory:

   ```yaml
   extension_id: kfbcmpgnoaljdhiekfbcmpgnoaljdhie
   ```

4. Run `applai serve` (port 8000: the extension asks there). WSL forwards Windows' `127.0.0.1` to it.
5. Sign in to JobTeaser in that Chrome as usual. Within a minute, `GET /api/browser/status` answers `"connected": true`.

A page the extension cannot read is reported to ApplAI and asked for again only after the next scan. After a second failure, the offer is triaged from its job alert, and its card says "full text not read". A form follows the same rule; after its second failure, the Applications dashboard asks for the form's URL, and a pasted one is read within a minute.

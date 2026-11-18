# The browser layer is a Chrome extension; Playwright is for the bench

Filling forms in a separate automated browser means logging in to every site again, losing the password manager, and failing captchas that pass in a normal browser. ApplAI therefore acts in the candidate's own Chrome through an ApplAI extension, behind a `BrowserDriver` interface: the extension reads forms, fills fields, attaches files, takes screenshots, blocks submission inside the page and detects the confirmation page, while the models stay in the backend. Playwright implements the same interface for the bench and CI, on local fake forms. The extension is also each user's local browser agent once ApplAI runs in the cloud.

## Considered Options

- **Playwright on a dedicated profile**: works today and is used until the extension lands, but every site needs its own first login and some captchas fail.
- **Driving the everyday Chrome profile over remote debugging**: not offered by Chrome on the default profile.
- **Claude in Chrome**: gives the agent general clicking, so blocking submission becomes an instruction instead of code, and it is tied to one Claude account.

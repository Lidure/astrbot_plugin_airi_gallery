# Cloud Gallery Anonymous Read Design

## Goal

Allow the Cloud Gallery page to browse public GitHub galleries without an access token while preserving write access for authenticated users. Add a one-click choice for the plugin's built-in default gallery, `Lidure/airi-gallery-images` on `main`, without removing support for custom repositories.

## Scope and compatibility

The change is limited to the Cloud Gallery page in `pages/zz_cloud/index.html` and its browser-side behavior. Existing localStorage keys and configuration fields remain unchanged, so existing saved configurations continue to load. GitHub public read behavior changes from token-required to token-optional. Gitee behavior remains token-required.

## Design

### Configuration

Add a default-gallery selector beside the repository fields. Selecting the built-in option fills platform, owner, repository, and branch with `github`, `Lidure`, `airi-gallery-images`, and `main`. The owner, repository, branch, and token remain editable, so users can select the default and then customize it or enter a repository directly.

The token label/help text explains that it is optional for public GitHub read-only access and required for all modifications. Existing token values are preserved when saving and loading.

### Authentication and request flow

`authHeaders()` and `authParams()` omit GitHub/Gitee credentials when no token is present rather than constructing an empty authorization header or query parameter. GitHub GET requests therefore use anonymous access for public repositories. Gitee still rejects a missing token before attempting a read.

Introduce a single write guard used by `putFile()` and `deleteFile()` and by the UI write handlers. The guard reports a clear read-only message and prevents any write request from being constructed. The request helper also rejects `POST`, `PUT`, and `DELETE` without a token, providing defense in depth if a future caller bypasses the UI guard. GET requests remain available without a GitHub token.

### UI state

Saving and testing a configuration require only the platform-specific repository fields needed to read it. Initial load and manual sync require owner/repository, not token. After a successful anonymous GitHub read, the page shows the gallery and marks the connection as read-only. Upload controls, upload actions, and delete controls are disabled or hidden with an adjacent explanatory message. With a token, the existing upload/delete controls remain enabled.

Connection feedback distinguishes anonymous read-only access from authenticated read/write access. Missing repository fields still produce the existing configuration prompt; missing token alone must not block a read.

## Data flow

1. Load saved configuration, merging the built-in default values as before.
2. Let the user select the built-in gallery or enter a custom repository.
3. Validate only required read fields, then save/test/sync.
4. Fetch the GitHub tree anonymously when no token is configured; fetch file bytes through the existing raw CDN/API fallback.
5. Render the gallery in read-only mode when no token is present.
6. Require a token in the UI and request layer before uploading or deleting.

## Testing

Add repository contract tests that inspect the cloud page for:

- the built-in default gallery selector and values;
- optional-token read validation and anonymous GitHub request headers/parameters;
- explicit write guards and write-method rejection without a token;
- initialization and sync paths that do not require a token;
- clear read-only messaging and write-control state handling.

Run the full existing pytest suite and any repository-provided static checks. If browser automation is unavailable, validate the JavaScript contract statically and report that limitation.

## Error handling

Public GitHub API failures retain the existing status/error handling. Authentication errors on authenticated requests keep their current messaging. Anonymous access failures are reported as read failures rather than as a missing-token validation error. All write attempts without a token receive the same explicit read-only/token-required message.

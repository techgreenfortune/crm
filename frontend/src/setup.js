// Side-effect-only setup that MUST run before any module that imports
// `composables/settings.js` (which fires createResource at module-eval time).
// Without this ordering, the resource falls back to the default `request`
// fetcher which doesn't prefix /api/method/ — calls resolve relative to the
// current page, Frappe returns the SPA HTML shell, JSON.parse fails, refs
// stay false, and UI gates like `callEnabled` never flip on.
import { setConfig, frappeRequest } from 'frappe-ui'

setConfig('resourceFetcher', frappeRequest)

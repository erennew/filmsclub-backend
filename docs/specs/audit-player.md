# UI/UX Audit — Player, Watch Flow, Mobile & Forms (CloudXMovies frontend)

Scope: `frontend-main/src/components/{InlinePlayer,Watch,PlayerModal,TrailerModal,AudioTracks,AudioTrackSelector,SubtitleSelector,MobilePlayBar,PWAInstallPrompt,Logout,signInWIthGoogle,PrivateRoute}.jsx`, `frontend-main/src/pages/{MobileDownloads,MobileProfile,OfflineFallback,Login,Token,MyList}.jsx`, `frontend-main/src/hooks/{useTouchGestures,useHapticFeedback,useOnlineStatus}.js`, `frontend-main/src/index.css`, `frontend-main/tailwind.config.mjs`.

Severity legend: **P0** user is blocked / sees nothing · **P1** major broken feature or page · **P2** real defect, workaround exists · **P3** polish / dead code.

---

## P0

### 1. Stream failure shows a black box — no error state, no retry
- **File:** `frontend-main/src/components/InlinePlayer.jsx:379-384`, `frontend-main/src/components/Watch.jsx:409-413`
- **What's wrong:** Neither player attaches an error listener to the Plyr instance or the `<video>` element. When a `/dl/...` link is dead (404/410/network drop/CORS), Plyr renders a black video with controls that do nothing. The user gets no message and no way to retry — the most important failure mode on a streaming site.
- **Fix:** subscribe to the video error and render an overlay:
  ```jsx
  useEffect(() => {
    const plyr = playerRef.current?.plyr;
    if (!plyr) return;
    const onErr = () => setStreamError(true);
    plyr.media.addEventListener("error", onErr);
    return () => plyr.media.removeEventListener("error", onErr);
  }, [sources]);
  // JSX: overlay when streamError
  {streamError && (
    <div className="absolute inset-0 grid place-items-center bg-carbon">
      <div>
        <p>This print failed to load.</p>
        <button onClick={() => { setStreamError(false); plyr.restart(); }}>Try again</button>
      </div>
    </div>
  )}
  ```
  Also add a "Retry" / "Switch quality" action rather than forcing a full page reload.

### 2. Watch popup renders an empty panel when no sources exist
- **File:** `frontend-main/src/components/Watch.jsx:409-413`
- **What's wrong:** The modal body only renders the player when `sources.length > 0`. If the API returns no sources (or the fetch fails at line 217-270), the modal opens with just Close/Minimize buttons over a black `bg-ink` panel — the user sees a blank screen with no explanation.
- **Fix:** add an `else` branch in the modal body: "No playable source — try another quality or check back later" plus a close affordance, and never open the modal when `sources.length === 0` without showing that state.

---

## P1

### 3. CSS `#player { transform: rotate(90deg) scale(1.75) }` portrait "fullscreen" hack
- **File:** `frontend-main/src/index.css:212-229`
- **What's wrong:** On `orientation: portrait` the rule rotates the element 90° and scales it 1.75× with CSS instead of entering real fullscreen. This does not resize the video to the rotated viewport (aspect ratio is wrong, content overflows/clips), breaks hit-testing: touch coordinates and Plyr control taps land at rotated offsets, and native controls misalign. `scale(1.75)` is an arbitrary magic number that is wrong for any viewport ≠ the one it was tuned for. It also currently matches nothing — `id="player"` appears nowhere in the source — so it is a latent landmine.
- **Fix:** delete the two `#player` blocks. For genuine portrait-to-landscape playback use the Fullscreen API (`plyr.fullscreen.enter()`) and, where supported, `screen.orientation.lock("landscape")` in the fullscreen-change handler (with `try/catch`, and `screen.orientation.unlock()` on exit).

### 4. Watch popup player has no volume/mute and no settings (quality/speed) control
- **File:** `frontend-main/src/components/Watch.jsx:307-316`
- **What's wrong:** `controls` lists `play-large, play, progress, current-time, duration, pip, fullscreen` but omits `mute`, `volume`, and `settings`. The popup player therefore has **no way to change volume or mute**, and `settings: ["quality","speed"]` (line 307) is unreachable because there is no settings gear. (The inline player is fine — it includes `mute`,`volume`,`settings`.)
- **Fix:** add the missing items:
  ```js
  controls: ["play-large","play","progress","current-time","duration","mute","volume","settings","pip","fullscreen"],
  ```

### 5. External subtitles are invisible in the Watch popup
- **File:** `frontend-main/src/components/Watch.jsx:400-405` and `:322`
- **What's wrong:** `switchSubtitleTrack` (lines 152-178) injects a native `<track>` and sets `mode = "showing"`, but the modal's `<style>` hides rendering with `video::-webkit-media-text-track-container { display: none !important }`, and `captions: { active: false }` disables Plyr's caption rendering. Result: selecting an external subtitle in the popup appears to do nothing (Chrome/WebKit).
- **Fix:** remove the `video::-webkit-media-text-track-container` rule and either let Plyr manage tracks (`captions: { active: true, update: true }` and pass `tracks` through `plyrProps.source`) or render the injected `<track>` via Plyr's API instead of DOM injection.

### 6. Mobile pages rely on undefined design tokens
- **File:** `frontend-main/src/pages/MobileDownloads.jsx` (e.g. `:98,:105,:120`), `frontend-main/src/pages/MobileProfile.jsx` (`:84,:91,:99`), `frontend-main/src/pages/OfflineFallback.jsx` (`:36,:48,:58`), `frontend-main/src/components/MobileMovieDetails.jsx`
- **What's wrong:** All four use `var(--bg-primary)`, `var(--bg-surface)`, `var(--border-color)`, `var(--text-primary)`, `var(--text-muted)`, `var(--text-secondary)`, `var(--accent-primary)`, `var(--bg-card)`. None of these variables are defined anywhere — `src/index.css` only defines `--ink`, `--carbon`, `--bone`, `--projector`, `--signal`, `--safe-top`, `--safe-bottom`. Arbitrary-value Tailwind classes like `bg-[var(--bg-primary)]` emit `background-color: var(--bg-primary)`, which computes to nothing; borders fall back to `currentColor`. The pages render off-brand/broken.
- **Fix:** define the tokens in `:root` in `index.css` (and map them in `.light`/`.dark`), or replace the arbitrary values with the existing Tailwind theme (`bg-ink`, `bg-carbon`, `border-bone/10`, `text-bone/50`, `bg-projector`).

### 7. `safe-area-top` class is used but never defined
- **File:** `frontend-main/src/pages/MobileDownloads.jsx:98`, `frontend-main/src/pages/MobileProfile.jsx:84`, `frontend-main/src/pages/OfflineFallback.jsx:36`
- **What's wrong:** `index.css` defines `--safe-top` and a `.safe-bottom` rule (lines 928-932) but no `.safe-area-top` rule. On notched iPhones in standalone mode, page content slides under the status bar/notch.
- **Fix:** add `.safe-area-top { padding-top: env(safe-area-inset-top, 0px); }` (or `padding-top: var(--safe-top)`).

### 8. MobileDownloads / MobileProfile are unreachable dead pages
- **File:** `frontend-main/src/App.jsx:62-80`, `frontend-main/src/components/Nav.jsx:17-22`, `frontend-main/src/components/MobileBottomNav.jsx:9-15`
- **What's wrong:** There is no route for `/downloads` or `/profile` (and `/history`, linked from MobileProfile.jsx:54, also has no route — it hits the `*` NotFound). Neither bottom nav lists Downloads or Profile. `OfflineFallback.jsx:114` navigates to `/downloads`, which resolves to the 404 page. These pages are effectively orphaned.
- **Fix:** either add routes (`/downloads`, `/profile`, `/history`) and nav entries, or delete the pages and fix the OfflineFallback link.

### 9. MobileProfile theme toggle does nothing
- **File:** `frontend-main/src/pages/MobileProfile.jsx:32-46`
- **What's wrong:** `toggleTheme` only adds/removes `light`/`dark` classes on `<html>` and writes `localStorage.theme`, but no stylesheet reacts to those classes (page colors come from the undefined vars in finding 6), so the toggle has no visible effect. The footer also hardcodes "Fyvio Streaming v1.0.0" (`:226`), which is not CloudXMovies.
- **Fix:** wire the class toggle to a real theme (define `:root`/`.light` token values, `darkMode:"class"` is already configured in `tailwind.config.mjs:95`) and fix the product name.

---

## P2

### 10. Audio/subtitle selector overlap on narrow screens
- **File:** `frontend-main/src/index.css:342-347` (`.audio-track-selector`, `right:60px`) and `:504-509` (`.subtitle-selector`, `right:180px`), mobile overrides `:476-501` / `:637-655`
- **What's wrong:** Both selectors are `position:absolute; top:10px` anchored by hardcoded `right` values. The buttons are ~120-150px wide (icon + flag + max-w 80px title + padding), so on a 320-360px viewport the audio button (right:50px on mobile) and subtitle button (right:140px) overlap. They also anchor to the nearest positioned ancestor — if none exists they position against the page, not the player.
- **Fix:** replace absolute offsets with a normal flex toolbar overlay inside the player container (`absolute inset-x-0 top-2 flex justify-end gap-2`), letting flexbox handle spacing and wrapping on narrow screens.

### 11. AudioTrackSelector / SubtitleSelector are not accessible menus
- **File:** `frontend-main/src/components/AudioTrackSelector.jsx:82-118`, `frontend-main/src/components/SubtitleSelector.jsx:81-130`
- **What's wrong:** The trigger `<button>` has no `aria-haspopup`/`aria-expanded`; the dropdown is a plain `<div>` with no `role="menu"`/`role="menuitemradio"`/`aria-checked`; no Escape-to-close; no arrow-key navigation; outside-close only listens to `mousedown` (misses touch-outside on some browsers). These two components are also imported nowhere (dead) while the live inline players duplicate the same pattern.
- **Fix:** add `aria-haspopup="listbox"`, `aria-expanded={isOpen}`, `role="listbox"` on the panel and `role="option"` + `aria-selected` on options; handle `Escape` and arrow keys; close on `pointerdown`/`focusout`.

### 12. InlinePlayer toolbar dropdowns have the same a11y gaps
- **File:** `frontend-main/src/components/InlinePlayer.jsx:244-266, 267-293, 300-321, 322-363`
- **What's wrong:** Audio and Subtitle triggers lack `aria-expanded`/`aria-haspopup`; panels lack menu semantics; no Escape handler; the Subtitle trigger's accessible name is just "CC" (line 316) with no text label; the outside-click handler (lines 187-198) uses the `click` event, so a touch scroll that ends outside won't reliably close.
- **Fix:** same treatment as finding 11 — add ARIA, Escape-to-close, and `role="menu"`/`role="menuitemradio"` with `aria-checked={index === currentTrack}`.

### 13. TrailerModal: missing dialog semantics and broken exit animation
- **File:** `frontend-main/src/components/TrailerModal.jsx:34-44, 54-60`
- **What's wrong:** `if (!isOpen) return null` (line 34) runs before `<AnimatePresence>`, so the exit animation never plays. The modal has no `role="dialog"`/`aria-modal`, no focus trap, and focus is not returned to the trigger on close. The close button at `-top-12` can be clipped on short viewports. `autoPlay` (line 69) plus the manual `.play()` (line 30) is a redundant double-start.
- **Fix:** move the guard inside `<AnimatePresence>` (`{isOpen && <motion.div ...>}`), add `role="dialog" aria-modal="true"`, trap focus, restore focus on unmount, and position the close button inside the panel.

### 14. Watch modal: no Escape, no scroll lock, no focus management
- **File:** `frontend-main/src/components/Watch.jsx:327-417`
- **What's wrong:** Unlike TrailerModal, the watch popup has no Escape handler, no `document.body.style.overflow` lock (iOS body-scroll bleed through the modal), no `role="dialog"`/`aria-modal`, no focus trap, and focus isn't returned to the "Watch" button that opened it. Backdrop click doesn't close. In mini-player mode the fixed close button can overlap the Plyr fullscreen button.
- **Fix:** add an Escape listener + body scroll lock + `aria-modal` dialog, move focus to the panel on open and back to the trigger on close, and use a portal with `inert` on the background (or a focus trap library).

### 15. PlayerModal close button silently forces the default player
- **File:** `frontend-main/src/components/PlayerModal.jsx:41-48`
- **What's wrong:** `handleClose` unconditionally calls `onSubmit("MX Player (free)")` and toasts "Default player is Set". The condition `if (!selectedPlayer || selectedPlayer)` is always truthy. Closing or pressing backdrop therefore persists a player the user never chose.
- **Fix:** closing should only call `onClose()`:
  ```jsx
  const handleClose = () => onClose();
  ```
  and keep explicit selection in `handlePlayerSubmit`.

### 16. Login inputs: no autocomplete, toggle not exposed to AT
- **File:** `frontend-main/src/pages/Login.jsx:154-183, 229-274`
- **What's wrong:** No `autocomplete` attributes (`email`, `current-password`, `new-password`, `given-name`, `family-name`) → no password managers/keyboard autofill. The eye toggle button has a static `aria-label="toggle password visibility"` and no `aria-pressed`, so screen-reader users can't tell the state.
- **Fix:** add `autoComplete` on each Input and use `aria-pressed={isVisible}` plus a dynamic label ("Show password" / "Hide password").

### 17. Login errors are toast-only; `error` state is dead code
- **File:** `frontend-main/src/pages/Login.jsx:34, 95-96, 119-120`
- **What's wrong:** `setError(error.message)` is written but `error` is never rendered. Failures surface only via `toast.error`, which defaults to top-right and can be off-screen on a phone. Errors are not announced in a `role="alert"` region near the form, and the raw `error.message` (e.g. Firebase `auth/invalid-credential`) is shown verbatim.
- **Fix:** render `{error && <p role="alert" className="text-signal">{friendlyMessage}</p>}` inside the form, and map Firebase codes to human-readable text.

### 18. Turnstile failure is silent
- **File:** `frontend-main/src/pages/Login.jsx:45-56, 63-67, 191`
- **What's wrong:** `window.turnstile.render` is called after a 1s `setTimeout` with no `error-callback`, `expired-callback`, or timeout callback. If the script is blocked (ad-blocker), the network is down, or `SITEKEY` is empty (default in `config.js`), the widget never appears and the only feedback is "Please complete the CAPTCHA." — the user sees a blank space and is stuck.
- **Fix:** add `error-callback`/`expired-callback`/`timeout-callback` that set a visible state ("Security check failed — retry"), and if `window.turnstile` never loads (e.g. after 5s) render a fallback message with a reload button.

### 19. PWA install prompt auto-fires on first visit
- **File:** `frontend-main/src/components/PWAInstallPrompt.jsx:89-93, 122-127`
- **What's wrong:** The install sheet opens automatically 4.5s (Android `beforeinstallprompt`) or 9s (iOS) after mount, interrupting first-time browsing. Persistence is otherwise correct (14-day `localStorage` dismissal, `DISMISS_KEY`).
- **Fix:** gate the auto-prompt on engagement (e.g. after the user has visited N pages / watched content, or a manual "Install app" button) instead of a page-load timer.

### 20. PWA install sheet is not a real dialog and can collide with the play bar
- **File:** `frontend-main/src/components/PWAInstallPrompt.jsx:247-256`
- **What's wrong:** `role="dialog"` is set but there is no `aria-modal`, no focus trap, no initial focus, and no Escape-to-close — the background stays interactive. The sheet bottom is `calc(var(--safe-bottom)+5.25rem)` and `MobilePlayBar` uses `+5.5rem` (MobilePlayBar.jsx:33); on a details page after a scroll both can be visible simultaneously and overlap (both `inset-x-3`, `max-w-md`, centered).
- **Fix:** add focus trap + Escape + `aria-modal`, and stack the two floating elements (or suppress the install sheet while the play bar is visible).

### 21. Track switching has no error path or timeout
- **File:** `frontend-main/src/components/InlinePlayer.jsx:103-184`, `frontend-main/src/components/Watch.jsx:120-201`
- **What's wrong:** `switchAudioTrack`/`switchSubtitleTrack` set `plyr.source` and wait for `once("loadedmetadata")`. If the ffmpeg remux URL fails (dead file, timeout), `loadedmetadata` never fires: the user sees the "remux active" hint forever, the old source is gone, and there is no fallback or retry. The external-subtitle branch also uses a magic 200ms `setTimeout` (InlinePlayer.jsx:154) to find the injected track, which races on slow network.
- **Fix:** attach an `error` listener + a 15s timeout; on failure restore the previous source and toast "Couldn't switch track — keeping current audio/subtitles."

### 22. MobileDownloads is a mockup with dead controls
- **File:** `frontend-main/src/pages/MobileDownloads.jsx:19-50, 74-88, 278-281`
- **What's wrong:** The three downloads are hardcoded demo data; `handlePauseResume`/`handleRetry` only flip a status string (`progress` never advances), and the "Watch" button has no `onClick`. The page presents a fake download manager as functional.
- **Fix:** wire it to a real download store, or clearly mark it as a demo; at minimum disable or hide "Watch" until playback is implemented.

---

## P3

### 23. OfflineFallback retry is a fake check and points at a dead route
- **File:** `frontend-main/src/pages/OfflineFallback.jsx:10-24, 114`
- **What's wrong:** `handleRetry` just waits 2s and re-reads `navigator.onLine` (no real network probe); `checkConnection` (lines 22-24) is defined but never used; "View Downloads" navigates to the nonexistent `/downloads` route (finding 8).
- **Fix:** probe a lightweight endpoint (`fetch(..., {method:"HEAD"})`) for retry, remove `checkConnection`, and fix/remove the downloads link.

### 24. Auth-related files are entirely commented out
- **File:** `frontend-main/src/pages/Token.jsx`, `frontend-main/src/components/Logout.jsx`, `frontend-main/src/components/signInWIthGoogle.jsx`, `frontend-main/src/components/PrivateRoute.jsx`
- **What's wrong:** All four files are 100% commented out, and `App.jsx:17-19,25` comments out the `Token`/`Login`/`PrivateRoute` routes and `AuthProvider`. The auth flow is silently disabled (no login entry point), and the dead files clutter the repo and diverge from the active code.
- **Fix:** either delete them or restore them with the fixes in findings 15-18.

### 25. useTouchGestures can't cancel page scroll and is unused
- **File:** `frontend-main/src/hooks/useTouchGestures.jsx:123-125, 40-52, 98-108`
- **What's wrong:** `touchmove` is registered `{ passive: true }`, so vertical swipes on the player scroll the page while also firing `onSwipeUp/Down` — conflicting behaviors. `onDoubleTap` doesn't suppress the browser's native double-tap zoom, so both the zoom and the callback trigger. The hook is imported nowhere, so these issues are latent.
- **Fix:** if it's meant for a video surface, use `touch-action: none` on the element and a non-passive `touchmove` with `preventDefault()` only when a swipe is detected; add `e.preventDefault()` on double-tap; otherwise delete the hook.

### 26. AudioTracks uses flag emoji as language identifiers
- **File:** `frontend-main/src/components/AudioTracks.jsx:6-20`, also `AudioTrackSelector.jsx:5-28`, `SubtitleSelector.jsx:5-28`
- **What's wrong:** Flags do not represent languages, and every Indian language maps to 🇮🇳 (Telugu, Tamil, Hindi, Kannada, Malayalam, Marathi, Gujarati, Punjabi all identical), making rows indistinguishable. Emoji flags also render inconsistently on Windows (two-letter country codes).
- **Fix:** show language name or ISO 639-1 code ("TE", "TA", "HI") instead of flags.

### 27. Invalid Tailwind utility and no-op menu items in MobileProfile
- **File:** `frontend-main/src/pages/MobileProfile.jsx:163, 183` (`active:scale-98`), `:48-81` (Privacy/Settings/Help `action: () => {}`)
- **What's wrong:** `scale-98` isn't in the default Tailwind scale (3.4 has 90/95/100/105/110…), so the press feedback silently no-ops. Three menu entries (Privacy, Help & Support, Settings) are buttons with empty actions — dead ends.
- **Fix:** use `active:scale-95` (or add `scale-98` to the config) and either remove dead entries or implement them.

### 28. Deprecated iOS detection and a beforeinstallprompt race
- **File:** `frontend-main/src/components/PWAInstallPrompt.jsx:23-29, 79-95`
- **What's wrong:** `navigator.platform` is deprecated (frozen in modern browsers), so iPadOS detection is fragile. `beforeinstallprompt` is only listened for inside a `useEffect` — if it fired before React mounted (possible with fast caching), the prompt is missed.
- **Fix:** use `navigator.userAgentData` / `maxTouchPoints` heuristics, and also check `window.__pwaInstallable` or re-emit when a deferred prompt arrives late.

### 29. Login layout can clip on short viewports
- **File:** `frontend-main/src/pages/Login.jsx:140`
- **What's wrong:** The container is `flex flex-col h-screen items-center` with no `overflow-y`. On short phones (or when the keyboard opens) the card with two tabs + Turnstile + Google button is taller than the viewport; the top (logo) or bottom gets clipped and is unreachable.
- **Fix:** use `min-h-screen` with `py-8` and `overflow-y:auto`, or center with `my-auto` inside a scrollable container.

### 30. MobileDownloads storage/online indicators are decorative and hardcoded
- **File:** `frontend-main/src/pages/MobileDownloads.jsx:52-53, 107-112, 122-131`
- **What's wrong:** `storageUsed`/`storageTotal` are hardcoded strings parsed with `parseFloat`; the Wi-Fi/offline icons have no text or `aria-label`, so online status is communicated only by color (green/red) — invisible to screen readers and color-blind users.
- **Fix:** compute storage from real data, and add `role="status"`/`aria-label` ("Online"/"Offline") to the status icon.

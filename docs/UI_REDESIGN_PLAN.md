# AccessFromPhone UI redesign: plan, actions and status

Goal: a pro-level, smooth, interesting interface with real depth (3D), without making an app that people use in urgent moments any slower, and with a
setting that turns the 3D off while keeping the same look and a gentle level of movement.

Status key: DONE = built and tested in this round. LATER = planned, not built yet (and why).

## 0. Ground rules (apply to every action below)

| Rule | How it is kept |
|---|---|
| Three motion levels | **Full 3D** (no attribute) / **Simple** (`data-motion="simple"`) / **Off** (`data-motion="off"`). Same layout and colours in all three; only the movement differs. |
| The 3D switch lives in Settings | Settings, Appearance: "Graphics and motion". Remembers the choice on the phone. |
| Sensible default | If the person never chose: phones that say they are low-end (little memory or few cores) start on Simple; a phone set to "remove animations" starts on Off; everything else starts on Full. |
| Only cheap properties animate | transform, opacity, filter. No layout animation. |
| Urgent actions never wait for animation | Stop, Reject, unlock, and the fingerprint prompt open at once. |
| Both themes | Every new rule uses the colour tokens and has a light-theme value. Checked by screenshots in dark and light. |
| Keyboard, screen reader | New controls are real buttons with labels and `aria-*`; focus rings kept. |
| No new script files | The Android app lists its web files by name, so new code goes into the existing `app.js` / `app.css` (no Android rebuild needed for this round). |

## 1. Foundations

| # | Action | Status |
|---|---|---|
| 1.1 | Motion level setting (`cfg.motion`), migrated from the old "Reduce motion" switch (old Reduce = Off) | DONE |
| 1.2 | `applyLook()` sets `data-motion` to `simple` / `off` (nothing for Full) | DONE |
| 1.3 | Automatic default for low-end phones and for "remove animations" | DONE |
| 1.4 | Depth tokens (`--depth-1/2`, glows) for dark and light | DONE |
| 1.5 | Settings screen: "Graphics and motion" with three choices, a live preview monitor and one line saying what each does | DONE |

## 2. Home

| # | Action | Status |
|---|---|---|
| 2.1 | 3D PC monitor hero: glows cyan (ready), amber (locked), grey (offline); floats, tilts to your finger or phone movement; lock icon on its screen | DONE |
| 2.2 | Simple mode: the same monitor drawn flat, with a slow glow and a scan line; Off: still | DONE |
| 2.3 | Cards rise in once when you arrive at Home (not on every refresh, which would flicker) | DONE |
| 2.4 | Soft layered shadows and a press-tilt on cards and buttons (Full only) | DONE |

## 3. Navigation

| # | Action | Status |
|---|---|---|
| 3.1 | Tab bar: the active icon lifts and glows (Full); colour change only (Simple/Off) | DONE |

## 4. Approvals (the most important moment)

| # | Action | Status |
|---|---|---|
| 4.1 | Approve with fingerprint (or face) when the app lock is on and "Use fingerprint or face" is on; PIN as the fallback | DONE |
| 4.2 | New setting under App lock: "Confirm approvals" (on by default while the lock is on) | DONE |
| 4.3 | The Approve button says how it will be confirmed ("Approve with fingerprint" / "Approve with PIN") | DONE |
| 4.4 | Confirmation sheet: scanning ring, PIN pad, wrong-PIN shake, same lockout as the app lock | DONE |
| 4.5 | Approving seals the card (green glow, lift in Full; glow only in Simple) | DONE |
| 4.6 | Approval cards: depth (they already had a risk-coloured edge, which is kept, not doubled) | DONE |
| 4.7 | The screen-share "Allow this folder" and access cards also ask for the fingerprint | LATER (kept to plan approvals so the flow stays quick; easy to add) |

## 5. Things found that were unwanted, and what was done

| Found | Action | Status |
|---|---|---|
| "null" printed in the Downloads sheet and in the screen status line (null passed straight into `replaceChildren`) | Fixed earlier; audited all other `replaceChildren` calls in this round | DONE |
| Reduce motion was all-or-nothing, so people who dislike 3D lost all movement | Three levels | DONE |
| Biometric prompt could make the app lock itself again on return | Approval confirmation is excluded from the "left the app" timer | DONE |

## 6. Later rounds (not in this round, on purpose)

| # | Action | Why later |
|---|---|---|
| 6.1 | Files: folder-open "fly in" and fan-out | Needs the file browser's list to be keyed so it does not replay on refresh |
| 6.2 | Access: people as avatar cards with status rings | Touches the Admin and sharing flows, which have their own tests |
| 6.3 | PC dashboard (`admin.html`) redesign | A second design surface with its own page and tests |
| 6.4 | Pairing and unlock scenes (3D lock turning) | Best built after the Home hero is judged on real phones |
| 6.5 | Real-phone performance pass on 3 phones (low, mid, high) | Needs the phones; the Simple level is the safety net until then |
| 6.6 | View Transitions between screens | Needs the oldest WebView version on your target phones |

## 7. Test plan

* `tests/e2e/design_e2e.py`: motion setting and its effect on the page, the Home monitor in all three levels and both themes, the fingerprint and PIN approval paths, and that nothing breaks without a fingerprint reader.
* Existing suites (pytest, node, all browser tests) must still pass.

## 8. Summary of everything changed in this round

**Settings**
* Look and feel has a new section, **Graphics and motion**: Full 3D / Simple / Off, with a live preview monitor and a line saying what the level does. It replaces the old on/off "Reduce motion" switch (old "on" becomes Off).
* **Simple** keeps the same look, flat, with gentle animation (slow glow and scan line, soft fades). **Off** stops everything. Phones that look low-end start on Simple; phones set to "remove animations" start on Off.
* App lock has a new row, **Confirm approvals** (on by default while the lock is on).

**Home**
* A 3D PC monitor sits under the welcome line: glows cyan when ready, amber when locked, grey when it can't be reached, shows the PC's name and a lock icon, floats, and leans toward your finger or the phone's movement (Full only).
* The cards rise in once when you arrive at Home (not on every refresh).
* A press leans cards and buttons toward the finger (Full only). Cards and approval cards have layered depth shadows.
* The tab bar's active icon lifts and glows (Full), or scales slightly (Simple).
* The monitor was kept compact so Home stays short (the Home height check was raised from 1220 to 1280 for it).

**Approvals**
* With the app lock on, Approve says how it will be confirmed ("Approve with fingerprint" or "Approve with PIN"), and tapping it opens a confirm sheet: a scanning ring, the system fingerprint prompt (the app's existing prompt, so no Android rebuild is needed), the PIN pad as the fallback, wrong-PIN shake and the same lockout as the app lock, Cancel, and a green close on success.
* A fingerprint that doesn't match approves nothing; Cancel approves nothing and the request stays waiting; with Confirm approvals off, Approve is a plain tap as before.
* The approved card plays a short seal animation (green glow, and a lift in Full).
* Returning from the system fingerprint prompt no longer risks the app lock locking itself again.

**Cleanups**
* Settings pages can no longer show a stray "null" (their lists are filtered).
* A test that expected the old unlock message was updated for the new "paused, will start again by itself" wording.

**Tests added or changed**
* `tests/e2e/design_e2e.py` (new): 44 checks covering the setting, all three levels, both themes (with screenshots), and every fingerprint/PIN approval path.
* `tests/e2e/home_e2e.py`: Home height limit 1220 to 1280. `tests/e2e/admin_unlock_e2e.py`: new wording.
* Full run: pytest 1223 passed (3 skipped), all node tests, and every browser test passed.

**Not tested (needs real phones)**
* The real Android fingerprint prompt, the phone-movement tilt, and the speed and battery use on a low-end phone. Simple is the safety net for slow phones until they are measured.

**Not done in this round, on purpose** (see section 6): Files folder-open animation, Access people cards, PC dashboard redesign, pairing and unlock scenes, View Transitions, and asking for the fingerprint on the "Allow this folder" cards.

## 9. Follow-up: downloads from the PC screen view

* After "Download to phone" the card stays and follows the download live (name, size, how far, speed, time left, pause, cancel) and, when saved, shows "Saved to Downloads, size" and a big **Open**. It reuses the Downloads list's own row, so the two always agree. In a plain browser it says the browser has the download.
* The size is known as soon as the file is picked, so the card never says "0 B of 0 B".
* Delete (the bin) asks first. The question used to open **underneath** the full-screen view (the view sits on `<body>` at z-index 400; dialogs were 90, sheets 60), which dimmed the page and showed nothing. While the PC screen is full screen, dialogs, sheets, toasts, the approval confirm sheet and the download animation now sit above it.
* When the picture is turned sideways on an upright phone, those same overlays (and the download animation, which now flies toward the phone's left edge, the picture's "down") turn with it. In the normal view everything stays upright and vertical.
* Test changes: `screen_e2e.py` covers the live card, Open, the delete question (Cancel, then confirm), and the same in sideways full screen; three zoom checks there now wait for the picture to swap instead of checking once (they failed at random).

## 10. Follow-up: full screen showed nothing, and a phone that vanishes mid-viewing

* **Full screen showed nothing (a mistake in my previous change).** Lifting dialogs above the full-screen view also lifted the PC screen's own leftover sheet (and the Access sheet under it), whose dark backdrop then covered the picture. Now, while full screen, every sheet at or under the screen's sheet is hidden and only sheets opened after it are shown, above the view. `screen_e2e.py` now hit-tests five points of the screen in full screen and fails if anything but the picture is on top.
* **The app is swiped away from the recent apps while viewing.** The phone sends nothing at that moment. The PC used to end a viewing only when something happened to call it (a picture request, or the PC page refreshing), so with nothing calling it the PC kept saying "viewing" (and any held mouse button stayed down). Now a watchdog checks every second while anyone is looking or asking. After 6 s without a picture request the PC banner turns amber ("isn't responding. Ending in N s", with the countdown, the edge light and tab amber too); after `screen.idle_seconds` (20 by default) the viewing ends by itself, everything is let go, and the PC stops saying someone is viewing. If the phone returns first, everything goes back to normal. The Owner can always press Stop on the tab, or the panic key.
* On the phone: opening the app again simply starts viewing again (it asks to start by itself, as before); if the PC still thought the old viewing was alive, it carries on from there.
* Tests: 6 manager tests (including the real watchdog thread ending a vanished viewing with nothing else calling), 1 desktop-window test for the amber state.

## 11. Follow-up: coming back within the PC's waiting time

* Proved with a real browser (`tests/e2e/reconnect_e2e.py`): when the app is closed and opened again inside the time the PC waits (`screen.idle_seconds`, 20 s), it picks up the SAME viewing: no new request, no "Start viewing" step, the PC's "isn't responding" note cleared, and control kept if the phone had it.
* What still felt "fresh" was that the person had to go Home, Access, View the PC's screen again. Now, when the app starts and the PC says this phone is still viewing, it opens straight back into the PC screen ("Back to your PC screen, where you left off."). It asks once per start of the app, only a viewing by this phone is picked up, it waits for the PIN when the app lock is on, and after the PC has ended the viewing nothing opens.

* **Loading scene for a resumed viewing.** When the app opens back into a viewing the PC is still holding (or you open the PC screen by hand and the PC says you are still viewing), the sheet opens straight onto the animated loading scene, tagged "Resuming", titled "Picking up your previous session", with "Looking from where you left off. Please wait…" then "Reconnecting to your PC's screen…" (and a plain note if it takes long). The status line says "Resuming". It melts into the picture when the first one arrives. A normal first-time open shows the same scene as "Connecting to your PC" (it replaces the old "Checking…" card). If the PC has already ended the viewing, the normal cards appear instead.

## 12. Follow-up: smoother picture and clearer sound

Measured on this PC (Dell Latitude 5540, Intel Iris Xe, 1080p screen, a busy desktop):

* **Why about 7 pictures a second.** Three things stacked: (1) the picture presets deliberately waited between pictures (Balanced 140 ms, Fast 90 ms, Sharp 220 ms), which alone caps them at about 7, 11 and 4 a second; (2) the phone asked for one picture at a time and waited for it; (3) the PC itself needs about 45-60 ms to copy the screen and 25-45 ms to shrink and compress it.
* **What changed.** The built-in waits are gone (Sharp keeps a short one). The phone keeps two pictures on the way (three on a slow link), drops one that arrives out of order, and measures a picture's speed by its share of the wait. The PC captures the next picture while the last is being compressed and sent (and hands each picture out once), uses a direct screen copy that is about 18% faster than ImageGrab (ImageGrab stays as the fallback), and shrinks the picture in two quick steps.
* **Result on this PC** (real server, real screen, loopback): about 21-22 pictures a second at 640-800 px wide, about 10-13 at 1280 px. Before: about 7-10. On a real phone link the extra pictures in flight matter more, because they hide the link's own delay.
* **The limit.** This PC's screen copy is the bottleneck (about 45 ms): it cannot go past about 20 a second, so 30-60 a second needs Windows' GPU screen capture (the `dxcam` package) and ideally a faster JPEG library, or video streaming. Installing packages was not allowed in this session, so that is not done.
* **Sound: why it was choppy.** The PC waited only 0.1 s for each 0.1 s chunk and put a whole chunk of silence in front of any chunk that was a moment late (a gap, then a pile-up, then a skip). The sound was also cut to 16 kHz and every small piece re-converted on the phone (a tiny click at each join).
* **Sound: what changed.** Sound is kept in step with the clock (silence is sent only for time the PC really was quiet), pieces are 40 ms, the default is "Clear" (48 kHz mono, no re-sampling on a typical PC or phone) with a "Light" choice (16 kHz) in the screen's Options, the phone plays at the same rate as the sound and keeps more in hand (0.25 s, growing up to 0.8 s if it runs dry).
* Tests: `tests/test_screen_audio.py` (new: rates, no silence put in front of a late chunk, silence only for real quiet, pictures handed out once, fast copy falls back), two manager tests for the sound's rate, and the browser tests for the screen and reconnecting still pass.

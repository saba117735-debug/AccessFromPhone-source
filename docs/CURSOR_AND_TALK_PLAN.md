# Cursor shape on the phone, and talking into a meeting from the phone

## Part 1: the pointer should look exactly like the PC's

**The problem.** On the PC the pointer changes with what is under it (arrow, text bar, hand, resize arrows, busy ring, custom shapes from apps). On the phone it was always the same arrow.

**How Windows lets us copy it.** `GetCursorInfo` says which cursor is showing right now (and whether it is hidden). The cursor's real picture can be drawn out of its handle, so any shape, including an app's own custom one, can be copied pixel for pixel (not just the standard list).

| Step | What | Where |
|---|---|---|
| 1 | Find the cursor showing now, and draw its real picture (with transparency and its "hot spot", the exact pixel that clicks). Cached per shape. | PC, `screen/capture.py` |
| 2 | While only looking, the PC draws THAT picture into the screen image instead of a fixed arrow (so it follows the real pointer exactly). Falls back to the arrow if anything fails. | PC, `grab_jpeg` |
| 3 | While controlling, the phone draws its own pointer (it follows the finger at once). Each picture says which shape is showing (`X-Cursor`); when it changes the phone fetches that shape (`/screen/cursor`) and swaps its pointer for it. | PC endpoint + phone |
| 4 | A hidden cursor (the PC hides it while typing or in a video) is hidden on the phone too. | both |

Safety: the phone can only fetch shapes the PC has actually shown (an unknown id is refused), and only while viewing.

## Part 2: talking into a Teams meeting from the phone

> **REMOVED from the application on 2026-11-20 (on request).** The complete code, with its tests, is kept separately in the folder `AccessFromPhone-talk-backup-2026-11-20` next to the app (in your user folder). `TALK-README.txt` there says what it contains and how to put it back. What is below is the plan as it was; none of it is in the app now.

**Is it reachable?** Yes, with one condition. The phone's voice can be sent to the PC, but a meeting app takes its microphone from a Windows **recording device**, and Windows has no built-in way to make one from sound the PC plays. A **virtual audio cable** (a small driver, for example the free VB-Audio Virtual Cable) adds a fake microphone: the PC plays the phone's voice into the cable's "input", and Teams listens to the cable's "output" as its microphone.

**What this needs from the person**
1. Install a virtual audio cable on the PC once. It is a driver, so it needs an administrator (on a locked-down work PC this is a request to IT).
2. In Teams, Settings, Devices, Microphone: choose the cable's output (for example "CABLE Output").
3. On the phone: look at the PC screen, take control, tap **Talk**.

Listening already works (the PC's sound streams to the phone, see "Sound" in the screen's Options). With the cable, the phone's own voice goes to the cable, not to the speakers, so the phone does not hear itself.

| Step | What | Where |
|---|---|---|
| 1 | Find the virtual cable among the PC's playback devices (or the one named in `screen.mic_device`). Say plainly if there is none and how to set it up. | PC, new `screen/mic.py` |
| 2 | Phone: ask for the microphone (the Android app already allows this, so no rebuild), turn it into 16 kHz mono, send it in 100 ms pieces, in order. | phone, `screen.js` |
| 3 | PC: receive the pieces, smooth out the timing, convert to what the cable wants, play. | PC |
| 4 | Always visible and always stoppable: the PC banner says "<phone> is speaking through this PC" in red; the phone shows a live mic; Stop on the PC ends it. | both |
| 5 | Same rules as control: needs Operate level, the phone's turn, and the phone has to be controlling. The Owner can switch it off (`screen.mic: false`). It stops by itself when control ends, the viewing ends, or the pieces stop coming. Recorded in the activity log (that it started and stopped, never the sound). | PC |

**Not built (and why).** Playing the voice through the PC's speakers so the PC's own microphone picks it up works without a driver, but the phone would hear its own voice back (the PC's sound is streamed to it), the quality is poor and Teams' echo removal fights it. It is left out on purpose. The other fully supported route needs no app of ours: join the meeting with the Teams app on the phone.

## What would still need doing by hand
* The virtual audio cable (driver) install, and choosing it as the Teams microphone.
* Hearing it on a real phone and a real meeting: the tests use a stand-in microphone and a stand-in playback device.

## What was built

* **Pointer shape:** `screen/capture.py` (`cursor_handle`, `render_cursor`, `draw_cursor`), `/api/screen/cursor`, the `X-Cursor` header on every picture, and the phone's own pointer taking the shape. Tested against all eight standard Windows shapes (arrow, text bar, hand, busy ring, cross, resize, move, not-allowed) with their hot spots, the hidden pointer, the fallback arrow, and the rule that only shapes the PC has shown can be fetched.
* **Talk:** built and tested, then **removed from the application** on request. The complete code is in the backup folder named above.
* **Never tested on real hardware (Talk):** the virtual audio cable itself, a real meeting, and a real phone microphone.

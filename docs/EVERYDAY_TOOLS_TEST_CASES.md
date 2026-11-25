# Everyday Tools: Manual Test Cases

Covers the **Control centre** card on Home (volume and brightness sliders, media keys and the tools below) and the new look and gestures: Sliders, Inbox, Notes, Remind, Watch, Recent and pinned, Print, Snap, PC alerts, Briefing, plus access levels, settings, security and display.

How to use: work through a section, write **P** (pass), **F** (fail) or **N/A** in the *Result* column, and note anything odd in *Notes*. Every test says what to do and what you should see.

---

## 0. Before you start

### 0.1 What you need
| Item | Why |
|---|---|
| The PC running AccessFromPhone (`.\run.ps1`) | Everything |
| A paired phone (the **first** phone paired is the Admin) | Main tester |
| A **second** paired phone or browser tab, set to a lower level | Access-level and "one person at a time" tests |
| Edge or Chrome on the phone and the PC | Browser checks |
| A printer, or "Microsoft Print to PDF" as the default printer | Print tests |
| A laptop (for brightness), and a desktop if you have one | Brightness tests |
| Some media playing on the PC (YouTube, Spotify, VLC) | Media key tests |
| Test files: a small `.txt`, a `.pdf`, a `.png`, a 5 MB file, a 150 MB file, and a file named `setup.exe` (any content) | Inbox, Print, Watch |
| `curl` (or PowerShell `Invoke-RestMethod`) | The API-level and security tests, including odd file names that Windows won't let you create (`..\..\evil.txt`) |

### 0.2 Settings used (in `config.yaml`, restart after changing)
```yaml
power:
  remote_actions: [lock, screen_off, sleep]
daily:
  enabled: true
  max_upload_mb: 100
  printing: true
  notifications: false
  webcam: false
```
Some tests ask you to change these; each says so.

### 0.3 Levels
| Level | Can do in Everyday tools |
|---|---|
| **View only** | Look at lists. Nothing that changes anything. |
| **Read and write** (editor) | Send text/files to the inbox, remove inbox items, save notes, pin places, hide PC alerts |
| **Operator** (the default for new phones) | All of the above, plus quick controls, reminders, watchers, print, screenshot, PC clipboard, open link on PC |
| **Admin** (first phone) | All of the above, plus webcam, and scheduling hibernate/restart/shutdown |

Change a phone's level on the PC page (Phones section) or in the phone's **Access** tab as the Admin.

### 0.4 Useful places on the PC
Default folder: `C:\Users\<you>\Documents\AccessFromPhone\` containing `Inbox`, `To phone`, `Notes`, `Screenshots`.
Activity log: the PC page, **Activity** section.

---

## 1. The card on Home

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| HC-01 | Card appears | Open the phone page, stay on Home | A **Control centre** card sits under the PC card (Lock / Screen off / Sleep, readings). It has a Volume slider, a Brightness slider, three media keys (previous, play/pause, next) and tiles: Inbox, Notes, Remind, Watch, Recent, Snap, Print, Briefing. The page behind it has a soft coloured glow and the card looks frosted | | |
| HC-02 | Tile list is right | Look at the tiles | Exactly: Inbox, Notes, Remind, Watch, Recent, Snap, Print, Briefing. No **PC alerts** tile while `notifications: false` | | |
| HC-03 | Sliders show the PC's levels | Look at Volume and Brightness | Each shows the PC's real percentage after a moment (a grey shimmer shows while it loads). A desktop monitor's Brightness says "Not on this screen" and cannot be moved | | |
| HC-04 | Tap sizes | Try each tile, slider and media key with a thumb | Each is easy to hit (at least 44 px tall and wide) | | |
| HC-05 | Appears only after PC answers | Open the page with the PC just started | Card shows once Home has loaded; no error toast | | |
| HC-06 | Hidden when helpers off | Set `daily: enabled: false`, restart, reload the phone page | No Control centre card. Rest of Home is unchanged | | |
| HC-07 | Hidden when PC offline | Stop the app on the PC, wait for the "can't reach your PC" state | Card is not shown while offline. Saved chats still readable | | |
| HC-08 | Returns after reconnect | Start the PC app again | Card comes back by itself (may take a few seconds) | | |
| HC-09 | Not loaded when off | With `enabled: false`, open the browser's network tab and reload | `daily.js` and `daily.css` are **not** requested | | |
| HC-10 | Printing off | Set `printing: false`, restart, reload | **Print** tile is gone; all other tiles stay | | |
| HC-11 | PC alerts on | Set `notifications: true`, restart, reload | **PC alerts** tile appears (after Print) | | |
| HC-12 | Controls hidden for lower levels | Use a **View only** or **Read and write** phone | Sliders are greyed and cannot be moved; the media keys are hidden. Tiles still show | | |
| HC-13 | Badges: inbox | Send one text to the inbox, drop one file in `To phone`, wait up to 20 s or reload | Inbox tile shows a number badge (items to PC + files to phone) | | |
| HC-14 | Badges: remind and watch | Add one reminder and one watcher | **Remind** shows 1, **Watch** shows 1. Badge hides when the count is 0 | | |
| HC-15 | Home keeps working | Tap Lock/Sleep, open Files, open Settings and come back | Control centre card still there; no duplicates, no flicker | | |
| HC-16 | Live readings still update | Watch the Processor/Memory tiles for 10 s | They change without the card being rebuilt (the open sheet does not close) | | |
| HC-17 | Back button | Open any tool sheet, press the phone's Back | Sheet closes, you stay on Home | | |
| HC-18 | Close button / outside tap | Open a sheet, tap **X**; open again, tap the dark area outside | Both close the sheet | | |

---

## 2. Control centre (sliders and media keys)

Sliders and media keys need **Operator** level. A slider is a glass bar: drag it (or tap a spot) to set the level; the number inside follows your finger.

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| QC-01 | Volume slider | Play music. Drag **Volume** from about 20% to 70% | The bar follows your finger. The PC's volume follows while you drag (a few updates a second) and ends at exactly the shown number. Windows' own volume bar agrees | | |
| QC-02 | Tap a spot | Tap near the right end of the Volume bar | Jumps there and the PC follows | | |
| QC-03 | Real level on open | Change the volume on the PC itself, then reload the phone page | The slider shows the PC's real level, not a guess | | |
| QC-04 | Mute | Tap the speaker icon on the Volume bar | Sound stops, the bar greys, the text says **Muted**, the icon changes. Tap again: sound returns, same level | | |
| QC-05 | Drag while muted | Mute, then drag the slider | It unmutes as you move it | | |
| QC-06 | Play / pause | Play a song. Tap the large round **Play** key | The key lights briefly. Music pauses. Tap again: resumes | | |
| QC-07 | Previous / next | Play a playlist. Tap **Next**, then **Previous** | Skips forward, then back | | |
| QC-08 | No player running | Close all media players. Tap **Play** | Nothing breaks | | |
| QC-09 | Brightness slider (laptop) | Drag **Brightness** to 40% and let go | The screen dims to about 40% **when you let go** (it takes about a second). The bar shows 40% | | |
| QC-10 | Brightness floor | Drag Brightness to the far left | Stops at 5%. Never black | | |
| QC-11 | Desktop monitor | On a desktop PC look at Brightness | Says "Not on this screen"; greyed out; nothing happens when touched. Volume still works | | |
| QC-12 | Brightness while blacked out | Use **Screen off** (approve it). Then move Brightness | Error toast: "The screen is blacked out. Turn it back on first, then change the brightness." Volume still works | | |
| QC-13 | Failed change slides back | Stop the PC app mid-drag, let go | Error toast; after reconnect the slider shows the PC's real level again | | |
| QC-14 | Haptic | Release a slider and tap a media key on a phone with vibration on | Short tick; error buzz on failures | | |
| QC-15 | Activity log | After QC-01, open the PC page → Activity | A "quick_control" entry with the action and the phone's name | | |
| QC-16 | View-only phone | On a View-only phone | Sliders greyed and cannot move; media keys hidden. (API: `POST /api/daily/volume` and `/api/daily/control` return 403 "level") | | |
| QC-17 | Read-and-write phone | On an editor phone | Same as QC-16 | | |
| QC-18 | Pause | Press **Pause** on the PC page, then touch a slider on another phone | The phone is told it's paused (HTTP 423); nothing changes on the PC | | |
| QC-19 | Open usual folder (API only) | `POST /api/daily/open-place {"key":"downloads"}` | The Downloads folder opens in Explorer on the PC. Message "Downloads is open on the PC." | | |
| QC-20 | Open-place bad names | Same call with `"key":"C:\\Windows"`, `".."`, `""`, `"music"` (if no Music folder) | Each returns 422 with a message; nothing opens | | |
| QC-21 | Unknown control | `POST /api/daily/control {"action":"format_c"}` and `{"action":"mute","steps":99}` | Both 422. Nothing happens | | |
| QC-22 | Volume API | `GET /api/daily/volume`, then `POST /api/daily/volume {"level":150}` and `{"level":-5}` | First returns `{available, level, muted}`; the others return 422. `{"level":40}` sets 40% | | |
| QC-23 | Brightness API | `POST /api/daily/brightness {"level":101}`, then `{"level":0}` | The first is 422. The second is accepted but raised to the 5% floor (never black) | | |
| QC-24 | Sliders follow the PC | With Home open on the phone, change brightness (or turn on automatic brightness) and the volume on the PC itself | Within about 10 seconds both sliders move to the PC's real levels, without you touching the phone. While you are dragging one, it does not jump | | |

---

## 3. Inbox

Open **Inbox**. Two tabs: **To the PC (n)** and **To this phone (n)**.

### 3.1 Sending text and links to the PC

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| IN-01 | Send text | Type `buy milk`, tap **Send** | Toast "Sent to the PC". Item appears at the top with "just now" and your phone's name | | |
| IN-02 | Empty text | Leave box empty or only spaces, tap **Send** | Nothing is sent; the box gets focus | | |
| IN-03 | Long text | Paste 20,000 characters, send | Sent. Item shows a trimmed heading and the body below | | |
| IN-04 | Too long | Paste 20,001+ characters (box stops at 20,000) or send via API | API returns 422 "That text is too long (up to 20000 characters)." | | |
| IN-05 | Multi-line text | Send three lines | Line breaks are kept when you view/copy it | | |
| IN-06 | Link recognised | Send `https://example.com/page?a=1` | Item shows a globe icon, buttons **Open here** and (Operator) **Open on PC** | | |
| IN-07 | Not a link | Send `example.com`, `ftp://x.com`, `javascript:alert(1)`, `file:///C:/x`, `https://a.com and more` | Each is stored as **text** (no Open buttons) | | |
| IN-08 | Special characters | Send `<b>hi</b> & "quotes" 😀` | Shown exactly as typed; nothing is rendered as HTML | | |
| IN-09 | Copy on the phone | Tap **Copy** on a text item | Toast "Copied". Pasting elsewhere on the phone gives the text | | |
| IN-10 | Copy a long item | Send a 5,000-character text, tap **Copy** | The full text is copied, not just the preview | | |
| IN-11 | To PC clipboard | Tap **To PC clipboard** (Operator), then press Ctrl+V on the PC in Notepad | Toast "On the PC's clipboard". Notepad pastes the text | | |
| IN-12 | Clipboard log | Check Activity | An "inbox_to_clipboard" entry with a character count, **not** the text | | |
| IN-13 | Open here | Tap **Open here** on a link | The link opens in a new tab on the phone | | |
| IN-14 | Open on PC | Tap **Open on PC** | A confirmation shows the link. Confirm → the PC's browser opens it. Cancel → nothing happens | | |
| IN-15 | Open on PC only for links | API: `POST /api/daily/inbox/<id of a text item>/open` | 422 "That item is not a web link." | | |
| IN-16 | Remove item | Swipe the row left and tap **Delete** (or swipe all the way) | Item disappears. Doing it again via API returns 404 | | |
| IN-17 | Newest first | Send A, then B | B is above A | | |
| IN-18 | Limit on stored items | (Optional, API) send 305 short texts | The list shows 100; at most 300 are kept; oldest are dropped | | |
| IN-19 | Survives restart | Restart the PC app | Text items are still there | | |

### 3.2 Sending files to the PC

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| IN-20 | Send a small file | Tap **Send a file…**, choose a `.txt` | "Sending name (size)…" then toast "Sent name". Item shows size. File exists in `…\AccessFromPhone\Inbox\` as `<id>_<name>` | | |
| IN-21 | Content intact | Open the saved file on the PC | Byte-for-byte the same as the original | | |
| IN-22 | Photo from camera roll | Choose a phone photo | Arrives; opens correctly on the PC | | |
| IN-23 | 5 MB file | Send it | Arrives; no timeout | | |
| IN-24 | Over the limit (phone check) | Set `max_upload_mb: 1`, restart. Pick a 5 MB file | Toast "<name> is larger than the 1 MB limit." Nothing is uploaded | | |
| IN-25 | Over the limit (server check) | `curl -X PUT` a 2 MB body to `/api/daily/inbox/file?name=big.bin` with the limit at 1 MB | 413 with "larger than the 1 MB limit". Inbox folder holds **no** partial file | | |
| IN-26 | Limit without Content-Length | Stream a body bigger than the limit using chunked transfer | 413; no partial file left | | |
| IN-27 | Empty file | Send a 0-byte file (or API with empty body) | Refused: "That file is empty." Nothing saved | | |
| IN-28 | Program marked | Send `setup.exe`, then `.ps1`, `.bat`, `.lnk` | Item shows an amber **program** chip | | |
| IN-29 | Same name twice | Send `a.txt` twice | Two separate items and two files (each has its own id prefix); nothing overwritten | | |
| IN-30 | Odd names | Send a file named `what?.pdf` (rename on phone if possible), `CON.txt`, a 200-character name | Saved with safe names (`what_.pdf`, `_CON.txt`, shortened with extension kept) | | |
| IN-31 | Path tricks (API) | `PUT …/file?name=..%5C..%5C..%5CWindows%5Cevil.bat` | 200 but saved as `evil.bat` **inside** `Inbox`; nothing written elsewhere. Marked as program | | |
| IN-32 | Connection drops | Start a large upload, turn off phone Wi-Fi midway | Error toast; no half file left in `Inbox` | | |
| IN-33 | Save an inbox file back | Tap **Save** on a file item | File downloads to the phone with its original name | | |
| IN-34 | Remove file item | Swipe a file item left and delete it | Item gone **and** the file deleted from `Inbox` | | |
| IN-35 | Activity log | After sending | "inbox_received" entries with kind, name and size | | |
| IN-36 | Server never runs files | Send `setup.exe` | Nothing starts on the PC | | |

### 3.3 Files from the PC to the phone

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| IN-40 | Empty state | Open **To this phone** with an empty `To phone` folder | "No files waiting." and the folder path is shown | | |
| IN-41 | File appears | Copy `menu.pdf` into `To phone` on the PC, reopen the sheet | Listed with size and "just now" | | |
| IN-42 | Save it | Tap **Save** | File downloads with its name; content identical | | |
| IN-43 | Hidden files skipped | Put `.secret` and `~$temp.docx` in the folder | Not listed | | |
| IN-44 | Sub-folders skipped | Make a sub-folder inside `To phone` | Not listed; files inside it not reachable | | |
| IN-45 | Newest first | Add two files a minute apart | Newer file is first | | |
| IN-46 | Program warning | Put `tool.exe` in the folder | Listed with "program: be careful" | | |
| IN-47 | Download limit | Set `agent.max_download_mb: 1`, put a 5 MB file in | Saving gives an error mentioning the limit | | |
| IN-48 | Name tricks (API) | `GET /api/daily/tophone/file?name=../secret.txt`, `..\secret.txt`, `menu.pdf:stream`, `C:\Windows\win.ini`, empty | Every one returns 404. Nothing outside the folder is returned | | |
| IN-49 | File deleted meanwhile | List, delete the file on the PC, tap Save | Clear error; no crash | | |
| IN-50 | Log | After a save | "tophone_download" entry in Activity | | |

### 3.4 Inbox and access levels

| ID | Test | Expected | Result | Notes |
|---|---|---|---|---|
| IN-60 | View-only phone | Can open Inbox and read items and save files. Sees "Sending needs editor access…" note. API send returns 403 | | |
| IN-61 | Editor phone | Can send text/file and remove. **Cannot** use To PC clipboard or Open on PC (buttons absent; API 403) | | |
| IN-62 | Operator phone | Everything including clipboard and Open on PC | | |
| IN-63 | Someone else's turn | While phone A holds the turn, phone B (operator) tries to send text | 409 "A's phone is using this PC. Ask them for a turn." Reading still works | | |

---

## 4. Notes

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| NT-01 | Save a note | Open **Notes**, type `call the bank`, **Save** | Toast "Note saved on the PC". It appears under **Today** with the time | | |
| NT-02 | File created | Check `Documents\AccessFromPhone\Notes\` | `Notes YYYY-MM-DD.md` with the line `- HH:MM call the bank` | | |
| NT-03 | To-do | Tick **To-do**, type `send invoice`, Save | Toast "To-do saved on the PC". Shown with a tick icon. File line is `- [ ] HH:MM send invoice` | | |
| NT-04 | Several in a day | Save three notes | All in the same day file, appended in order; list shows newest first | | |
| NT-05 | Multi-line note | Type two lines | File keeps the second line indented under the same item; list shows both lines as one note | | |
| NT-06 | Empty note | Press Save with nothing typed | Nothing saved; box focused | | |
| NT-07 | Long note | 5,000 characters | Saved. 5,001 via API → 422 "too long" | | |
| NT-08 | Special characters | `<script>alert(1)</script> 😀 "quotes"` | Shows as text; no script runs | | |
| NT-09 | Day headings | Create files dated yesterday and last week (edit file names by hand) | Headings read **Today**, **Yesterday**, then e.g. "Tue 28 Apr" | | |
| NT-10 | Only 14 days | Make 20 daily files | Only the newest 14 days are shown | | |
| NT-11 | Open in Notepad | Open the `.md` on the PC | Plain readable text | | |
| NT-12 | Dictate (secure link) | On an https link in Chrome, tap **Dictate**, speak, tap again | Words appear in the box; stopping keeps them; Save stores them | | |
| NT-13 | Dictate (not secure) | Open over plain http | Toast explains dictation needs a secure connection; typing still works | | |
| NT-14 | Dictate: mic denied | Deny microphone permission | Toast "The microphone isn't available." | | |
| NT-15 | Dictate then save | Start dictation and press Save without stopping | Dictation stops, text saved | | |
| NT-16 | Log privacy | Check Activity after saving | "note_saved" shows todo flag and a character count, **not** the note text | | |
| NT-17 | View-only phone | Sees notes; Save is refused with a note about editor access (API 403) | | |
| NT-18 | Survives restart | Restart PC app | Notes still listed (they are files) | | |
| NT-19 | Different language | Set the dictation language in Settings to Hindi; dictate | Recognised in that language | | |

---

## 5. Remind (reminders and scheduled actions)

Need **Operator**. Open **Remind**.

### 5.1 Reminders

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| RM-01 | Quick time | Choose **Remind me**, type `stretch`, tap **In 10 min**, **Add** | Toast "Scheduled for today H:MM am/pm". Listed with the time | | |
| RM-02 | It fires | Use a time 1–2 minutes ahead (pick with the date box) and wait | Within about 5–20 s of the time, a toast "Reminder: stretch" appears on the phone, with a vibration. A push arrives if the page is closed | | |
| RM-03 | Locked phone | Lock the phone before it fires | Push notification "Reminder" arrives. Body says "Tap to see it." (or the text if `push.show_details: true`) | | |
| RM-04 | Gone after firing | After RM-02 | The task disappears from the list | | |
| RM-05 | Quick time buttons | Look at the chips | **In 10 min**, **In 1 hour**, **This evening** (only if it's more than 15 min before 6 pm), **Tomorrow 8 am** | | |
| RM-06 | Pick a date and time | Use the date box for tomorrow 9:30 | Listed as "tomorrow 9:30 am" | | |
| RM-07 | Past time | Pick a time that has passed | Error "That time has already passed." | | |
| RM-08 | No time | Press Add without choosing | Toast "Pick a time." | | |
| RM-09 | No text | Remind me with empty text | Toast "Say what to be reminded about." | | |
| RM-10 | More than a year ahead | Choose a date 2 years away | Error "Pick a time within the next year." | | |
| RM-11 | Invalid date (API) | `due` as NaN / Infinity / a string | 422; nothing saved | | |
| RM-12 | Long text | 300 characters | Trimmed to 120 characters | | |
| RM-13 | Repeat: daily | Set a reminder 1 min ahead, repeat **Every day** | Fires once now; stays in the list with a new due time tomorrow at the same clock time; "last: done" is shown | | |
| RM-14 | Repeat: weekdays | Set for Friday, repeat **Weekdays**, let it fire (or test by changing the PC clock) | Next due is Monday | | |
| RM-15 | Repeat: weekly | Repeat **Every week** | Next due is the same day next week | | |
| RM-16 | Delete | Tap the bin | Task removed. Deleting twice via API → 404 | | |
| RM-17 | PC was off | Schedule a reminder 3 minutes ahead, close the app, start it again 15 minutes later | Reminder arrives marked **(late)**. A repeating one skips to the next future time and sends only **one** late reminder | | |
| RM-18 | Survives restart | Schedule for 1 hour ahead, restart the app | Still listed, still fires | | |
| RM-19 | Limit | Add 50 tasks, then a 51st | Error "There are already 50 scheduled tasks. Remove one first." | | |
| RM-20 | Two at once | Two reminders due the same minute | Both arrive | | |
| RM-21 | Phone time zone | Set the phone to another time zone and schedule 10 minutes ahead | Fires 10 minutes later regardless (time is sent as an absolute moment) | | |
| RM-22 | Activity log | After adding/removing/firing | `task_scheduled`, `task_removed`, `reminder_fired` entries | | |
| RM-23 | Toast while on another screen | Fire a reminder while you are in Files or Chat | Toast still appears (the phone checks every 15 s) | | |
| RM-24 | App in background | Switch the phone page to the background | No toast spam; push arrives instead | | |

### 5.2 Scheduled power actions

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| RP-01 | Options match config | Open the "What to schedule" menu | Remind me, Lock this PC, Black out the screen, Put this PC to sleep. **No** Shut down/Restart/Hibernate (not in `remote_actions`) | | |
| RP-02 | Confirmation | Pick **Lock this PC**, a time 2 min ahead, **Add** | A dialog "Lock this PC today … ?" explaining it will happen by itself and is skipped, not done late, if the PC is off. **Cancel** adds nothing; **Schedule it** adds | | |
| RP-03 | Lock fires | Wait for the time | PC locks. A push/toast "Scheduled action: Doing it now: lock." arrives just before | | |
| RP-04 | Sleep notice | Schedule **sleep** 2 min ahead | Phone gets "Your PC is going to sleep" before the PC sleeps | | |
| RP-05 | Black out screen | Schedule **Black out the screen** | Screen goes black at the time; phone still sees/controls the desktop | | |
| RP-06 | Missed power action | Schedule lock 5 min ahead, close the app for 15 min, reopen | PC is **not** locked late. Phone gets "A scheduled action was missed" | | |
| RP-07 | Action removed from config | Schedule sleep, then remove `sleep` from `remote_actions` and restart, let it come due | It is **not** run; task result shows "switched off" | | |
| RP-08 | Switched-off action (API) | `POST /api/daily/tasks {"kind":"power","action":"shutdown",…}` while shutdown isn't allowed | 422 "switched off… add it to power.remote_actions" | | |
| RP-09 | Admin-only action | Add `shutdown` to `remote_actions`. On an Operator phone try to schedule it | 403 "needs admin access" | | |
| RP-10 | Admin schedules shutdown | On the Admin phone | Accepted; dialog shows; PC counts down 15 s at the time (cancel with `shutdown /a`) | | |
| RP-11 | Repeating power action | Schedule lock **Every day** | Locks once, then is listed for tomorrow | | |
| RP-12 | Activity log | Check | `task_scheduled` (with action and time), `scheduled_power` when run, `scheduled_power_missed` when skipped | | |
| RP-13 | Failure isolation | If one task fails, others still run | Next task fires normally | | |

---

## 6. Watch ("Tell me when…")

Need **Operator**. Open **Watch**. Three kinds: **A program**, **A download**, **PC goes quiet**. Tap **Watch something** to open the form.

### 6.1 A program

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| WP-01 | List loads | Choose **A program** | Up to 14 running programs as buttons (biggest memory first, "×N" for several copies) | | |
| WP-02 | Watch Notepad | Open Notepad, choose `notepad.exe`, leave "Then: just tell me", tap **Watch** | Toast "I'll tell you". Listed as "notepad.exe to finish" | | |
| WP-03 | Fires | Close Notepad | Within about 10–20 s a toast/push "Finished: notepad.exe has finished." The watcher disappears | | |
| WP-04 | Restart is not "finished" | Close and reopen Notepad within a few seconds | No alert (it needs to stay gone for two checks) | | |
| WP-05 | Nothing chosen | Tap **Watch** with nothing picked | Toast "Pick one first." | | |
| WP-06 | Not running (API) | Watch `ghost.exe` | 422 "ghost.exe is not running now." | | |
| WP-07 | Bad names (API) | `""`, `..\x.exe`, `a*b`, 101 characters | Each 422 | | |
| WP-08 | Several copies | Open 3 Chrome windows, watch `chrome.exe` | Fires only when **all** copies have closed | | |
| WP-09 | Process list is for Operators | View-only/editor phone | API 403; UI shows lock note | | |

### 6.2 A download (new file in a folder)

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| WD-01 | Folder choices | Choose **A download** | Buttons for usual folders (Downloads, Desktop, Documents…) | | |
| WD-02 | Existing files ignored | Watch Downloads that already contains files | No alert for existing files | | |
| WD-03 | New download | Start a browser download of a ~50 MB file | Nothing during the download (`.crdownload` ignored). About 10–15 s after it finishes: "Finished: <file> has arrived in Downloads." | | |
| WD-04 | Still growing | Copy a large file into the folder | Alert only after it stops growing and is not locked | | |
| WD-05 | Temp types ignored | Create `x.part`, `x.tmp`, `x.download`, `x.opdownload` | No alert for these | | |
| WD-06 | Empty file | Create a 0-byte file | No alert | | |
| WD-07 | Folder you may read | (API) watch a folder inside an allowed workspace | Accepted | | |
| WD-08 | Folder not allowed | (API) watch a private folder not allowed | 403 `needs_access` with the folder name | | |
| WD-09 | Folder missing | Watch a path that doesn't exist | Error "That folder does not exist on this PC." | | |
| WD-10 | App restarted | Restart the app while a folder watcher exists | The watcher is still listed; it takes a fresh baseline (files already there are not news) | | |

### 6.3 PC quiet

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| WQ-01 | Slider | Choose **PC goes quiet** | Slider 5–40%, label "under N% busy" | | |
| WQ-02 | Fires when idle | Start a CPU-heavy job (a video export), watch with 15%, let it finish | About 30 s after the PC settles: "The PC has gone quiet…" | | |
| WQ-03 | Does not fire while busy | Keep the CPU above the limit | No alert | | |
| WQ-04 | Blip resets it | Quiet for 20 s, then a burst, then quiet | The 30 s count starts again | | |
| WQ-05 | Range (API) | `target` `0`, `99`, `lots` | 422 for each | | |

### 6.4 "Then…" actions and general

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| WT-01 | Then menu | Open the "Then" menu | "just tell me" plus only allowed actions (lock, sleep…) | | |
| WT-02 | Confirmation | Pick "Then: lock this pc", **Watch** | Dialog "Lock this PC when it's done?" Cancel → nothing; confirm → added | | |
| WT-03 | Runs the action | Let the watcher finish | Alert says "Now: lock." and the PC locks | | |
| WT-04 | Sleep notice | Use "Then: sleep" | Phone is told the PC is going to sleep first | | |
| WT-05 | Refused action | (API) `then:"shutdown"` when not allowed | 422 "switched off" | | |
| WT-06 | Admin-only | `then:"shutdown"` (when enabled) from an Operator phone | 403 needs admin | | |
| WT-07 | Fires once | After any alert | The watcher is gone; no repeat | | |
| WT-08 | Remove | Swipe the watcher left and delete it | Watcher removed; no alert ever arrives | | |
| WT-09 | Limit | Add 20 watchers, then a 21st | Error "There are already 20 watchers. Remove one first." | | |
| WT-10 | Several together | Two watchers finishing at once | Both alerts arrive | | |
| WT-11 | Log | After adding and firing | `watch_added` and `watch_fired` in Activity | | |
| WT-12 | Locked phone | Lock phone, let a watcher finish | Push "Finished" arrives ("Something you were watching has finished." unless details are on) | | |

---

## 7. Recent and pinned

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| RC-01 | Two lists | Open **Recent** | Switch with **Pinned (n)** and **Recent (n)** | | |
| RC-02 | Recent list | Open a few documents on the PC, then the **Recent** list | Those files appear, newest first, with their folder and "x min ago" | | |
| RC-03 | Missing files skipped | Delete a recently opened file | It no longer appears | | |
| RC-04 | No duplicates | Open the same file twice | Listed once | | |
| RC-05 | Cache | Open the sheet twice in 30 s | Second open is instant (list is remembered for 30 s) | | |
| RC-06 | Usual folders | In **Pinned**, see "Pin a usual folder" chips | Chips for Desktop, Documents, Downloads, Pictures, Videos, Music (only ones that exist) | | |
| RC-07 | Pin a folder | Tap **+ Downloads** | Appears in the pinned list with its path | | |
| RC-08 | Pin from recent | In **Recent** tap the star on a file | Toast "Pinned". Shows in Pinned; star disappears for it | | |
| RC-09 | Open pinned folder | Tap a pinned folder | Sheet closes, the Files browser opens at that folder | | |
| RC-10 | Open pinned file | Tap a pinned file in an allowed folder | Opens in the viewer/editor | | |
| RC-11 | Folder not allowed | Tap a pinned/recent item in a folder that isn't allowed yet | The app asks to allow the folder (same as Files). Allow → it opens | | |
| RC-12 | Unpin | Swipe the pinned row left and tap **Unpin** | Removed | | |
| RC-13 | Rename by re-pinning | (API) pin the same path with a new label | Label changes, still one entry | | |
| RC-14 | Pin limit | Pin 30 places, then a 31st | Error "You can pin up to 30 places. Unpin one first." | | |
| RC-15 | Missing place | Pin a folder, rename/delete it on the PC | Entry stays; opening it shows a clear error | | |
| RC-16 | Not a real path (API) | Pin `C:\nope` or empty | 422 | | |
| RC-17 | Survives restart | Restart the app | Pins remain | | |
| RC-18 | Access levels | View-only: can see lists and open items, cannot pin/unpin (no buttons; API 403). Editor and up: can pin | | |
| RC-19 | Windows list unavailable | If PowerShell is blocked | "Windows has no recent files to show." No crash | | |

---

## 8. Print

Need **Operator**. Open **Print**.

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| PR-01 | Header | Open the sheet | "Prints on <default printer>. You confirm each one." | | |
| PR-02 | No default printer | Remove the default printer (or run where none exists) | "This PC has no default printer. Set one in Windows first." | | |
| PR-03 | List | Recently opened documents/pictures | Only printable types appear; `.exe`, `.zip`, `.html` do not | | |
| PR-04 | Print | Tap **Print** on a PDF | Dialog "Print <name>?" naming the printer. Confirm → toast "Sent to <printer>". Print job appears in the queue / PDF is made | | |
| PR-05 | Cancel | Cancel the dialog | Nothing is printed | | |
| PR-06 | Offline printer | Set the printer offline | Dialog or error mentions it is offline; refused if offline | | |
| PR-07 | File types | Try `.docx`, `.xlsx`, `.pptx`, `.txt`, `.png`, `.jpg`, `.csv`, `.rtf` | Each prints via its own Windows program | | |
| PR-08 | Blocked types (API) | Print `setup.exe`, `.ps1`, `.bat`, `.lnk`, `.html`, no extension | Each 422; no program starts | | |
| PR-09 | No print program | A type with no Windows print handler | Error "Windows has no program that can print this kind of file." | | |
| PR-10 | Over 100 MB | A 150 MB PDF | 422 "larger than 100 MB" | | |
| PR-11 | File outside allowed folders | Print a file in a folder not allowed | 403 `needs_access`; the phone asks to allow the folder; after allowing, print continues | | |
| PR-12 | Missing file | File deleted after listing | 404 error toast | | |
| PR-13 | Printing off | `printing: false` | No Print tile; API returns 404 "switched off" | | |
| PR-14 | Log | After printing | `print` entry with the path, printer and phone name | | |
| PR-15 | Access | View-only/editor: no Print buttons, lock note shown; API 403 | | |
| PR-16 | Empty list | No printable recent files | Message "None of your recent files can be printed. Open the file in Files, or pin it, then come back." | | |

---

## 9. Snap

### 9.1 Screenshot

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| SN-01 | Owner's phone | On the Admin phone tap **Screenshot of the PC** | "Taking a screenshot…" then a **Screenshot** sheet with the picture and **Save to this phone** | | |
| SN-02 | Looks right | Check the picture | A full-size picture of the PC's real screen, without the pointer | | |
| SN-03 | Saved on PC | Check `Screenshots\` | `screenshot-YYYYMMDD-HHMMSS.jpg` exists and opens | | |
| SN-04 | Save to phone | Tap **Save to this phone** | The picture downloads | | |
| SN-05 | Another phone, not allowed | On an Operator phone that has no permission to view | Toast "Ask to look at the screen first. The owner says yes on the PC." and the screen-view sheet opens | | |
| SN-06 | After owner says yes | Ask to look, accept on the PC, then take a screenshot | Works; viewing is not left running afterwards (the PC's "someone is looking" banner disappears) | | |
| SN-07 | Already viewing | While viewing the screen live, take a screenshot | Works; the live view continues | | |
| SN-08 | Someone else is looking | Another phone is viewing | Clear message (screen busy) | | |
| SN-09 | Blacked-out screen | Use **Screen off**, then screenshot | Picture shows the real desktop (not black) | | |
| SN-10 | Locked PC | Lock the PC and try | A clear error or the lock screen; no crash | | |
| SN-11 | Multi-monitor | With two monitors | Picture shows the primary screen (note what you see) | | |
| SN-12 | Access | View-only: no button, lock note; API 403 | | |
| SN-13 | Log | After a screenshot | `snap_screen` entry with the file name | | |

### 9.2 Webcam

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| SW-01 | Off by default | Open **Snap** | Text "The webcam is off. To allow the Admin phone to use it, set daily: webcam: true…" | | |
| SW-02 | API refused when off | `POST /api/daily/snap/webcam` | 403 `webcam_off` | | |
| SW-03 | Turn on | `pip install opencv-python`, set `webcam: true`, restart | Admin phone sees **Photo from the PC's webcam** | | |
| SW-04 | Confirm | Tap it | Dialog "Take a photo with this PC's webcam?" explaining everyone is told. Cancel → nothing | | |
| SW-05 | Photo | Confirm | Camera light blinks; the picture opens in a sheet; copy saved as `webcam-….jpg` in `Screenshots` | | |
| SW-06 | Everyone told | Check a second paired phone | Gets a security notification "Webcam photo taken" naming who took it | | |
| SW-07 | Logged first | Check Activity | `snap_webcam` is there, even when the camera failed | | |
| SW-08 | Not admin | Operator phone | Shown "The webcam is only for the Admin phone."; API 403 needs admin | | |
| SW-09 | No camera / in use | Unplug or use the camera in another app | Error "This PC's camera could not be opened…" | | |
| SW-10 | opencv missing | `webcam: true` without opencv | Error "The webcam needs the opencv-python package on the PC…" | | |

---

## 10. PC alerts (Windows notifications on the phone)

Set `daily: notifications: true` and restart first.

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| PA-01 | Off by default | With the default config | No tile. Opening via API shows "enabled: false" | | |
| PA-02 | Tile on | With it on | **PC alerts** tile appears | | |
| PA-03 | Shows recent | Open it | Notifications from roughly the last 6 hours: title, app name, "x min ago", body | | |
| PA-04 | New one arrives | Cause a Windows notification (e.g. send yourself a Teams message, plug in the charger) and reopen | It shows in the list | | |
| PA-05 | Push when new | With the page closed, trigger new notifications | Phone gets "New notifications on your PC" (at most once per minute; says count unless details are on) | | |
| PA-06 | Hide one | Tap X on an item | It disappears from the phone list only; still in the PC's Action Center | | |
| PA-07 | Hide all | Tap **Hide all** | List empties | | |
| PA-08 | Not shown again | Reopen the sheet | Hidden ones stay hidden until the app restarts | | |
| PA-09 | Plain text only | A notification with odd characters or markup | Shown as text | | |
| PA-10 | Windows database missing/locked | If Windows' list can't be read | A red message such as "Windows' notification list could not be read" appears; no crash | | |
| PA-11 | Nothing written | Search the app's data folder for notification text | None stored (kept in memory only) | | |
| PA-12 | PC's database untouched | Before/after file hash of `wpndatabase.db` is not changed by the app | Unchanged (the app reads a copy) | | |
| PA-13 | Access | View-only: can read, cannot hide (API 403). Editor and up: can hide | | |
| PA-14 | Private data warning | Read the "off until you choose" note in the sheet when disabled | Wording is clear | | |

---

## 11. Briefing

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| BR-01 | Opens | Tap **Briefing** | A box of short sentences: battery %, memory %, free disk space, uptime | | |
| BR-02 | Battery wording | On battery vs plugged in | "Battery 80% and on battery." / "…and charging." Desktop: no battery line | | |
| BR-03 | Uptime | PC up for more than an hour | "Up for N h." or "Up for N d N h." | | |
| BR-04 | Updates (first open) | First time after starting the app | May say "Checking Windows for updates… open this again in a minute." | | |
| BR-05 | Updates (later) | Refresh after a minute | "N Windows updates waiting" or nothing if none | | |
| BR-06 | Pending restart | If Windows needs a restart | "Windows is waiting for a restart to finish updating." (replaces the update count) | | |
| BR-07 | Reminders in next 24 h | Add a reminder for tomorrow morning | Line "1 reminder or task in the next 24 hours." and it appears under **Next up** | | |
| BR-08 | Watchers | Add a watcher | "Watching 1 thing." | | |
| BR-09 | Files waiting | Put a file in `To phone` | "1 file waiting in To phone." | | |
| BR-10 | PC alerts count | With notifications on | "N PC notifications to read." | | |
| BR-11 | Top programs | Look at the bottom | "Using the most memory": up to 4 programs with MB and count | | |
| BR-12 | Nothing to report | Clean state (desktop, no extras) | "Nothing needs you right now." if no other lines apply | | |
| BR-13 | Refresh | Tap **Refresh** | Numbers update | | |
| BR-14 | Works for every level | View-only phone | Can open it | | |
| BR-15 | PC status unavailable | If `psutil` is missing | Still opens; no crash | | |

---

## 12. Access levels (summary matrix)

Test each row with a phone at the stated level. **Y** = works, **—** = refused (button absent or message "needs … access"; API returns 403).

| Action | View only | Editor | Operator | Admin |
|---|---|---|---|---|
| Open any sheet and read lists | Y | Y | Y | Y |
| Save a file from To phone / inbox | Y | Y | Y | Y |
| Briefing | Y | Y | Y | Y |
| Send text/file to inbox | — | Y | Y | Y |
| Remove inbox item | — | Y | Y | Y |
| Save a note | — | Y | Y | Y |
| Pin / unpin | — | Y | Y | Y |
| Hide PC alert | — | Y | Y | Y |
| Sliders, media keys and open usual folder | — | — | Y | Y |
| To PC clipboard / Open link on PC | — | — | Y | Y |
| Add / remove reminder, lock/sleep/screen-off schedules | — | — | Y | Y |
| Add / remove watcher; process list | — | — | Y | Y |
| Print | — | — | Y | Y |
| Screenshot | — | — | Y* | Y |
| Schedule or watch-then hibernate / restart / shutdown / unlock | — | — | — | Y |
| Webcam photo | — | — | — | Y |

\* Needs permission to view the screen (the owner's yes on the PC), except the owner's own Admin phone.

| ID | Test | Expected | Result | Notes |
|---|---|---|---|---|
| AL-01 | Walk the matrix with one phone, changing its level between runs | Matches the table | | |
| AL-02 | Level change takes effect | Change the phone's level on the PC page while the sheet is open, then tap a button | The next action obeys the new level | | |
| AL-03 | Refused action does not take the turn | Viewer/editor taps a forbidden action, then another phone takes a turn | The refused phone never held or blocked the turn | | |
| AL-04 | One person at a time | Phone A starts a turn (send anything). Phone B (operator) tries any change | B gets 409 "A is using this PC. Ask them for a turn." B can still look | | |
| AL-05 | Turn ends | End A's turn; B retries | Works | | |
| AL-06 | Pause | Press Pause on the PC page | Every Everyday tools call returns "paused" (423); resume restores | | |
| AL-07 | Removed/blocked phone | Block a phone on the PC page and tap a tool | Signed out / 401 message; no data shown | | |

---

## 13. Settings and switches

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| CF-01 | `daily.enabled: false` | Restart, use the phone | No card; every `/api/daily/*` returns 404 `daily_off`. Background reminders/watchers don't run | | |
| CF-02 | `daily.folder` | Set to `D:\Phone` | Inbox/Notes/Screenshots/To phone are under it; created on first use | | |
| CF-03 | Folder with `~` | Leave default | Resolves to your Documents profile | | |
| CF-04 | `max_upload_mb` | Set 1; 5; 2048 | Limit enforced as described in IN-24/25. Values < 1 or > 2048 are rejected at start with a clear config error | | |
| CF-05 | `printing` | true/false | Tile and API follow | | |
| CF-06 | `notifications` | true/false | Tile and API follow | | |
| CF-07 | `webcam` | true/false | Follows SW tests | | |
| CF-08 | `power.remote_actions` | Add/remove actions | Schedule and watch-then menus follow; unchanged older tasks respect new rules | | |
| CF-09 | `screen.clipboard: false` | Restart | **To PC clipboard** is refused with a clear message (the inbox clipboard is switched off along with it) | | |
| CF-10 | `push.show_details` | true vs false | Notification wording changes as described (text shown only when true) | | |
| CF-11 | Env override | `HANDOFF_DAILY__ENABLED=false` | Same as editing the file | | |
| CF-12 | Typo in section | Add `daily: {enbled: true}` | The misspelt key is silently ignored and the default (enabled) is used. Note it: a typo does not warn you. A typo in a top-level section name (e.g. `dayly:`) is refused at start | | |

---

## 14. Display and usability

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| UX-01 | Dark theme | Settings → dark | Card and every sheet readable; text has good contrast | | |
| UX-02 | Light theme | Settings → light | Same | | |
| UX-03 | Accent colours | Try every accent | Tile icons, active chips and primary buttons follow | | |
| UX-04 | Large text | Settings → large text | Nothing is cut off; sheets scroll | | |
| UX-05 | Narrow phone | A 320 px wide window | The card fits; sliders full width; media keys centred; tiles in 3 per row; no sideways scrolling inside the card | | |
| UX-06 | Wide screen | Desktop browser | Card sits in the Home grid, not stretched oddly | | |
| UX-07 | Landscape | Rotate the phone | Sheets scroll; buttons reachable | | |
| UX-08 | Reduced motion | Turn on "Reduce motion" or the system setting | No sliding/scaling on taps | | |
| UX-09 | Keyboard | Use Tab/Enter on a desktop browser | Every tile, button, field and chip is reachable; focus is visible; Esc closes the sheet | | |
| UX-10 | Screen reader labels | Use TalkBack/VoiceOver on the control centre | Sliders read as "Volume", "Brightness" with "N percent"; keys as "Previous", "Play or pause", "Next"; the speaker button as "Mute or unmute" | | |
| UX-11 | Long names | A file or folder with a very long name | Wraps; nothing overflows the sheet | | |
| UX-12 | Slow connection | Throttle to "Slow 3G" | "Asking your PC…" shows, then fills; errors are readable | | |
| UX-13 | Messages are readable | Trigger each error above | Plain-language messages; never the word "null" or a stack trace | | |
| UX-14 | Toast stacking | Tap Play / Next / Previous quickly | Nothing gets stuck; the keys light briefly each time | | |
| UX-15 | Sheet over sheet | Open Snap → screenshot (opens a picture sheet) | Picture sheet closes first with Back, then Snap | | |
| UX-16 | Add to Home Screen (PWA) | Install the page to the phone's home screen and test a few tools | Works the same | | |
| UX-17 | Glow and glass | Look at Home, Chat, Files, Access and Settings in dark and light | A soft coloured glow behind every screen; cards, sheets and the tab bar look frosted; text stays easy to read | | |
| UX-18 | Swipe to delete | In Inbox, Remind, Watch or Pinned, drag a row left a little and let go | It stays open with a red **Delete** (or **Unpin**) button; tap it to remove. Tap elsewhere or drag back to close | | |
| UX-19 | Swipe all the way | Drag a row more than about two thirds across, or flick it hard | It slides off, the list closes up and the PC removes it. If the PC refuses (e.g. level), the row slides back and the reason is shown | | |
| UX-20 | Swipe vs scroll | Scroll a list up and down with a slightly slanted finger | The list scrolls; rows do not move sideways by accident | | |
| UX-21 | Pull to refresh | At the top of any tool sheet, pull down and let go | A ring fills; past the line it spins and the list reloads. Pulling a little and letting go does nothing | | |
| UX-22 | Loading shapes | Throttle to "Slow 3G" and open a tool | Grey shimmering shapes show where the content will be, then are replaced by it | | |
| UX-23 | Empty pictures | Open Inbox, Notes, Remind, Watch with nothing in them | A small picture plus one line of what it is for, never a blank screen | | |
| UX-24 | Drag and drop upload | On a tablet or computer, drag files onto the open Inbox sheet | "Drop to send to your PC" appears; on drop each file gets a progress bar with size, speed and time left; **X** cancels one; done files show a tick | | |
| UX-25 | Upload errors | Drop a file bigger than the limit; pull the network mid-upload | A message names the limit / says the connection dropped; a retry button appears | | |
| UX-26 | Success feedback | Save a note, send text, pin a place | The button briefly shows a tick animation and the phone gives a short tick | | |
| UX-27 | Calm mode | Turn on the system reduce-motion setting, or set the app's motion to Off | No glow drift, bouncing or sliding; swipe and pull still work but jump instead of gliding | | |
| UX-28 | Old phone | A phone without blur support | Panels are solid, not see-through; everything still readable | | |
| UX-29 | Oil paint opens Chat | From Home, tap **Chat** | Thick brush strokes in your accent colours sweep across the page (about half a second), a "Chat" title floats up over the paint, Chat is switched on underneath, then the paint is scraped away like a palette knife. In all about 1.5 seconds. The tab bar stays above the paint and glides away as usual | | |
| UX-30 | It is 3D | Watch it on a phone with Motion on Full | The paint layer tilts in from a slight angle and settles flat, a soft sheen of light slides across the wet paint, then it tilts the other way as it is scraped off | | |
| UX-31 | Different every time | Tap Chat, go Home, tap Chat again several times | The strokes differ each time; the colours follow your accent (try each one in Settings) in dark and light themes; no flash of the old screen between strokes | | |
| UX-32 | Skip it | Tap anywhere while the paint is on screen | It stops at once and Chat is showing | | |
| UX-33 | Motion levels | Settings → Motion: Simple, then Off; also the system reduce-motion setting | **Simple:** the same paint but flat and shorter (no tilt or sheen). **Off** or reduce-motion: no paint at all, Chat opens at once | | |
| UX-34 | Other ways to open Chat | Use the Back arrow from Chat, open a chat from Home's recent list | They are not slowed by the paint (it only plays when the Chat tab is tapped) | | |
| UX-35 | Weak phone | A low-end phone | Still smooth, or at least not stuck: if frames are slow it still finishes in under about 3 seconds and never blocks the page | | |
| UX-36 | PC tab paint | From Home, tap **Access** | Blue brush strokes are drawn line by line from the top like a screen being scanned, with faint screen lines and a light bar sweeping down; the title **YOUR PC** with a monitor icon floats up; the PC access screen opens underneath and is scraped clear. The paint covers the tab bar too | | |
| UX-37 | Files tab paint | From Home, tap **Files** | Warm amber, orange and cream slanted slabs cascade down the page like papers being dealt, a folder icon drops in above **Files**, then the file browser opens underneath and the paint is scraped away. Nothing of the browser shows before the page is covered | | |
| UX-38 | Settings tab paint | From Home, tap **Settings** | Violet, pink and teal spirals wind out from the middle like a dial being turned, a pulse of light spreads from the centre, the sliders icon turns above **Settings**; Settings opens underneath and is revealed from the middle outwards like an iris | | |
| UX-39 | Each tab looks different | Tap Chat, Access, Files and Settings in turn | Four clearly different pictures (waves, scan, stack, swirl) in different colours; only Chat follows your accent colour | | |
| UX-40 | Tap during the paint | Tap **Files**, then tap **Settings** while the Files paint is still on screen | The Files screen opens at once, then the Settings paint plays; you end up in Settings with Files behind it. Tapping the paint itself skips it and still opens that tab's screen | | |
| UX-41 | Motion levels (all tabs) | Settings → Motion: Simple, then Off; also the system reduce-motion setting | Simple: the same paints, flat and shorter. Off or reduce-motion: no paint, each screen opens at once | | |
| UX-42 | Light theme and 320 px | Repeat UX-36 to UX-38 in the light theme and a 320 px wide window | The paint covers the whole page with no gaps at the corners; the title is readable; the tab bar and screens are normal afterwards | | |
| UX-43 | Back and Close unaffected | Open each screen, then Back, then Close | Only tapping the tab plays the paint; Back, Close and links inside screens do not | | |

---

## 15. Security tests (API level)

Replace `TOKEN` with a paired phone's token and `PC` with the PC address (add `-k` for the self-signed certificate).

| ID | Test | How | Expected | Result | Notes |
|---|---|---|---|---|---|
| SE-01 | No token | `curl -k https://PC:8765/api/daily/state` | 401 | | |
| SE-02 | Wrong token | Same with `-H "X-Token: wrong"` | 401 | | |
| SE-03 | Every route needs a token | Repeat for inbox, notes, tasks, watch, briefing, recent, favourites, alerts, notifications, processes, print, snap | All 401 | | |
| SE-04 | Path traversal: To phone | `GET /api/daily/tophone/file?name=..%2F..%2FWindows%2Fwin.ini` and `..%5C..%5C…` | 404 | | |
| SE-05 | Path traversal: Inbox id | `GET /api/daily/inbox/..%2F..%2Fx/file` and `0123456789abcdef/file` | 404 | | |
| SE-06 | Path traversal: upload name | `PUT …/file?name=..%5C..%5Cevil.exe` | Saved inside `Inbox` only, name cleaned | | |
| SE-07 | Alternate data stream | `?name=menu.pdf:stream` | 404 / cleaned | | |
| SE-08 | Reserved names | Upload `CON`, `NUL.txt`, `COM1` | Saved with a leading underscore | | |
| SE-09 | Link scheme | Send `javascript:alert(1)` | Stored as text; "Open on PC" refused (422) | | |
| SE-10 | Open on PC with a non-link id | Try with a text item id | 422 | | |
| SE-11 | Script in text | Send `<img src=x onerror=alert(1)>` in inbox, notes | Shows as text; no alert | | |
| SE-12 | Script in notification | (Notifications on) a Windows toast whose title contains markup | Shown as text | | |
| SE-13 | XML bomb in toast | Not creatable by hand; covered by automated tests | n/a | | |
| SE-14 | SQL-like ids | `GET /api/daily/inbox/' OR 1=1 --/file` | 404 | | |
| SE-15 | Oversized body | 2 GB upload | 413 early (from the declared size) | | |
| SE-16 | Wrong methods | `GET /api/daily/control`, `DELETE /api/daily/inbox/text` | 405 | | |
| SE-17 | Bad JSON | `POST /api/daily/tasks` with `{}` and with wrong types | 422 | | |
| SE-18 | Power actions bypass | Schedule `format`, `unlock`, `restart` (not allowed) | 422 / 403 | | |
| SE-19 | Level bypass | Use a viewer token against every changing route | 403 each | | |
| SE-20 | Turn bypass | Use a non-holder's token to change something | 409 | | |
| SE-21 | Webcam bypass | Operator token to `POST /api/daily/snap/webcam` | 403 | | |
| SE-22 | Screenshot bypass | Operator token without view permission to `POST /api/daily/snap/screen` | 403 `screen_not_allowed` | | |
| SE-23 | Log has no secrets | Search Activity export after using notes, clipboard, inbox text | No note/clipboard/message text, only counts and names | | |
| SE-24 | Webcam log even on failure | Make the camera fail, then check Activity | `snap_webcam` recorded | | |
| SE-25 | Files never executed | Send, then list, then delete a `.exe` | Never launched by the app | | |
| SE-26 | Other-phone isolation | Does phone B see phone A's inbox? | Yes by design (the inbox is shared across paired phones); confirm this matches what you expect and note it | | |
| SE-27 | Rate/size of PC alerts | Flood Windows with notifications | List stays at most 50 items; push sent at most once a minute | | |
| SE-28 | Response headers | Check downloads | `X-Content-Type-Options: nosniff`; screenshots `Cache-Control: no-store` | | |

---

## 16. Reliability and recovery

| ID | Test | Steps | Expected | Result | Notes |
|---|---|---|---|---|---|
| RL-01 | App restart | Create a reminder, watcher, pin, inbox items, notes. Restart | All still there (watchers re-baseline) | | |
| RL-02 | PC sleeps and wakes | Schedule a reminder, sleep the PC through it, wake | Reminder arrives late-marked once; power actions are skipped | | |
| RL-03 | Phone loses Wi-Fi | Turn off Wi-Fi in a sheet | Friendly offline state; no endless spinner; works again after reconnect | | |
| RL-04 | Two phones at once | Both open Inbox; one sends | Other sees it after reopening/refresh | | |
| RL-05 | Long uptime | Leave running a day with a few watchers | No memory growth; ticks continue | | |
| RL-06 | Disk full | Fill the drive then upload | Clear error; no partial file left | | |
| RL-07 | Folder permissions | Make `Documents\AccessFromPhone` read-only | Clear errors on save/send; app keeps running | | |
| RL-08 | Folder deleted | Delete `AccessFromPhone` folder while the app runs | Recreated on next use | | |
| RL-09 | Corrupt notes file | Put binary junk in a `Notes …md` | Notes list still loads | | |
| RL-10 | Firewall | Test from a phone on a different network without relay | Clear "can't reach" state | | |
| RL-11 | Via relay / Tailscale | Repeat the main tests over your relay/Anywhere link | Same behaviour (uploads may be slower) | | |
| RL-12 | Android app | Install the APK, open Home | Control centre card loads from the PC; check the file picker (**Send a file…**), saving downloads, dictation, and printing all work or fail with a clear message | | |

---

## 17. Automated checks you can run

From `C:\Users\UllaAfza\AccessFromPhone`:

```powershell
# the new features' tests (about 1 minute)
.\.venv\Scripts\python -m pytest -q tests/test_daily_core.py tests/test_daily_http.py tests/test_daily_assets.py

# the phone page's wording, time maths, gestures and Home rules
node tests/js/daily_test.js
node tests/js/ux_test.js

# everything else that touches them
.\.venv\Scripts\python -m pytest -q tests/test_home.py tests/test_access_core.py tests/test_access.py tests/test_browse.py tests/test_offline_assets.py

# the full suite (about 5 minutes; skip the live-screen blackout test)
.\.venv\Scripts\python -m pytest -q --deselect tests/test_blackout.py

# a real browser against a real PC app (needs Edge or Chrome; changes nothing on this PC)
.\.venv\Scripts\python tests/e2e/daily_e2e.py
.\.venv\Scripts\python tests/e2e/home_e2e.py

# screenshots of every screen at a size and theme you choose (folder, width, dark|light|both)
.\.venv\Scripts\python tests/e2e/shots_e2e.py $env:TEMP\shots 390 both
```

What the automated tests already cover, so you can focus manual time elsewhere:
- Every route's level check, turn check, pause, and no-token behaviour.
- Name/path tricks for inbox, To phone and uploads; size limits; empty files; partial-file clean-up.
- Reminder, repeat, missed-task and power-action rules with a fake clock.
- Watcher rules (process, download, quiet) with fakes.
- Windows notification parsing against a copy of its database format.
- The phone page: tiles shown per setting, sliders and media keys follow the PC and the phone's level, swipe-to-delete, drag-and-drop upload, folded forms, badges, no stray "null", every icon, route and style exists.

What only a person can check (so put your effort here): real volume/brightness/media keys, real printers, real webcam, real push notifications on a locked phone, dictation, the owner's yes for screenshots, how it looks and feels on your phones, and the Android app.

---

## 18. Bug report template

```
ID:            (e.g. RM-17)
Phone / browser / Android version:
PC Windows version:
App version (Settings → About):
Config differences from the defaults:
Steps:
Expected:
What happened:
Screenshot / the Activity-log lines around it:
```

## 19. Sign-off

| Area | Tester | Date | Pass / Fail | Open bugs |
|---|---|---|---|---|
| Card on Home | | | | |
| Control centre (sliders, media keys) | | | | |
| Inbox | | | | |
| Notes | | | | |
| Remind | | | | |
| Watch | | | | |
| Recent and pinned | | | | |
| Print | | | | |
| Snap (screenshot, webcam) | | | | |
| PC alerts | | | | |
| Briefing | | | | |
| Access levels | | | | |
| Settings | | | | |
| Display and usability | | | | |
| Security | | | | |
| Reliability | | | | |

/* titles.js: a short, readable name for a chat, made from its first request.
 *
 *   "i want to open C:\output folder please open it"  ->  "Open output folder"
 *   "lock my PC"                                      ->  "Lock PC"
 *
 * It runs on the phone, instantly and offline, and sends nothing anywhere. When the PC has its own AI model, the page may later replace this with
 * the model's wording. Pure text in, text out (no page, no network), so the rules are tested on their own: tests/js/titles_test.js.
 */
(function (root) {
  "use strict";

  var FILLER = /^(?:hey|hi|hello|hola|ok(?:ay)?|so|well|please|kindly|pls|plz|just|now|also|actually|can you|could you|would you|will you|can u|could u|i want to|i wanna|i need to|i'd like to|i would like to|i have to|let's|lets|help me|go and|go ahead and|try to)[,;:]?\s+/i;
  var TAIL = /\s*(?:,|\.|!|\?)?\s*(?:please|pls|plz|for me|thanks|thank you|now|asap)\s*[.!?]*\s*$/i;
  var SPLIT = /\s+(?:please|pls|plz|and then|then)\s+/i;
  var POWER = [
    [/^(?:un)?lock\b.*\b(?:pc|computer|laptop|machine|screen)\b/i, null],
    [/^(?:shut\s?down|restart|reboot|sleep|hibernate|power\s?off)\b.*\b(?:pc|computer|laptop|machine)\b/i, null],
  ];
  var SMALL = { a: 1, an: 1, the: 1, my: 1, your: 1, our: 1, that: 1, this: 1, those: 1, these: 1, me: 1 };
  var MAX_WORDS = 7, MAX_CHARS = 48;

  function capital(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

  /* A path in the words becomes its last name: C:\Users\Asha\Reports  ->  Reports */
  function shortPaths(text) {
    // A Windows path may have spaces inside a folder name ("OneDrive - Unisys"), but the last name ends at the next space.
    return text.replace(/(?:(?:[A-Za-z]:[\\/]|\\\\)(?:[^\\/\r\n:"<>|?*]{1,40}[\\/])*[^\\/\s"<>|?*]*|\/[^\s"'<>|?*]*)/g, function (p) {
      var parts = p.split(/[\\/]+/).filter(Boolean);
      var last = parts.length ? parts[parts.length - 1] : p;
      return /^[A-Za-z]:$/.test(last) ? p : last;
    });
  }

  function make(prompt, options) {
    var o = options || {};
    var maxWords = o.maxWords || MAX_WORDS, maxChars = o.maxChars || MAX_CHARS;
    var text = String(prompt == null ? "" : prompt).replace(/\s+/g, " ").trim().replace(/^["'`]+|["'`]+$/g, "");
    if (!text) return "New chat";

    var guard = 0;
    while (FILLER.test(text) && guard++ < 6) text = text.replace(FILLER, "");   // "please can you ..." -> "..."
    var cut = SPLIT.exec(text);                                                  // "open X please open it" -> "open X"
    if (cut && cut.index >= 6) text = text.slice(0, cut.index);
    text = text.replace(TAIL, "").trim();
    text = shortPaths(text);
    text = text.replace(/\s+(?:and )?(?:open|show|read|do) it$/i, "").trim();    // a repeated "open it" at the end adds nothing

    for (var i = 0; i < POWER.length; i++) {                                     // "lock my pc" -> "Lock PC"
      if (POWER[i][0].test(text)) {
        var verb = text.split(" ")[0].toLowerCase();
        return capital(verb) + " PC";
      }
    }

    var words = text.replace(/^[\s,.;:!?-]+|[\s,.;:!?-]+$/g, "").split(" ").filter(Boolean);
    while (words.length > 2 && SMALL[words[0].toLowerCase()] && words.length > maxWords) words.shift();
    words = words.map(function (w, k) { return k > 0 && /^(?:my|your|the)$/i.test(w) ? null : w; }).filter(Boolean);  // "open my folder" -> "open folder"
    if (words.length > maxWords) words = words.slice(0, maxWords);
    var title = words.join(" ").replace(/[\s,.;:!?-]+$/g, "");
    if (title.length > maxChars) {
      title = title.slice(0, maxChars + 1);
      var space = title.lastIndexOf(" ");
      title = (space > 12 ? title.slice(0, space) : title.slice(0, maxChars)).replace(/[\s,.;:!?-]+$/g, "");
    }
    return title ? capital(title) : "New chat";
  }

  /* What the PC's model wrote, made safe to show: one line, no quotes or label, not too long. null when it is not a usable name. */
  function tidy(raw) {
    var t = String(raw == null ? "" : raw).split(/\r?\n/).filter(function (l) { return l.trim(); })[0] || "";
    t = t.replace(/^\s*(?:title|name|chat title)\s*[:\-]\s*/i, "").replace(/^[\s"'`*#]+|[\s"'`*#.:;-]+$/g, "").replace(/\s+/g, " ");
    if (!t || /^(?:i'm sorry|i am sorry|sorry|as an ai|i cannot|i can't)/i.test(t)) return null;
    var words = t.split(" ");
    if (words.length > 9) t = words.slice(0, 9).join(" ");
    return t.length > 60 ? null : capital(t);
  }

  var api = { make: make, tidy: tidy };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AfpTitles = api;
})(typeof window !== "undefined" ? window : this);

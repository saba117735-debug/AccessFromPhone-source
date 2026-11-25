/* Runs before the page's own scripts, only inside the phone app.
   It gives the page the two things Android's built-in web view lacks, using the app's native parts instead:
   voice input (the page expects the browser's SpeechRecognition), and saving a downloaded file. */
(function () {
  if (window.__afpShim) return;
  window.__afpShim = true;

  // The app only ever shows the PC the person paired with, and does its own certificate check, so the page may use the microphone.
  try { if (!window.isSecureContext) Object.defineProperty(window, "isSecureContext", { get: function () { return true; } }); } catch (e) { /* already secure */ }

  // ---- voice: SpeechRecognition on top of Android's recogniser
  if (window.AfpSpeech && window.AfpSpeech.available()) {
    var active = null;
    var Recognition = function () {
      this.lang = "en-US"; this.continuous = false; this.interimResults = false; this.maxAlternatives = 1;
      this.onstart = this.onresult = this.onerror = this.onend = null;
    };
    Recognition.prototype.start = function () { active = this; window.AfpSpeech.start(this.lang, !!this.interimResults); };
    Recognition.prototype.stop = function () { window.AfpSpeech.stop(); };
    Recognition.prototype.abort = function () { window.AfpSpeech.abort(); };
    window.__afpSpeech = function (type, text, isFinal) {
      var r = active;
      if (!r) return;
      if (type === "start") { if (r.onstart) r.onstart({}); }
      else if (type === "result") {
        var alt = { transcript: text, confidence: 0.9 };
        var list = [alt]; list.isFinal = isFinal; list.item = function (i) { return list[i]; };
        if (r.onresult) r.onresult({ results: [list], resultIndex: 0 });
      }
      else if (type === "error") { if (r.onerror) r.onerror({ error: text }); }
      else if (type === "end") { active = null; if (r.onend) r.onend({}); }
    };
    window.SpeechRecognition = window.webkitSpeechRecognition = Recognition;
  }

  // ---- spoken replies: speechSynthesis on top of Android's text-to-speech (without it the page hides its speaker button)
  if (window.AfpTts && window.AfpTts.available()) {
    var current = null;
    var Utterance = function (text) { this.text = String(text || ""); this.lang = "en-US"; this.rate = 1; this.onstart = this.onend = this.onerror = null; };
    var synth = {
      speaking: false, pending: false, paused: false, onvoiceschanged: null,
      getVoices: function () { return []; },
      speak: function (u) { current = u; window.AfpTts.speak(u.text, String(u.lang || "en-US"), Number(u.rate) || 1); },
      cancel: function () { current = null; window.AfpTts.stop(); },
      pause: function () {}, resume: function () {}, addEventListener: function () {}, removeEventListener: function () {}
    };
    window.__afpTts = function (type) {
      var u = current;
      if (!u) return;
      if (type === "start") { if (u.onstart) u.onstart({}); return; }
      current = null;
      if (type === "end") { if (u.onend) u.onend({}); } else if (u.onerror) u.onerror({ error: "synthesis-failed" });
    };
    try {
      Object.defineProperty(window, "SpeechSynthesisUtterance", { value: Utterance, configurable: true, writable: true });
      Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true });
    } catch (e) { /* the page keeps whatever the browser has */ }
  }

  // ---- downloads: the page saves a file by clicking a hidden link to an in-memory file; hand that to the app
  var originalClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    var a = this;
    if (window.AfpApp && a.download && a.href && a.href.indexOf("blob:") === 0) {
      fetch(a.href).then(function (r) { return r.blob(); }).then(function (blob) {
        var reader = new FileReader();
        reader.onloadend = function () { window.AfpApp.saveFile(a.download, blob.type || "application/octet-stream", String(reader.result).split(",")[1] || ""); };
        reader.readAsDataURL(blob);
      });
      return;
    }
    return originalClick.apply(this, arguments);
  };
})();

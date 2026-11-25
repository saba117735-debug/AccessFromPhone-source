// The phone app: a native shell (scanner, voice, downloads, trust for your own PC) around the same web page the PC serves.
plugins {
    id("com.android.application") version "8.13.0" apply false
    id("org.jetbrains.kotlin.android") version "2.1.21" apply false
    id("com.google.gms.google-services") version "4.4.3" apply false  // turns app/google-services.json into the app's Firebase settings
}

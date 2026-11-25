# The web page calls these from JavaScript by name, so they must keep their names in a release build.
-keepclassmembers class app.accessfromphone.** {
    @android.webkit.JavascriptInterface <methods>;
}

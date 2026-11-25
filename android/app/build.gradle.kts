import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Push notifications need this project's Firebase file (app/google-services.json: see "Push notifications" in the README).
// Without it the app still builds and runs; notifications simply say they are unavailable.
if (file("google-services.json").exists()) apply(plugin = "com.google.gms.google-services")

android {
    namespace = "app.accessfromphone"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.accessfromphone"  // changes when the product is renamed (and the app name in res/values/strings.xml)
        minSdk = 29  // Android 10: downloads can be saved without a storage permission
        targetSdk = 36
        versionCode = 1
        versionName = "0.2.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { buildConfig = true }
    lint { abortOnError = false }
}

// The "Connect to your PC" screen is the page's own, built into the app: these files are copied from the PC app's page every build,
// so the app and the browser show the same screen and can never drift apart.
val copyPage = tasks.register<Copy>("copyPage") {
    from(rootDir.resolve("../src/handoff/web")) { include("index.html", "app.css", "glass.css", "conn.js", "offline.js", "queue.js", "titles.js", "access.js", "screen.js", "ux.js", "app.js", "icon.svg") }
    into(layout.buildDirectory.dir("generated/page/web"))
}
android.sourceSets.getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/page"))
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("Assets") }.configureEach { dependsOn(copyPage) }

kotlin {
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

dependencies {
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.webkit:webkit:1.14.0")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")  // the QR scanner
    implementation(platform("com.google.firebase:firebase-bom:33.16.0"))
    implementation("com.google.firebase:firebase-messaging")  // notifications from the PC
    testImplementation("junit:junit:4.13.2")
}

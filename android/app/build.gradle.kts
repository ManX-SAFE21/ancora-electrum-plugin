plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "life.after.bitcoin"
    compileSdk = 35

    defaultConfig {
        applicationId = "life.after.bitcoin"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        // Chaquopy requires explicit ABI filters. Python 3.12 ships only for
        // 64-bit ABIs: phones (arm64-v8a) + the common emulator image (x86_64).
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        viewBinding = true
    }
}

chaquopy {
    defaultConfig {
        version = "3.12"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")

    // CameraX
    implementation("androidx.camera:camera-core:1.3.4")
    implementation("androidx.camera:camera-camera2:1.3.4")
    implementation("androidx.camera:camera-lifecycle:1.3.4")
    implementation("androidx.camera:camera-view:1.3.4")

    // ML Kit on-device barcode scanning (QR only, no API key)
    implementation("com.google.mlkit:barcode-scanning:17.3.0")
}
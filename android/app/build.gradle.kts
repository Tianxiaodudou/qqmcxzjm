plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// 版本号由 CI 用 -PappVersion=<manifest 里的版本> 传入，保证 APK 与 FPK 同版本。
val appVersion = (project.findProperty("appVersion") as String?) ?: "0.0.0"
private fun verPart(index: Int): Int =
    appVersion.split(".", "-", "+").getOrNull(index)?.toIntOrNull() ?: 0

android {
    namespace = "com.tianxiaodudou.qqmusicdownloader"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.tianxiaodudou.qqmusicdownloader"
        minSdk = 26
        targetSdk = 36
        versionCode = verPart(0) * 1_000_000 + verPart(1) * 1_000 + verPart(2)
        versionName = appVersion
    }

    // 固定签名：密钥只在 CI 里从仓库 Secrets 还原，不落仓库。
    // 没有配置密钥时（本地开发 / fork）回退到 debug 签名。
    val keystorePath = System.getenv("ANDROID_KEYSTORE_PATH")
    signingConfigs {
        if (!keystorePath.isNullOrBlank()) {
            create("release") {
                storeFile = file(keystorePath)
                storeType = "PKCS12"
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
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
        compose = true
        buildConfig = true
    }
    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.09.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.media3:media3-exoplayer:1.4.1")
    implementation("net.jthink:jaudiotagger:3.0.1")
    implementation("org.slf4j:slf4j-nop:2.0.13")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("io.coil-kt.coil3:coil-compose:3.0.4")
    implementation("io.coil-kt.coil3:coil-network-okhttp:3.0.4")

    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.compose.ui:ui-tooling-preview")
}

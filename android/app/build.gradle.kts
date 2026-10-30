plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
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
    // 没有配置密钥时（本地开发 / fork）回退到 debug 签名，产物不可用于覆盖安装正式版。
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
        buildConfig = true
    }
    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
}

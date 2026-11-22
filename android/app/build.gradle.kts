import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val versionJson = groovy.json.JsonSlurper().parse(file("../../version.json")) as Map<*, *>
val appVersionName = (providers.gradleProperty("appVersionName").orNull ?: (versionJson["version"] as String))
val appVersionCode = appVersionName.split(".").let { (a, b, c) -> a.toInt() * 10000 + b.toInt() * 100 + c.toInt() }

// يجمع ملفات تطبيق بايثون والواجهة من جذر المستودع إلى assets دون تعديل أي منها
val webappDir = layout.buildDirectory.dir("generated/webapp")
val copyWebapp = tasks.register<Sync>("copyWebapp") {
    from("../..") {
        include("app.py", "config.py", "version.json", "core/**", "platforms/**", "static/**")
        exclude("**/__pycache__/**", "**/*.pyc")
    }
    into(webappDir.map { it.dir("webapp") })
}

android {
    namespace = "app.sajed.socialmanager"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.sajed.socialmanager"
        minSdk = 24
        targetSdk = 34
        versionCode = appVersionCode
        versionName = appVersionName
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64") }
    }

    sourceSets["main"].assets.srcDir(webappDir)

    signingConfigs {
        create("release") {
            val path = System.getenv("SIGNING_KEYSTORE_PATH")
            if (!path.isNullOrBlank() && file(path).exists()) {
                storeFile = file(path)
                storePassword = System.getenv("SIGNING_STORE_PASSWORD")
                keyAlias = System.getenv("SIGNING_KEY_ALIAS")
                keyPassword = System.getenv("SIGNING_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            val cfg = signingConfigs.getByName("release")
            if (cfg.storeFile != null) signingConfig = cfg
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    packaging { jniLibs.useLegacyPackaging = true }
    lint { checkReleaseBuilds = false }
}

chaquopy {
    defaultConfig {
        version = "3.11"
        buildPython(System.getenv("CHAQUOPY_BUILD_PYTHON") ?: "python3")
        pip {
            install("flask>=3.0")
            install("apscheduler>=3.10")
            install("cryptography")
            install("requests")
            install("python-dotenv")
            install("tweepy")
            install("tzdata")
            install("pillow")
            install("google-api-python-client")
            install("google-auth-oauthlib")
            install("boto3>=1.35,<2")
        }
    }
}

tasks.matching { it.name == "preBuild" }.configureEach { dependsOn(copyWebapp) }

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
}

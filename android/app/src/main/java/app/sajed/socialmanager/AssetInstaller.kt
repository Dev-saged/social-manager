package app.sajed.socialmanager

import android.content.Context
import java.io.File

/** ينسخ مجلد webapp من assets إلى مجلد حقيقي (يُستبدل كله عند تغيّر النسخة). */
object AssetInstaller {
    fun install(ctx: Context): File {
        val target = File(ctx.filesDir, "webapp")
        val marker = File(ctx.filesDir, "webapp.version")
        val version = ctx.packageManager.getPackageInfo(ctx.packageName, 0).let {
            "${it.versionName}-${if (android.os.Build.VERSION.SDK_INT >= 28) it.longVersionCode else 0}-${it.lastUpdateTime}"
        }
        if (target.exists() && marker.exists() && marker.readText() == version) return target
        target.deleteRecursively()
        copy(ctx, "webapp", target)
        marker.writeText(version)
        return target
    }

    private fun copy(ctx: Context, assetPath: String, dest: File) {
        val children = ctx.assets.list(assetPath) ?: emptyArray()
        if (children.isEmpty()) {
            dest.parentFile?.mkdirs()
            ctx.assets.open(assetPath).use { i -> dest.outputStream().use { o -> i.copyTo(o) } }
        } else {
            dest.mkdirs()
            for (c in children) copy(ctx, "$assetPath/$c", File(dest, c))
        }
    }
}

package kz.fieldroute.app

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.android.RenderMode

/// Texture + отключённый Impeller — на части Xiaomi Surface даёт серый экран.
class MainActivity : FlutterActivity() {
    override fun getRenderMode(): RenderMode = RenderMode.texture
}

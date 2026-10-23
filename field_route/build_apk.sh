#!/bin/bash
# Сборка APK «Полевой маршрут» внутри образа Flutter.
set -euo pipefail
cd "$(dirname "$0")"
git config --global --add safe.directory '*' 2>/dev/null || true

if [ ! -d android ]; then
  flutter create . --project-name field_route --org kz.fieldroute --platforms android
  rm -f test/widget_test.dart
fi

PROPS=android/gradle.properties
sed -i 's|^org.gradle.jvmargs=.*|org.gradle.jvmargs=-Xmx1536m -XX:MaxMetaspaceSize=512m -XX:ReservedCodeCacheSize=192m|' "$PROPS"
for kv in org.gradle.daemon=false org.gradle.workers.max=2 kotlin.compiler.execution.strategy=in-process; do
  grep -q "^${kv%%=*}=" "$PROPS" || echo "$kv" >> "$PROPS"
done

MANIFEST=android/app/src/main/AndroidManifest.xml
grep -q 'android.permission.INTERNET' "$MANIFEST" ||
  sed -i 's|<manifest xmlns:android="http://schemas.android.com/apk/res/android">|&\n    <uses-permission android:name="android.permission.INTERNET"/>|' "$MANIFEST"
grep -q 'ACCESS_FINE_LOCATION' "$MANIFEST" ||
  sed -i 's|<uses-permission android:name="android.permission.INTERNET"/>|&\n    <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>\n    <uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION"/>|' "$MANIFEST"
grep -q 'usesCleartextTraffic' "$MANIFEST" ||
  sed -i 's|android:label="field_route"|android:label="Полевой маршрут"\n        android:usesCleartextTraffic="true"|' "$MANIFEST"

# applicationId
GRADLE=android/app/build.gradle.kts
sed -i 's|applicationId = ".*"|applicationId = "kz.fieldroute.app"|' "$GRADLE"
sed -i 's|namespace = ".*"|namespace = "kz.fieldroute.app"|' "$GRADLE"

flutter pub get
flutter analyze --no-fatal-infos
flutter test

DEFINES=()
[ -n "${INSPECTOR_PASSWORD:-}" ] && DEFINES+=("--dart-define=INSPECTOR_PASSWORD=$INSPECTOR_PASSWORD")
[ -n "${INSPECTOR_USER:-}" ] && DEFINES+=("--dart-define=INSPECTOR_USER=$INSPECTOR_USER")
[ -n "${POVILIKA_URL_REPO:-}" ] && DEFINES+=("--dart-define=POVILIKA_URL_REPO=$POVILIKA_URL_REPO")
# Запасной адрес GeoServer, если автоопределение с GitHub не сработает
if [ -z "${INSPECTOR_SERVER_URL:-}" ] && [ -f /data/povilika/url-publish/published ]; then
  INSPECTOR_SERVER_URL=$(cat /data/povilika/url-publish/published)
fi
[ -n "${INSPECTOR_SERVER_URL:-}" ] && DEFINES+=("--dart-define=INSPECTOR_SERVER_URL=$INSPECTOR_SERVER_URL")
flutter build apk --release --target-platform android-arm64,android-arm "${DEFINES[@]}"

mkdir -p dist
cp build/app/outputs/flutter-apk/app-release.apk dist/field_route.apk
ls -la dist/field_route.apk

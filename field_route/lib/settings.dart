import 'package:shared_preferences/shared_preferences.dart';

const kDefaultUser = String.fromEnvironment('INSPECTOR_USER', defaultValue: 'inspector_app');
const kDefaultPassword = String.fromEnvironment('INSPECTOR_PASSWORD');
const kUrlRepo = String.fromEnvironment('POVILIKA_URL_REPO', defaultValue: 'Talant2004/geoserver');
/// Запасной адрес на случай, если GitHub недоступен с телефона.
const kFallbackServerUrl = String.fromEnvironment('INSPECTOR_SERVER_URL');

class AppSettings {
  const AppSettings({
    required this.serverUrl,
    required this.username,
    required this.password,
    this.autoUrl = true,
  });

  /// https://….trycloudflare.com/geoserver
  final String serverUrl;
  final String username;
  final String password;
  final bool autoUrl;

  /// Тот же хост, путь /fieldnav (один сервер / шлюз).
  String get fieldnavUrl {
    final base = serverUrl.trim().replaceAll(RegExp(r'/+$'), '');
    if (base.endsWith('/geoserver')) {
      return '${base.substring(0, base.length - '/geoserver'.length)}/fieldnav';
    }
    final uri = Uri.tryParse(base);
    if (uri == null) return '$base/fieldnav';
    return uri.replace(path: '/fieldnav', query: '', fragment: '').toString().replaceAll(RegExp(r'/+$'), '');
  }

  bool get isConfigured => autoUrl || serverUrl.isNotEmpty;

  AppSettings copyWith({String? serverUrl}) => AppSettings(
        serverUrl: serverUrl ?? this.serverUrl,
        username: username,
        password: password,
        autoUrl: autoUrl,
      );

  /// Без SharedPreferences — если плагин на телефоне падает.
  factory AppSettings.defaults() => AppSettings(
        serverUrl: kFallbackServerUrl,
        username: kDefaultUser,
        password: kDefaultPassword,
        autoUrl: true,
      );

  static Future<AppSettings> load() async {
    final p = await SharedPreferences.getInstance();
    String orDefault(String key, String def) {
      final v = p.getString(key) ?? '';
      return v.isEmpty ? def : v;
    }

    final saved = p.getString('serverUrl') ?? '';
    return AppSettings(
      serverUrl: saved.isNotEmpty ? saved : kFallbackServerUrl,
      username: orDefault('username', kDefaultUser),
      password: orDefault('password', kDefaultPassword),
      autoUrl: p.getBool('autoUrl') ?? true,
    );
  }

  Future<void> save() async {
    try {
      final p = await SharedPreferences.getInstance();
      await p.setString('serverUrl', serverUrl.trim().replaceAll(RegExp(r'/+$'), ''));
      await p.setString('username', username.trim());
      await p.setString('password', password);
      await p.setBool('autoUrl', autoUrl);
    } catch (_) {
      // Настройки сессии остаются в памяти; на диск на этом устройстве не пишем.
    }
  }
}

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../api.dart';
import '../server_locator.dart';
import '../settings.dart';
import 'map_screen.dart';
import 'settings_screen.dart';

/// Стартовый экран без flutter_map: если здесь «серо» — проблема не в карте.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.settings});

  final AppSettings settings;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  late AppSettings _settings = widget.settings;
  String _log = 'Проверка…';
  bool _busy = true;
  List<InspectorLayer> _layers = [];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _check());
  }

  Future<void> _check() async {
    setState(() {
      _busy = true;
      _log = '1/4 Загрузка настроек…\n';
    });
    final buf = StringBuffer();
      buf.writeln('Версия: 0.1.2 (сборка +3)');

    try {
      if (_settings.autoUrl) {
        buf.writeln('2/4 Адрес с GitHub…');
        setState(() => _log = buf.toString());
        final url = await fetchServerUrl().timeout(const Duration(seconds: 8));
        if (url != null && url != _settings.serverUrl) {
          final s = _settings.copyWith(serverUrl: url);
          await s.save();
          _settings = s;
        }
      }
      buf.writeln('GeoServer: ${_settings.serverUrl.isEmpty ? "(пусто)" : _settings.serverUrl}');
      buf.writeln('FieldNav:  ${_settings.fieldnavUrl}');

      buf.writeln('3/4 Интернет (плитка карты)…');
      setState(() => _log = buf.toString());
      final tile = Uri.parse(
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/8/91/181',
      );
      final tr = await http.get(tile).timeout(const Duration(seconds: 10));
      buf.writeln(tr.statusCode == 200 && tr.bodyBytes.length > 500
          ? 'Плитка карты: OK (${tr.bodyBytes.length} байт)'
          : 'Плитка карты: ошибка ${tr.statusCode}');

      if (_settings.serverUrl.isEmpty) {
        buf.writeln('4/4 Сервер не задан — откройте Настройки');
      } else {
        buf.writeln('4/4 Слои инспектора…');
        setState(() => _log = buf.toString());
        final api = FieldRouteApi(_settings);
        final layers = await api.layers().timeout(const Duration(seconds: 15));
        _layers = layers;
        buf.writeln(layers.isEmpty
            ? 'Слоёв нет (загрузите TIFF на компьютере)'
            : 'Слоёв: ${layers.length}');
        for (final l in layers.take(4)) {
          buf.writeln(' • ${l.title}');
        }
      }
    } catch (e) {
      buf.writeln('ОШИБКА: $e');
    }

    if (!mounted) return;
    setState(() {
      _log = buf.toString();
      _busy = false;
    });
  }

  Future<void> _openSettings() async {
    final s = await Navigator.push<AppSettings>(
      context,
      MaterialPageRoute(builder: (_) => SettingsScreen(settings: _settings)),
    );
    if (s == null) return;
    setState(() => _settings = s);
    await _check();
  }

  void _openMap() {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => MapScreen(settings: _settings)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        title: const Text('Полевой маршрут'),
        backgroundColor: const Color(0xFF1565C0),
        foregroundColor: Colors.white,
        actions: [
          IconButton(icon: const Icon(Icons.settings), onPressed: _openSettings),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(
              padding: const EdgeInsets.all(12),
              color: const Color(0xFFFFF59D),
              child: const Text(
                'Версия 0.1.2 — если видите жёлтый блок, APK правильный.\n'
                'Карта открывается кнопкой ниже (не сразу).',
                style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
              ),
            ),
            const SizedBox(height: 12),
            Expanded(
              child: Container(
                padding: const EdgeInsets.all(12),
                color: const Color(0xFFF5F5F5),
                child: SingleChildScrollView(
                  child: Text(_log, style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
                ),
              ),
            ),
            const SizedBox(height: 12),
            if (_busy) const LinearProgressIndicator(),
            const SizedBox(height: 8),
            OutlinedButton(onPressed: _busy ? null : _check, child: const Text('Проверить снова')),
            const SizedBox(height: 8),
            FilledButton.icon(
              onPressed: _busy ? null : _openMap,
              icon: const Icon(Icons.map),
              label: Text(_layers.isEmpty ? 'Открыть карту' : 'Открыть карту (${_layers.length} слоя)'),
            ),
          ],
        ),
      ),
    );
  }
}

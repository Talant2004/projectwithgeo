import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';

import '../api.dart';
import '../server_locator.dart';
import '../settings.dart';
import 'settings_screen.dart';

/// Центр дельты р. Или.
const _iliCenter = LatLng(45.5, 75.0);

/// Подложка Esri — OSM на телефонах часто не отдаёт плитки (серый экран).
const _basemapUrl =
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}';

class MapScreen extends StatefulWidget {
  const MapScreen({super.key, required this.settings});

  final AppSettings settings;

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  late AppSettings _settings = widget.settings;
  late FieldRouteApi _api = FieldRouteApi(_settings);
  final _map = MapController();

  List<InspectorLayer> _layers = [];
  final Set<String> _enabled = {};
  LatLng? _gps;
  LatLng? _target;
  RouteResult? _route;
  String _status = 'Карта загружается…';
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    // Сразу после кадра — сеть и GPS, карта уже на экране.
    WidgetsBinding.instance.addPostFrameCallback((_) => _bootstrap());
  }

  Future<void> _bootstrap() async {
    setState(() => _status = 'Подключение к серверу…');
    // Не ждём друг друга: карта уже видна с подложкой.
    await Future.wait([
      _resolveUrl().timeout(const Duration(seconds: 8), onTimeout: () {}),
      _locate().timeout(const Duration(seconds: 8), onTimeout: () {}),
    ]);
    if (!mounted) return;
    await _loadLayers();
  }

  Future<void> _resolveUrl() async {
    if (!_settings.autoUrl) return;
    try {
      final url = await fetchServerUrl();
      if (url == null || url == _settings.serverUrl) return;
      final s = _settings.copyWith(serverUrl: url);
      await s.save();
      if (!mounted) return;
      setState(() {
        _settings = s;
        _api = FieldRouteApi(s);
      });
    } catch (_) {}
  }

  Future<void> _loadLayers() async {
    setState(() {
      _busy = true;
      _status = 'Загрузка слоёв…';
    });
    try {
      if (_settings.autoUrl && _settings.serverUrl.isEmpty) {
        await _resolveUrl();
      }
      if (_settings.serverUrl.isEmpty) {
        throw ApiException(
          'Нет адреса сервера. Нажмите ⚙ и впишите адрес вручную, либо проверьте интернет.',
        );
      }
      final list = await _api.layers().timeout(const Duration(seconds: 20));
      if (!mounted) return;
      setState(() {
        _layers = list;
        if (_enabled.isEmpty && list.isNotEmpty) {
          final main = list.where((l) => l.name.contains('incubation')).toList();
          _enabled.add((main.isNotEmpty ? main.first : list.first).name);
        }
        _status = list.isEmpty
            ? 'Слоёв нет. Загрузите TIFF программой на компьютере.'
            : 'Нажмите на карту — маршрут от GPS';
      });
    } catch (e) {
      if (mounted) {
        setState(() => _status = '$e');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _locate() async {
    try {
      var perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.denied || perm == LocationPermission.deniedForever) {
        return;
      }
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.medium,
          timeLimit: Duration(seconds: 10),
        ),
      );
      if (!mounted) return;
      setState(() => _gps = LatLng(pos.latitude, pos.longitude));
    } catch (_) {}
  }

  Future<void> _buildRoute(LatLng target) async {
    final start = _gps;
    if (start == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Нет GPS. Включите геолокацию и нажмите «Где я».')),
      );
      return;
    }
    setState(() {
      _busy = true;
      _target = target;
      _status = 'Строю маршрут…';
      _route = null;
    });
    try {
      final r = await _api.route(start: start, end: target).timeout(const Duration(seconds: 30));
      if (!mounted) return;
      setState(() {
        _route = r;
        _status = '${r.roadKm} км · ${r.durationMin} мин'
            ' (к дороге ${r.offroadStartM.toInt()} м / от дороги ${r.offroadTargetM.toInt()} м)';
      });
      final pts = [...r.road, ...r.offroad.expand((e) => e)];
      if (pts.length >= 2) {
        final bounds = LatLngBounds.fromPoints(pts);
        _map.fitCamera(CameraFit.bounds(bounds: bounds, padding: const EdgeInsets.all(48)));
      }
    } catch (e) {
      if (mounted) {
        setState(() => _status = '$e');
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _openSettings() async {
    final s = await Navigator.push<AppSettings>(
      context,
      MaterialPageRoute(builder: (_) => SettingsScreen(settings: _settings)),
    );
    if (s == null) return;
    setState(() {
      _settings = s;
      _api = FieldRouteApi(s);
    });
    await _loadLayers();
  }

  void _showLayers() {
    showModalBottomSheet(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setModal) => SafeArea(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const ListTile(title: Text('Слои инспектора (не повилика)')),
                if (_layers.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(16),
                    child: Text('Слои не загружены'),
                  ),
                for (final layer in _layers)
                  CheckboxListTile(
                    value: _enabled.contains(layer.name),
                    title: Text(layer.title),
                    subtitle: Text(layer.name, style: const TextStyle(fontSize: 11)),
                    onChanged: (v) {
                      setState(() {
                        if (v == true) {
                          _enabled.add(layer.name);
                        } else {
                          _enabled.remove(layer.name);
                        }
                      });
                      setModal(() {});
                    },
                  ),
                const SizedBox(height: 8),
              ],
            ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        title: const Text('Полевой маршрут'),
        actions: [
          IconButton(icon: const Icon(Icons.layers), onPressed: _showLayers, tooltip: 'Слои'),
          IconButton(icon: const Icon(Icons.settings), onPressed: _openSettings),
        ],
      ),
      body: Column(
        children: [
          Material(
            color: const Color(0xFFE3F2FD),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              child: Row(
                children: [
                  Expanded(child: Text(_status, style: const TextStyle(fontSize: 13))),
                  if (_busy)
                    const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  else
                    TextButton(onPressed: _bootstrap, child: const Text('Повторить')),
                ],
              ),
            ),
          ),
          Expanded(
            child: FlutterMap(
              mapController: _map,
              options: MapOptions(
                initialCenter: _gps ?? _iliCenter,
                initialZoom: 8,
                backgroundColor: const Color(0xFFDCE3EA),
                onTap: (_, latlng) => _buildRoute(latlng),
              ),
              children: [
                TileLayer(
                  urlTemplate: _basemapUrl,
                  userAgentPackageName: 'kz.fieldroute.app',
                  maxNativeZoom: 19,
                  tileProvider: NetworkTileProvider(
                    headers: const {
                      'User-Agent': 'Mozilla/5.0 (Linux; Android 13) FieldRoute/0.1',
                    },
                  ),
                ),
                if (_settings.serverUrl.isNotEmpty)
                  for (final name in _enabled)
                    TileLayer(
                      urlTemplate: _api.tileUrl(name),
                      userAgentPackageName: 'kz.fieldroute.app',
                      tms: true,
                      maxNativeZoom: 18,
                      tileProvider: NetworkTileProvider(
                        headers: const {
                          'User-Agent': 'Mozilla/5.0 (Linux; Android 13) FieldRoute/0.1',
                        },
                      ),
                      tileBuilder: (context, tileWidget, tile) =>
                          Opacity(opacity: 0.8, child: tileWidget),
                    ),
                if (_route != null)
                  PolylineLayer(
                    polylines: [
                      Polyline(points: _route!.road, color: const Color(0xFF1565C0), strokeWidth: 5),
                      for (final off in _route!.offroad)
                        Polyline(points: off, color: const Color(0xFFFF6F00), strokeWidth: 3),
                    ],
                  ),
                MarkerLayer(
                  markers: [
                    if (_gps != null)
                      Marker(
                        point: _gps!,
                        width: 36,
                        height: 36,
                        child: const Icon(Icons.my_location, color: Color(0xFF2E7D32), size: 32),
                      ),
                    if (_target != null)
                      Marker(
                        point: _target!,
                        width: 36,
                        height: 36,
                        child: const Icon(Icons.place, color: Color(0xFFC62828), size: 36),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.small(
        onPressed: () async {
          await _locate();
          if (_gps != null) _map.move(_gps!, 12);
        },
        tooltip: 'Где я',
        child: const Icon(Icons.my_location),
      ),
    );
  }
}

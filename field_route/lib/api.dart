import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:latlong2/latlong.dart';

import 'settings.dart';

const workspace = 'inspector_data';

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

class InspectorLayer {
  InspectorLayer({required this.name, required this.title});
  final String name;
  final String title;
  String get fullName => name.contains(':') ? name : '$workspace:$name';
}

class RouteResult {
  RouteResult({
    required this.roadKm,
    required this.durationMin,
    required this.offroadStartM,
    required this.offroadTargetM,
    required this.road,
    required this.offroad,
  });

  final double roadKm;
  final double durationMin;
  final double offroadStartM;
  final double offroadTargetM;
  final List<LatLng> road;
  final List<List<LatLng>> offroad;
}

class FieldRouteApi {
  FieldRouteApi(this.settings);
  final AppSettings settings;
  static const _timeout = Duration(seconds: 45);

  String get _gs => settings.serverUrl.replaceAll(RegExp(r'/+$'), '');
  String get _fn => settings.fieldnavUrl.replaceAll(RegExp(r'/+$'), '');

  /// TMS тайлы слоя инспектора (не повилика).
  String tileUrl(String layer) {
    final name = layer.contains(':') ? layer : '$workspace:$layer';
    return '$_gs/gwc/service/tms/1.0.0/$name@EPSG:900913@png/{z}/{x}/{y}.png';
  }

  Future<List<InspectorLayer>> layers() async {
    final uri = Uri.parse('$_fn/api/geoserver/layers').replace(queryParameters: {
      'geoserver_url': _gs,
      'workspace': workspace,
    });
    final r = await http.get(uri).timeout(_timeout);
    if (r.statusCode != 200) {
      throw ApiException('Не удалось получить список слоёв (${r.statusCode})');
    }
    final data = jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;
    final list = (data['layers'] as List? ?? []).cast<Map<String, dynamic>>();
    return list
        .map((m) => InspectorLayer(
              name: (m['name'] as String).split(':').last,
              title: (m['title'] as String?) ?? m['name'] as String,
            ))
        .toList();
  }

  Future<RouteResult> route({
    required LatLng start,
    required LatLng end,
  }) async {
    final uri = Uri.parse('$_fn/api/route').replace(queryParameters: {
      'start_lon': '${start.longitude}',
      'start_lat': '${start.latitude}',
      'end_lon': '${end.longitude}',
      'end_lat': '${end.latitude}',
    });
    final r = await http.get(uri).timeout(_timeout);
    final body = utf8.decode(r.bodyBytes);
    if (r.statusCode != 200) {
      try {
        final err = jsonDecode(body) as Map<String, dynamic>;
        throw ApiException(err['detail']?.toString() ?? 'Ошибка маршрута ${r.statusCode}');
      } catch (e) {
        if (e is ApiException) rethrow;
        throw ApiException('Ошибка маршрута ${r.statusCode}');
      }
    }
    final data = jsonDecode(body) as Map<String, dynamic>;
    final metrics = data['metrics'] as Map<String, dynamic>;
    final roadCoords = (data['road_geojson']['geometry']['coordinates'] as List)
        .map((c) => LatLng((c as List)[1] as double, c[0] as double))
        .toList();
    final off = <List<LatLng>>[];
    for (final f in (data['offroad_geojson']['features'] as List)) {
      final coords = (f['geometry']['coordinates'] as List)
          .map((c) => LatLng((c as List)[1] as double, c[0] as double))
          .toList();
      off.add(coords);
    }
    return RouteResult(
      roadKm: (metrics['road_distance_km'] as num).toDouble(),
      durationMin: (metrics['duration_min'] as num).toDouble(),
      offroadStartM: (metrics['offroad_start_m'] as num).toDouble(),
      offroadTargetM: (metrics['offroad_target_m'] as num).toDouble(),
      road: roadCoords,
      offroad: off,
    );
  }
}

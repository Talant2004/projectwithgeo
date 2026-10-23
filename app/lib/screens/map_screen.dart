import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:intl/intl.dart';
import 'package:latlong2/latlong.dart';

import '../geo.dart';
import '../geoserver_api.dart';
import '../models.dart';
import 'report_screen.dart';

const _userAgent = 'kz.povilika.app';

class MapScreen extends StatefulWidget {
  const MapScreen({super.key, required this.api, required this.survey});

  final GeoServerApi api;
  final Survey survey;

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  final _map = MapController();
  final _hit = LayerHitNotifier<Hotspot>(null);
  List<Hotspot> _hotspots = [];
  final List<LatLng> _draft = [];
  bool _drawing = false;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _hit.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
    } on Exception catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _load() => _run(() async {
        final list = await widget.api.hotspots(widget.survey.layerName);
        if (mounted) setState(() => _hotspots = list);
      });

  Future<void> _saveDraft() => _run(() async {
        final points = List<LatLng>.of(_draft);
        final h = Hotspot(
          surveyLayer: widget.survey.layerName,
          points: points,
          areaHa: polygonAreaHa(points),
        );
        await widget.api.insertHotspot(widget.survey, h);
        if (!mounted) return;
        setState(() {
          _draft.clear();
          _drawing = false;
        });
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Очаг сохранён: ${fmtNum(h.areaHa, 3)} га')),
        );
        final list = await widget.api.hotspots(widget.survey.layerName);
        if (mounted) setState(() => _hotspots = list);
      });

  Future<void> _delete(Hotspot h) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(h.isAuto ? 'Удалить найденный очаг?' : 'Удалить очаг?'),
        content: Text('${h.isAuto ? 'Найден автоматически' : 'Оконтурен вручную'}\n'
            'Площадь ${fmtNum(h.areaHa, 3)} га'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Отмена')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Удалить')),
        ],
      ),
    );
    if (ok != true || h.fid == null) return;
    await _run(() async {
      await widget.api.deleteHotspot(h.fid!);
      final list = await widget.api.hotspots(widget.survey.layerName);
      if (mounted) setState(() => _hotspots = list);
    });
  }

  Future<void> _deleteAllAuto() async {
    final auto = _hotspots.where((h) => h.isAuto).toList();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Удалить все найденные очаги?'),
        content: Text('Будет удалено ${auto.length} автоматических очагов '
            '(${fmtNum(auto.fold(0.0, (s, h) => s + h.areaHa), 3)} га). '
            'Оконтуренные вручную останутся.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Отмена')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Удалить')),
        ],
      ),
    );
    if (ok != true) return;
    await _run(() async {
      await widget.api.deleteAutoHotspots(widget.survey.layerName);
      final list = await widget.api.hotspots(widget.survey.layerName);
      if (mounted) setState(() => _hotspots = list);
    });
  }

  void _onPolygonTap() {
    final hit = _hit.value;
    if (hit == null) return;
    if (_drawing) {
      setState(() => _draft.add(hit.coordinate));
    } else if (hit.hitValues.isNotEmpty) {
      _delete(hit.hitValues.first);
    }
  }

  void _showList() {
    final fmt = DateFormat('dd.MM.yyyy HH:mm');
    showModalBottomSheet<void>(
      context: context,
      builder: (ctx) => _hotspots.isEmpty
          ? const SizedBox(height: 120, child: Center(child: Text('Очагов пока нет')))
          : ListView(
              shrinkWrap: true,
              children: [
                ListTile(
                  title: Text('Очагов: ${_hotspots.length}'),
                  subtitle: Text('Общая площадь: ${fmtNum(_totalArea, 3)} га'
                      '${_autoCount > 0 ? '\nНайдено автоматически: $_autoCount (оранжевые)' : ''}'),
                  isThreeLine: _autoCount > 0,
                  trailing: _autoCount > 0
                      ? TextButton(
                          onPressed: () {
                            Navigator.pop(ctx);
                            _deleteAllAuto();
                          },
                          child: const Text('Удалить\nвсе авто', textAlign: TextAlign.center),
                        )
                      : null,
                ),
                const Divider(height: 1),
                for (final (i, h) in _hotspots.indexed)
                  ListTile(
                    leading: CircleAvatar(
                      backgroundColor: h.isAuto ? Colors.orange.shade100 : Colors.red.shade100,
                      child: Text('${i + 1}'),
                    ),
                    title: Text('${fmtNum(h.areaHa, 3)} га'),
                    subtitle: Text([
                      h.isAuto ? 'авто' : 'вручную',
                      if (h.createdAt != null) fmt.format(h.createdAt!.toLocal()),
                    ].join(' · ')),
                    onTap: () {
                      Navigator.pop(ctx);
                      _map.move(centroid(h.points), _map.camera.zoom);
                    },
                    trailing: IconButton(
                      icon: const Icon(Icons.delete_outline),
                      onPressed: () {
                        Navigator.pop(ctx);
                        _delete(h);
                      },
                    ),
                  ),
              ],
            ),
    );
  }

  double get _totalArea => _hotspots.fold(0.0, (s, h) => s + h.areaHa);

  int get _autoCount => _hotspots.where((h) => h.isAuto).length;

  void _openReport() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ReportScreen(api: widget.api, survey: widget.survey, hotspots: _hotspots),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final survey = widget.survey;
    final extent = survey.extent;
    return Scaffold(
      appBar: AppBar(
        title: Text(survey.title, overflow: TextOverflow.ellipsis),
        actions: [
          IconButton(icon: const Icon(Icons.list), tooltip: 'Очаги', onPressed: _showList),
          IconButton(
            icon: const Icon(Icons.assignment),
            tooltip: 'Отчёты (акт / полётное задание)',
            onPressed: _hotspots.isEmpty || _drawing ? null : _openReport,
          ),
        ],
      ),
      body: Stack(
        children: [
          FlutterMap(
            mapController: _map,
            options: MapOptions(
              initialCenter: const LatLng(43.25, 76.95),
              initialZoom: 12,
              initialCameraFit: extent.length >= 2
                  ? CameraFit.bounds(
                      bounds: LatLngBounds.fromPoints(extent),
                      padding: const EdgeInsets.all(24),
                    )
                  : null,
              maxZoom: 24,
              onTap: (_, point) {
                if (_drawing) setState(() => _draft.add(point));
              },
            ),
            children: [
              TileLayer(
                urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                userAgentPackageName: _userAgent,
                maxNativeZoom: 19,
              ),
              TileLayer(
                urlTemplate: widget.api.tileUrlTemplate(survey.layerName),
                tms: true,
                userAgentPackageName: _userAgent,
              ),
              GestureDetector(
                onTap: _onPolygonTap,
                child: PolygonLayer<Hotspot>(
                  hitNotifier: _hit,
                  polygons: [
                    for (final h in _hotspots)
                      Polygon(
                        points: h.points,
                        hitValue: h,
                        color: (h.isAuto ? Colors.orange : Colors.red).withAlpha(90),
                        borderColor: h.isAuto ? Colors.orange.shade900 : Colors.red.shade900,
                        borderStrokeWidth: 2,
                      ),
                  ],
                ),
              ),
              PolygonLayer(
                polygons: [
                  if (_draft.length >= 3)
                    Polygon(
                      points: _draft,
                      color: Colors.amber.withAlpha(80),
                      borderColor: Colors.amber.shade800,
                      borderStrokeWidth: 2,
                    ),
                ],
              ),
              if (_draft.length == 2)
                PolylineLayer(
                  polylines: [Polyline(points: _draft, color: Colors.amber.shade800, strokeWidth: 2)],
                ),
              MarkerLayer(
                markers: [
                  for (final p in _draft)
                    Marker(
                      point: p,
                      width: 14,
                      height: 14,
                      child: Container(
                        decoration: BoxDecoration(
                          color: Colors.amber,
                          shape: BoxShape.circle,
                          border: Border.all(color: Colors.black54),
                        ),
                      ),
                    ),
                ],
              ),
              const RichAttributionWidget(
                attributions: [TextSourceAttribution('© OpenStreetMap contributors')],
              ),
            ],
          ),
          if (_busy) const LinearProgressIndicator(),
          if (_drawing)
            Positioned(
              top: 12,
              left: 12,
              right: 12,
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Text(
                    _draft.length < 3
                        ? 'Нажимайте по карте, чтобы обвести очаг (точек: ${_draft.length})'
                        : 'Точек: ${_draft.length} · площадь ${fmtNum(polygonAreaHa(_draft), 3)} га',
                  ),
                ),
              ),
            ),
        ],
      ),
      floatingActionButton: _drawing ? _drawingButtons() : FloatingActionButton.extended(
        onPressed: () => setState(() => _drawing = true),
        icon: const Icon(Icons.draw),
        label: const Text('Оконтурить очаг'),
      ),
    );
  }

  Widget _drawingButtons() {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        FloatingActionButton.small(
          heroTag: 'undo',
          tooltip: 'Отменить точку',
          onPressed: _draft.isEmpty ? null : () => setState(() => _draft.removeLast()),
          child: const Icon(Icons.undo),
        ),
        const SizedBox(height: 8),
        FloatingActionButton.small(
          heroTag: 'cancel',
          tooltip: 'Отмена',
          onPressed: () => setState(() {
            _draft.clear();
            _drawing = false;
          }),
          child: const Icon(Icons.close),
        ),
        const SizedBox(height: 8),
        FloatingActionButton.extended(
          heroTag: 'save',
          onPressed: _draft.length >= 3 && !_busy ? _saveDraft : null,
          icon: const Icon(Icons.check),
          label: const Text('Сохранить'),
        ),
      ],
    );
  }
}

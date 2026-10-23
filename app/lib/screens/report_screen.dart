import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:printing/printing.dart';
import 'package:share_plus/share_plus.dart';

import '../geo.dart';
import '../geoserver_api.dart';
import '../models.dart';
import '../report/flight_task.dart';
import '../report/phyto_act.dart';

class ReportScreen extends StatefulWidget {
  const ReportScreen({
    super.key,
    required this.api,
    required this.survey,
    required this.hotspots,
  });

  final GeoServerApi api;
  final Survey survey;
  final List<Hotspot> hotspots;

  @override
  State<ReportScreen> createState() => _ReportScreenState();
}

class _ReportScreenState extends State<ReportScreen> {
  final _solutionRate = TextEditingController(text: '15');
  final _productRate = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _solutionRate.dispose();
    _productRate.dispose();
    super.dispose();
  }

  double? _parse(TextEditingController c) =>
      double.tryParse(c.text.trim().replaceAll(',', '.'));

  FlightTask? get _task {
    final rate = _parse(_solutionRate);
    if (rate == null || rate <= 0) return null;
    final product = _parse(_productRate);
    return FlightTask(
      survey: widget.survey,
      hotspots: widget.hotspots,
      solutionRate: rate,
      productRate: product != null && product > 0 ? product : null,
      organization: widget.api.settings.organization,
    );
  }

  PhytoAct get _act => PhytoAct(
        survey: widget.survey,
        hotspots: widget.hotspots,
        organization: widget.api.settings.organization,
      );

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
    } on Exception catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Ошибка: $e')));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _withTask(Future<void> Function(FlightTask t) action) async {
    final t = _task;
    if (t == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Укажите норму расхода рабочего раствора')),
      );
      return;
    }
    await _run(() => action(t));
  }

  Future<void> _previewFlightPdf() => _withTask((t) async {
        final bytes = await buildPdf(t, widget.api);
        await Printing.layoutPdf(onLayout: (_) async => bytes, name: '${t.fileBaseName}.pdf');
      });

  Future<void> _shareFlight() => _withTask((t) async {
        final dir = await getTemporaryDirectory();
        final pdf = File('${dir.path}/${t.fileBaseName}.pdf');
        final kml = File('${dir.path}/${t.fileBaseName}.kml');
        await pdf.writeAsBytes(await buildPdf(t, widget.api));
        await kml.writeAsBytes(utf8.encode(buildKml(t)));
        await Share.shareXFiles(
          [
            XFile(pdf.path, mimeType: 'application/pdf'),
            XFile(kml.path, mimeType: 'application/vnd.google-earth.kml+xml'),
          ],
          subject: 'Полётное задание — ${t.survey.farm} / ${t.survey.field}',
        );
      });

  Future<void> _previewActPdf() => _run(() async {
        final bytes = await buildPhytoActPdf(_act, widget.api);
        await Printing.layoutPdf(onLayout: (_) async => bytes, name: '${_act.fileBaseName}.pdf');
      });

  Future<void> _shareAct() => _run(() async {
        final act = _act;
        final dir = await getTemporaryDirectory();
        final pdf = File('${dir.path}/${act.fileBaseName}.pdf');
        await pdf.writeAsBytes(await buildPhytoActPdf(act, widget.api));
        await Share.shareXFiles(
          [XFile(pdf.path, mimeType: 'application/pdf')],
          subject: 'Акт фитосанитарного обследования — ${act.survey.farm} / ${act.survey.field}',
        );
      });

  @override
  Widget build(BuildContext context) {
    final t = _task;
    final area = widget.hotspots.fold(0.0, (s, h) => s + h.areaHa);
    final text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Отчёты')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('${widget.survey.farm} / ${widget.survey.field}', style: text.titleMedium),
          const SizedBox(height: 4),
          const Text('Вредный организм: $pestName', style: TextStyle(fontStyle: FontStyle.italic)),
          Text('Очагов: ${widget.hotspots.length} · площадь ${fmtNum(area, 3)} га '
              '(${fmtNum(area * 10000, 0)} м²)'),
          if (_busy) ...[
            const SizedBox(height: 12),
            const LinearProgressIndicator(),
          ],

          const SizedBox(height: 24),
          Text('Акт фитосанитарного обследования', style: text.titleMedium),
          const SizedBox(height: 6),
          Text(
            'Шапка с датой и координатами поля, мини-карта с контурами, '
            'таблица очагов и сумма площади заражения.',
            style: text.bodySmall,
          ),
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed: _busy ? null : _previewActPdf,
            icon: const Icon(Icons.description),
            label: const Text('Просмотр / печать акта (PDF)'),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: _busy ? null : _shareAct,
            icon: const Icon(Icons.share),
            label: const Text('Отправить акт PDF'),
          ),

          const SizedBox(height: 28),
          const Divider(),
          const SizedBox(height: 8),
          Text('Полётное задание БПЛА', style: text.titleMedium),
          const SizedBox(height: 12),
          TextField(
            controller: _solutionRate,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(
              labelText: 'Норма расхода рабочего раствора',
              suffixText: 'л/га',
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _productRate,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(
              labelText: 'Норма расхода препарата (необязательно)',
              suffixText: 'л/га',
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 16),
          Card(
            color: Theme.of(context).colorScheme.primaryContainer,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Объём рабочего раствора', style: text.labelLarge),
                  Text(t == null ? '—' : '${fmtNum(t.solutionVolume, 1)} л', style: text.headlineMedium),
                  if (t?.productVolume != null) ...[
                    const SizedBox(height: 8),
                    Text('Объём препарата', style: text.labelLarge),
                    Text('${fmtNum(t!.productVolume!, 2)} л', style: text.titleLarge),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          FilledButton.tonalIcon(
            onPressed: _busy ? null : _previewFlightPdf,
            icon: const Icon(Icons.picture_as_pdf),
            label: const Text('Просмотр / печать задания PDF'),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: _busy ? null : _shareFlight,
            icon: const Icon(Icons.share),
            label: const Text('Отправить задание PDF + KML'),
          ),
        ],
      ),
    );
  }
}

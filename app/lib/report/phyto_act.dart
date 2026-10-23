import 'dart:typed_data';

import 'package:flutter/services.dart' show rootBundle;
import 'package:intl/intl.dart';
import 'package:latlong2/latlong.dart';
import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;

import '../geo.dart';
import '../geoserver_api.dart';
import '../models.dart';

const _green = PdfColor.fromInt(0xFF2E7D32);
const _muted = PdfColor.fromInt(0xFF7F8C8D);
const _mapWidthPx = 1200;
const _mapHeightPx = 720;

/// Акт фитосанитарного обследования по выбранным очагам повилики.
class PhytoAct {
  PhytoAct({
    required this.survey,
    required this.hotspots,
    this.organization = '',
    DateTime? createdAt,
  }) : createdAt = createdAt ?? DateTime.now();

  final Survey survey;
  final List<Hotspot> hotspots;
  final String organization;
  final DateTime createdAt;

  double get totalAreaHa => hotspots.fold(0.0, (s, h) => s + h.areaHa);
  double get totalAreaM2 => totalAreaHa * 10000;

  String get fileBaseName =>
      'akt_${survey.layerName}_${DateFormat('yyyyMMdd_HHmm').format(createdAt)}';
}

Future<Uint8List> buildPhytoActPdf(PhytoAct t, GeoServerApi api) async {
  Future<pw.Font> font(String name) async =>
      pw.Font.ttf(await rootBundle.load('assets/fonts/Roboto-$name.ttf'));
  final theme = pw.ThemeData.withFont(
    base: await font('Regular'),
    bold: await font('Bold'),
    italic: await font('Italic'),
  );

  final allPoints = t.hotspots.expand((h) => h.points);
  final box = MercatorBox.around(allPoints, aspect: _mapHeightPx / _mapWidthPx);
  final image = await api.mapImage(t.survey.layerName, box, _mapWidthPx, _mapHeightPx);

  final dateFmt = DateFormat('dd.MM.yyyy');
  final fieldCenter = centroid(allPoints.toList());
  final doc = pw.Document(title: 'Акт фитосанитарного обследования', creator: 'Повилика');

  doc.addPage(pw.MultiPage(
    pageFormat: PdfPageFormat.a4,
    margin: const pw.EdgeInsets.all(36),
    theme: theme,
    footer: (ctx) => pw.Container(
      padding: const pw.EdgeInsets.only(top: 8),
      decoration: const pw.BoxDecoration(
        border: pw.Border(top: pw.BorderSide(color: PdfColors.grey300)),
      ),
      child: pw.Row(
        mainAxisAlignment: pw.MainAxisAlignment.spaceBetween,
        children: [
          pw.Text('Сформировано в приложении «Повилика»',
              style: const pw.TextStyle(fontSize: 9, color: _muted)),
          pw.Text('Стр. ${ctx.pageNumber} из ${ctx.pagesCount}',
              style: const pw.TextStyle(fontSize: 9, color: _muted)),
        ],
      ),
    ),
    build: (ctx) => [
      pw.Container(
        padding: const pw.EdgeInsets.only(bottom: 12),
        decoration: const pw.BoxDecoration(
          border: pw.Border(bottom: pw.BorderSide(color: _green, width: 2)),
        ),
        child: pw.Column(crossAxisAlignment: pw.CrossAxisAlignment.start, children: [
          pw.Text('Акт фитосанитарного обследования',
              style: const pw.TextStyle(fontSize: 18, fontWeight: pw.FontWeight.bold)),
          if (t.organization.isNotEmpty)
            pw.Text(t.organization, style: const pw.TextStyle(fontSize: 10, color: _muted)),
        ]),
      ),
      pw.SizedBox(height: 14),
      _infoTable(t, dateFmt, fieldCenter),
      pw.SizedBox(height: 16),
      pw.Text('Мини-карта участка с контурами очагов',
          style: const pw.TextStyle(fontSize: 12, fontWeight: pw.FontWeight.bold)),
      pw.SizedBox(height: 8),
      _map(t, box, image),
      pw.SizedBox(height: 16),
      _table(t),
      pw.SizedBox(height: 12),
      pw.Container(
        padding: const pw.EdgeInsets.all(10),
        color: const PdfColor.fromInt(0xFFE8F5E9),
        child: pw.Text(
          'Общая площадь заражения: ${fmtNum(t.totalAreaM2, 1)} м² '
          '(${fmtNum(t.totalAreaHa, 4)} га), очагов: ${t.hotspots.length}',
          style: const pw.TextStyle(fontSize: 12, fontWeight: pw.FontWeight.bold),
        ),
      ),
      pw.SizedBox(height: 36),
      pw.Text('Ответственное лицо: ______________________ / ______________________'),
    ],
  ));
  return doc.save();
}

pw.Widget _infoTable(PhytoAct t, DateFormat dateFmt, LatLng fieldCenter) {
  pw.TableRow row(String label, String value) => pw.TableRow(children: [
        pw.Padding(
          padding: const pw.EdgeInsets.symmetric(vertical: 4, horizontal: 4),
          child: pw.Text(label, style: const pw.TextStyle(color: _muted, fontSize: 10)),
        ),
        pw.Padding(
          padding: const pw.EdgeInsets.symmetric(vertical: 4, horizontal: 4),
          child: pw.Text(value, style: const pw.TextStyle(fontWeight: pw.FontWeight.bold, fontSize: 10)),
        ),
      ]);

  return pw.Table(
    columnWidths: const {0: pw.FlexColumnWidth(1.1), 1: pw.FlexColumnWidth(1.6)},
    children: [
      row('Дата формирования', DateFormat('dd.MM.yyyy HH:mm').format(t.createdAt)),
      row('Хозяйство / поле', '${t.survey.farm} / ${t.survey.field}'),
      row('Дата съёмки', dateFmt.format(t.survey.date)),
      row(
        'Координаты участка (WGS-84)',
        '${fieldCenter.latitude.toStringAsFixed(6)}°, ${fieldCenter.longitude.toStringAsFixed(6)}°',
      ),
      row('Вредитель', '$pestName (повилика полевая)'),
    ],
  );
}

pw.Widget _map(PhytoAct t, MercatorBox box, Uint8List? image) {
  const width = 523.0;
  const height = width * _mapHeightPx / _mapWidthPx;
  return pw.Container(
    decoration: pw.BoxDecoration(border: pw.Border.all(color: PdfColors.grey400)),
    child: pw.CustomPaint(
      size: const PdfPoint(width, height),
      foregroundPainter: (canvas, size) {
        for (final h in t.hotspots) {
          final pts = h.points.map((p) {
            final m = toMercator(p);
            return PdfPoint(
              (m.x - box.minX) / box.width * size.x,
              (m.y - box.minY) / box.height * size.y,
            );
          }).toList();
          void path() {
            canvas.moveTo(pts.first.x, pts.first.y);
            for (final p in pts.skip(1)) {
              canvas.lineTo(p.x, p.y);
            }
            canvas.closePath();
          }

          canvas
            ..saveContext()
            ..setGraphicState(const PdfGraphicState(opacity: 0.35))
            ..setFillColor(PdfColors.red);
          path();
          canvas
            ..fillPath()
            ..restoreContext()
            ..setStrokeColor(PdfColors.red900)
            ..setLineWidth(1.2);
          path();
          canvas.strokePath();
        }
      },
      child: image != null
          ? pw.Image(pw.MemoryImage(image), width: width, height: height, fit: pw.BoxFit.fill)
          : pw.Container(
              width: width,
              height: height,
              color: PdfColors.grey100,
              alignment: pw.Alignment.bottomLeft,
              padding: const pw.EdgeInsets.all(6),
              child: pw.Text('Подложка недоступна — показаны только контуры',
                  style: const pw.TextStyle(fontSize: 8, color: _muted)),
            ),
    ),
  );
}

pw.Widget _table(PhytoAct t) {
  return pw.TableHelper.fromTextArray(
    headers: ['№', 'Долгота', 'Широта', 'Площадь, м²'],
    data: [
      for (final (i, h) in t.hotspots.indexed)
        () {
          final c = centroid(h.points);
          return [
            '${i + 1}',
            c.longitude.toStringAsFixed(6),
            c.latitude.toStringAsFixed(6),
            fmtNum(h.areaHa * 10000, 1),
          ];
        }(),
    ],
    headerStyle: const pw.TextStyle(fontWeight: pw.FontWeight.bold, fontSize: 9, color: PdfColors.white),
    headerDecoration: const pw.BoxDecoration(color: _green),
    cellStyle: const pw.TextStyle(fontSize: 9),
    cellAlignments: {
      0: pw.Alignment.center,
      1: pw.Alignment.center,
      2: pw.Alignment.center,
      3: pw.Alignment.centerRight,
    },
  );
}

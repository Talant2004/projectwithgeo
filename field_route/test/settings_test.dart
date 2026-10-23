import 'package:flutter_test/flutter_test.dart';
import 'package:field_route/settings.dart';
import 'package:field_route/server_locator.dart';

void main() {
  test('fieldnavUrl из адреса GeoServer', () {
    const s = AppSettings(
      serverUrl: 'https://a-b-c-d.trycloudflare.com/geoserver',
      username: 'inspector_app',
      password: 'x',
    );
    expect(s.fieldnavUrl, 'https://a-b-c-d.trycloudflare.com/fieldnav');
  });

  test('parseServerUrl', () {
    expect(
      parseServerUrl('{"url":"https://x.trycloudflare.com/geoserver/"}'),
      'https://x.trycloudflare.com/geoserver',
    );
  });
}

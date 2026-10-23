import 'dart:convert';

import 'package:http/http.dart' as http;

import 'settings.dart';

const _branch = 'server-url';
const _file = 'server_url.json';

Future<String?> fetchServerUrl({http.Client? client}) async {
  final c = client ?? http.Client();
  final t = DateTime.now().millisecondsSinceEpoch;
  final sources = [
    (
      Uri.parse('https://api.github.com/repos/$kUrlRepo/contents/$_file?ref=$_branch&t=$t'),
      {'Accept': 'application/vnd.github.raw'},
    ),
    (Uri.parse('https://raw.githubusercontent.com/$kUrlRepo/$_branch/$_file?t=$t'), <String, String>{}),
  ];
  try {
    for (final (uri, headers) in sources) {
      try {
        final r = await c.get(uri, headers: headers).timeout(const Duration(seconds: 10));
        if (r.statusCode != 200) continue;
        final url = parseServerUrl(r.body);
        if (url != null) return url;
      } catch (_) {
        continue;
      }
    }
    return null;
  } finally {
    if (client == null) c.close();
  }
}

String? parseServerUrl(String body) {
  try {
    final url = (jsonDecode(body) as Map<String, dynamic>)['url'];
    if (url is! String) return null;
    final uri = Uri.tryParse(url.trim());
    if (uri == null || uri.scheme != 'https' || uri.host.isEmpty) return null;
    return url.trim().replaceAll(RegExp(r'/+$'), '');
  } catch (_) {
    return null;
  }
}
